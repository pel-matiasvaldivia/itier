"""Adaptador de GLPI (núcleo ITSM).

Habla con la API REST de GLPI (apirest.php): abre sesión, crea entidades (una por
cliente) y opera tickets. En modo demo devuelve fixtures.

⚠ VERIFICAR CONTRA GLPI 11 (reciente, 2025-10):
  - La ruta de la API (ITIER_GLPI_APIREST_PATH, por defecto /apirest.php) y el
    esquema de autenticación (App-Token + user_token) vienen del API REST clásico
    de GLPI 9/10. GLPI 11 podría exponer una API nueva o cambiar nombres de campos.
  - Confirmá: initSession, el header de App-Token, y los campos de Entity/Ticket.
  - Todo el acoplamiento con GLPI vive en ESTE archivo: si algo difiere, es un
    cambio localizado (ADR-0001/0003).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from . import AdapterError

# ── Fixtures de demo ────────────────────────────────────────────────────────

_DEMO_ENTIDADES = [
    {"id": 0, "name": "Raíz (entidad raíz)", "completename": "Raíz"},
    {"id": 2, "name": "Ferretería San Martín", "completename": "Raíz > Ferretería San Martín"},
    {"id": 3, "name": "Estudio Contable Paz", "completename": "Raíz > Estudio Contable Paz"},
]

_DEMO_TICKETS = [
    {"id": 1042, "name": "No anda el correo en recepción", "status": 2, "priority": 4,
     "entities_id": 2, "date": "2026-11-11 09:14", "users_id_recipient": "mesa"},
    {"id": 1041, "name": "Pedido: alta de usuario para pasante", "status": 1, "priority": 3,
     "entities_id": 3, "date": "2026-11-11 08:40", "users_id_recipient": "mesa"},
    {"id": 1038, "name": "Impresora de administración sin tóner", "status": 4, "priority": 2,
     "entities_id": 2, "date": "2026-11-10 17:02", "users_id_recipient": "mesa"},
]

# Tickets creados en modo demo (memoria del proceso), para que la demo sea
# coherente entre requests sin tocar GLPI real.
_DEMO_TICKETS_EXTRA: dict[int, dict] = {}

# Técnico asignado por ticket (demo).
_DEMO_TECNICOS: dict[int, str] = {1042: "María Técnica", 1041: "", 1038: "Juan Soporte"}

# status GLPI: 1 nuevo, 2 en curso (asignado), 3 en curso (planificado),
#              4 en espera, 5 resuelto, 6 cerrado.
ESTADOS_TICKET = {
    1: "Nuevo", 2: "En curso", 3: "Planificado", 4: "En espera", 5: "Resuelto", 6: "Cerrado",
}
PRIORIDADES = {1: "Muy baja", 2: "Baja", 3: "Media", 4: "Alta", 5: "Muy alta", 6: "Mayor"}


class GLPIClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.demo = cfg.demo or not (cfg.glpi_url and cfg.glpi_app_token)
        self._base = f"{cfg.glpi_url}{cfg.glpi_apirest_path}" if cfg.glpi_url else ""
        self._session_token: str | None = None
        self._http: httpx.Client | None = None
        self._users_cache: dict[int, str] = {}

    def __enter__(self) -> "GLPIClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._session_token and self._http is not None:
            try:
                self._http.get(
                    f"{self._base}/killSession", headers=self._headers_sesion()
                )
            except httpx.HTTPError:
                pass
        if self._http is not None:
            self._http.close()
            self._http = None
        self._session_token = None

    # -- sesión --------------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self.cfg.http_timeout_s)
        return self._http

    def _headers_sesion(self) -> dict[str, str]:
        return {
            "Session-Token": self._session_token or "",
            "App-Token": self.cfg.glpi_app_token or "",
            "Content-Type": "application/json",
        }

    def _asegurar_sesion(self) -> None:
        if self._session_token:
            return
        headers = {"App-Token": self.cfg.glpi_app_token or ""}
        if self.cfg.glpi_user_token:
            headers["Authorization"] = f"user_token {self.cfg.glpi_user_token}"
        try:
            r = self._client().get(f"{self._base}/initSession", headers=headers)
        except httpx.HTTPError as e:
            raise AdapterError("GLPI", f"no se pudo conectar a initSession: {e}") from e
        if r.status_code != 200:
            raise AdapterError("GLPI", f"initSession devolvió {r.status_code}: {r.text[:200]}")
        self._session_token = r.json().get("session_token")
        if not self._session_token:
            raise AdapterError("GLPI", "initSession no devolvió session_token")

    def _get(self, ruta: str, params: dict | None = None) -> Any:
        self._asegurar_sesion()
        r = self._client().get(f"{self._base}{ruta}", headers=self._headers_sesion(), params=params)
        if r.status_code not in (200, 206):
            raise AdapterError("GLPI", f"GET {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, ruta: str, cuerpo: dict) -> Any:
        self._asegurar_sesion()
        r = self._client().post(f"{self._base}{ruta}", headers=self._headers_sesion(), json=cuerpo)
        if r.status_code not in (200, 201):
            raise AdapterError("GLPI", f"POST {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json()

    def _put(self, ruta: str, cuerpo: dict) -> Any:
        self._asegurar_sesion()
        r = self._client().put(f"{self._base}{ruta}", headers=self._headers_sesion(), json=cuerpo)
        if r.status_code not in (200, 201):
            raise AdapterError("GLPI", f"PUT {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json()

    # -- salud ---------------------------------------------------------------

    def health(self) -> dict:
        if self.demo:
            return {"ok": True, "detalle": "demo", "version": "11.0 (demo)"}
        try:
            self._asegurar_sesion()
            return {"ok": True, "detalle": "sesión iniciada"}
        except AdapterError as e:
            return {"ok": False, "detalle": e.detalle}

    # -- entidades (una por cliente) ----------------------------------------

    def listar_entidades(self) -> list[dict]:
        if self.demo:
            return list(_DEMO_ENTIDADES)
        datos = self._get("/Entity", params={"range": "0-500", "expand_dropdowns": "true"})
        return datos if isinstance(datos, list) else []

    def crear_entidad(self, nombre: str, parent_id: int = 0) -> int:
        if self.demo:
            return 900 + (abs(hash(nombre)) % 99)
        # ⚠ GLPI 11: confirmar el nombre del campo padre (entities_id).
        resp = self._post("/Entity", {"input": {"name": nombre, "entities_id": parent_id}})
        if isinstance(resp, list):
            resp = resp[0] if resp else {}
        nuevo = resp.get("id")
        if nuevo is None:
            raise AdapterError("GLPI", f"crear_entidad sin id en respuesta: {resp}")
        return int(nuevo)

    # -- tickets -------------------------------------------------------------

    def listar_tickets(self, entity_id: int | None = None, limit: int = 100) -> list[dict]:
        if self.demo:
            t = list(_DEMO_TICKETS_EXTRA.values()) + list(_DEMO_TICKETS)
            if entity_id is not None:
                t = [x for x in t if x["entities_id"] == entity_id]
            return t
        # GLPI 11 (motor api.php/v1) valida `sort` como NOMBRE de campo, no como
        # el ID de opción de búsqueda de GLPI 10, y rechaza varios parámetros del
        # estilo viejo. Para ser robustos entre versiones pedimos crudo y
        # ordenamos/filtramos del lado de la consola (los volúmenes son chicos).
        params = {"range": f"0-{limit - 1}"}
        try:
            datos = self._get("/Ticket", params=params)
        except AdapterError as e:
            # Install nuevo sin tickets: GLPI responde 400 ERROR_RANGE_EXCEED_TOTAL
            # (el rango supera el total) → lista vacía.
            if "RANGE_EXCEED_TOTAL" in str(e):
                return []
            raise
        if not isinstance(datos, list):
            return []
        if entity_id is not None:
            datos = [t for t in datos if str(t.get("entities_id")) == str(entity_id)]
        datos.sort(key=lambda t: t.get("id", 0) or 0, reverse=True)
        return datos

    def tecnico_de_ticket(self, ticket_id: int) -> str:
        """Técnico(s) asignado(s) a un ticket.

        El asignado no es un campo del ticket: es un actor (Ticket_User con
        type=2). Se leen los actores y se devuelven los nombres de los de tipo 2.
        """
        if self.demo:
            return _DEMO_TECNICOS.get(ticket_id, "")
        try:
            recs = self._get(f"/Ticket/{ticket_id}/Ticket_User", params={"expand_dropdowns": "true"})
        except AdapterError:
            return ""
        if not isinstance(recs, list):
            return ""
        nombres: list[str] = []
        for r in recs:
            if str(r.get("type")) != "2":  # 1=solicitante, 2=asignado, 3=observador
                continue
            u = r.get("users_id")
            if u in (None, "", 0, "0"):
                continue
            if isinstance(u, str) and not u.isdigit():
                nombres.append(u)  # expand_dropdowns ya trajo el nombre
            else:
                nombres.append(self._nombre_usuario(int(u)))
        return ", ".join(n for n in nombres if n)

    def _nombre_usuario(self, uid: int) -> str:
        if uid in self._users_cache:
            return self._users_cache[uid]
        nombre = str(uid)
        try:
            u = self._get(f"/User/{uid}")
            full = " ".join(p for p in [u.get("realname") or "", u.get("firstname") or ""] if p).strip()
            nombre = full or u.get("name") or str(uid)
        except AdapterError:
            pass
        self._users_cache[uid] = nombre
        return nombre

    def ver_ticket(self, ticket_id: int) -> dict:
        if self.demo:
            if ticket_id in _DEMO_TICKETS_EXTRA:
                return dict(_DEMO_TICKETS_EXTRA[ticket_id])
            for t in _DEMO_TICKETS:
                if t["id"] == ticket_id:
                    return {**t, "content": "Detalle de ejemplo (modo demo)."}
            raise AdapterError("GLPI", f"ticket {ticket_id} no encontrado (demo)")
        return self._get(f"/Ticket/{ticket_id}")

    def crear_ticket(self, titulo: str, contenido: str, entity_id: int, priority: int = 3) -> int:
        if self.demo:
            tid = 2001 + len(_DEMO_TICKETS_EXTRA)
            _DEMO_TICKETS_EXTRA[tid] = {
                "id": tid, "name": titulo, "status": 1, "priority": priority,
                "entities_id": entity_id, "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "content": contenido or "(sin descripción)",
            }
            return tid
        resp = self._post(
            "/Ticket",
            {"input": {"name": titulo, "content": contenido, "entities_id": entity_id, "priority": priority}},
        )
        if isinstance(resp, list):
            resp = resp[0] if resp else {}
        nuevo = resp.get("id")
        if nuevo is None:
            raise AdapterError("GLPI", f"crear_ticket sin id en respuesta: {resp}")
        return int(nuevo)

    def asignar_ticket(self, ticket_id: int, users_id: int) -> None:
        if self.demo:
            return
        # Asignación = actor de tipo 2 (asignado) vía Ticket_User.
        # ⚠ GLPI 11: confirmar el recurso Ticket_User y el campo type.
        self._post(
            "/Ticket_User",
            {"input": {"tickets_id": ticket_id, "users_id": users_id, "type": 2}},
        )


def cliente(cfg) -> GLPIClient:
    return GLPIClient(cfg)
