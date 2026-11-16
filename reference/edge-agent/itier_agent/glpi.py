"""Adaptador de GLPI para el edge: abre/actualiza tickets y correlaciona el host
de Zabbix con el CI de GLPI. Es lo que cierra el lazo evento → ticket.

Usa el API REST clásico (apirest.php): App-Token + user_token → Session-Token,
igual que el adaptador de la consola. Solo lo que el bucle necesita: abrir un
ticket por incidente, anotarlo y vincular el activo afectado.

⚠ VERIFICAR CONTRA GLPI 11 (todo el acoplamiento vive acá):
  - Ticket:       POST /Ticket {input:{name,content,entities_id,urgency}}
  - Seguimiento:  POST /ITILFollowup {input:{itemtype:"Ticket",items_id,content}}
  - Vínculo CI:   POST /Item_Ticket {input:{tickets_id,itemtype,items_id}}
                  (itemtype = clase PHP exacta en inglés, p. ej. "Computer")
  - Correlación:  GET /search/Computer, criteria por nombre (field 1), id (field 2)
"""

from __future__ import annotations

from typing import Any

import httpx

from .events import Evento

# severidad de iTier → urgency de GLPI (1 muy baja … 5 muy alta).
_URGENCIA = {
    "critica": 5, "disaster": 5, "alta": 4, "media": 3,
    "advertencia": 2, "informacion": 1, "no_clasificada": 2,
}


class GLPIError(RuntimeError):
    """Falla al hablar con GLPI. La atrapan abrir_desde_evento/anotar (no fatal)."""


class GLPITickets:
    """Puerto de tickets contra GLPI real. Se inyecta en el Runner."""

    def __init__(self, *, url: str | None, app_token: str | None, user_token: str | None,
                 entity_id: int = 0, timeout: float = 15.0, demo: bool = False) -> None:
        self.demo = demo or not (url and app_token and user_token)
        self._base = f"{url.rstrip('/')}/apirest.php" if url else ""
        self._app = app_token or ""
        self._user = user_token or ""
        self.entity_id = entity_id
        self._timeout = timeout
        self._session: str | None = None
        self._http: httpx.Client | None = None
        self._demo_seq = 3400

    def __enter__(self) -> "GLPITickets":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._session and self._http is not None:
            try:
                self._http.get(f"{self._base}/killSession", headers=self._headers())
            except httpx.HTTPError:
                pass
        if self._http is not None:
            self._http.close()
            self._http = None
        self._session = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _headers(self) -> dict[str, str]:
        return {"Session-Token": self._session or "", "App-Token": self._app,
                "Content-Type": "application/json"}

    def _asegurar_sesion(self) -> None:
        if self._session:
            return
        headers = {"App-Token": self._app, "Authorization": f"user_token {self._user}"}
        try:
            r = self._client().get(f"{self._base}/initSession", headers=headers)
        except httpx.HTTPError as e:
            raise GLPIError(f"no se pudo conectar a initSession: {e}") from e
        if r.status_code != 200:
            raise GLPIError(f"initSession → {r.status_code}: {r.text[:200]}")
        self._session = r.json().get("session_token")
        if not self._session:
            raise GLPIError("initSession no devolvió session_token")

    def _post(self, ruta: str, cuerpo: dict) -> Any:
        self._asegurar_sesion()
        r = self._client().post(f"{self._base}{ruta}", headers=self._headers(), json=cuerpo)
        if r.status_code not in (200, 201):
            raise GLPIError(f"POST {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json()

    def _get(self, ruta: str, params: dict | None = None) -> Any:
        self._asegurar_sesion()
        r = self._client().get(f"{self._base}{ruta}", headers=self._headers(), params=params)
        if r.status_code not in (200, 206):
            raise GLPIError(f"GET {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json()

    @staticmethod
    def _id_de(resp: Any) -> int | None:
        if isinstance(resp, list):
            resp = resp[0] if resp else {}
        return resp.get("id") if isinstance(resp, dict) else None

    # -- operaciones -------------------------------------------------------

    def ci_por_host(self, host: str) -> dict | None:
        """Correlación host→CI: busca un Computer por nombre. Devuelve
        {itemtype, items_id, nombre} o None. Best-effort (no fatal)."""
        if self.demo:
            return {"itemtype": "Computer", "items_id": "42", "nombre": host} if host else None
        if not host:
            return None
        try:
            res = self._get("/search/Computer", params={
                "criteria[0][field]": "1", "criteria[0][searchtype]": "contains",
                "criteria[0][value]": host, "forcedisplay[0]": "2", "range": "0-0",
            })
        except GLPIError:
            return None
        datos = res.get("data") if isinstance(res, dict) else None
        if not datos:
            return None
        return {"itemtype": "Computer", "items_id": str(datos[0].get("2")), "nombre": host}

    def abrir_ticket(self, titulo: str, contenido: str, *, urgencia: int = 3) -> str:
        if self.demo:
            self._demo_seq += 1
            return str(self._demo_seq)
        inp = {"name": titulo[:250], "content": contenido,
               "entities_id": self.entity_id, "urgency": urgencia}
        tid = self._id_de(self._post("/Ticket", {"input": inp}))
        if tid is None:
            raise GLPIError("abrir_ticket: sin id en la respuesta")
        return str(tid)

    def agregar_seguimiento(self, ticket_id: str, contenido: str) -> None:
        if self.demo:
            return
        self._post("/ITILFollowup", {"input": {
            "itemtype": "Ticket", "items_id": int(ticket_id), "content": contenido}})

    def vincular_item(self, ticket_id: str, itemtype: str, items_id: str) -> None:
        if self.demo:
            return
        self._post("/Item_Ticket", {"input": {
            "tickets_id": int(ticket_id), "itemtype": itemtype, "items_id": int(items_id)}})

    # -- puerto que usa el Runner ------------------------------------------

    def abrir_desde_evento(self, ev: Evento) -> str:
        """Abre el ticket del incidente y, si puede, lo vincula al CI afectado."""
        urgencia = _URGENCIA.get(ev.severidad, 3)
        titulo = f"[iTier] {ev.disparador}"
        contenido = (
            "Incidente detectado por el monitoreo (iTier).\n\n"
            f"Origen: {ev.origen} · evento: {ev.id}\n"
            f"Host: {ev.host or '—'} · CI: {ev.ci_id or '—'}\n"
            f"Severidad: {ev.severidad}\n\n"
            f"Disparador: {ev.disparador}"
        )
        tid = self.abrir_ticket(titulo, contenido, urgencia=urgencia)
        if ev.host:
            try:
                ci = self.ci_por_host(ev.host)
                if ci and ci.get("items_id"):
                    self.vincular_item(tid, ci["itemtype"], ci["items_id"])
            except GLPIError:
                pass  # el ticket ya quedó abierto; el vínculo es best-effort
        return tid

    def anotar(self, ticket_id: str, texto: str) -> None:
        try:
            self.agregar_seguimiento(ticket_id, texto)
        except GLPIError:
            pass


class TicketsDemo:
    """Puerto de tickets en memoria, para la demo y los tests."""

    def __init__(self) -> None:
        self._seq = 3400
        self.abiertos: list[tuple[str, str]] = []
        self.notas: list[tuple[str, str]] = []

    def abrir_desde_evento(self, ev: Evento) -> str:
        self._seq += 1
        tid = str(self._seq)
        self.abiertos.append((tid, ev.ci_id))
        return tid

    def anotar(self, ticket_id: str, texto: str) -> None:
        self.notas.append((ticket_id, texto))
