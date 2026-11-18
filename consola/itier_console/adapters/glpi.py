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

# ── Descubrimiento de red (SNMP): itemtypes de GLPI 11 + plugin GlpiInventory ──
# ⚠ Itemtypes y campos confirmados contra el código fuente (core SNMPCredential y
#   el plugin glpi-inventory, nov-2026), NO contra una instancia GLPI 11 viva. Si
#   tu plugin/versión difiere, se ajusta todo acá (adaptador aislado, ADR-0001).
IT_SNMPCRED = "SNMPCredential"                                 # core (Administración → Inventario)
IT_IPRANGE = "PluginGlpiinventoryIPRange"                      # plugin GlpiInventory
IT_IPRANGE_CRED = "PluginGlpiinventoryIPRange_SNMPCredential"  # link rango↔credencial
_SNMP_VER_COD = {"1": 1, "2c": 2, "3": 3}                      # glpi_snmpcredentials.snmpversion
_SNMP_AUTH_COD = {"MD5": 1, "SHA": 2, "SHA224": 3, "SHA256": 4, "SHA384": 5, "SHA512": 6}
_SNMP_PRIV_COD = {"DES": 1, "AES": 2, "3DES": 3, "AES192": 4, "AES256": 5}

# ── Criticidad (curación de CMDB): business criticity de GLPI ──────────────
# ⚠ Itemtype del dropdown confirmado como BusinessCriticity (CommonTreeDropdown);
#   el campo en el ítem es businesscriticities_id. Confirmá contra tu GLPI 11.
#   Es la MISMA criticidad que el agente de borde lee (reference/.../datos.py).
IT_BUSINESSCRIT = "BusinessCriticity"
CRITICIDADES = {"critica": "Crítica", "alta": "Alta", "media": "Media", "baja": "Baja"}

_DEMO_CIS = [
    {"itemtype": "Computer", "id": "501", "name": "SRV-DC01", "entities_id": 2, "criticidad": "Crítica"},
    {"itemtype": "Computer", "id": "502", "name": "SRV-FILE01", "entities_id": 2, "criticidad": "Alta"},
    {"itemtype": "Computer", "id": "503", "name": "PC-RECEPCION", "entities_id": 2, "criticidad": ""},
    {"itemtype": "Computer", "id": "601", "name": "SRV-APP", "entities_id": 3, "criticidad": ""},
]


# Salud inventariada por id de Computer (demo): discos en MB y antivirus.
_DEMO_SALUD = {
    "501": {"discos": [{"nombre": "C:", "total_mb": 500000, "libre_mb": 30000},
                       {"nombre": "D:", "total_mb": 1000000, "libre_mb": 600000}],
            "antivirus": [{"nombre": "Windows Defender", "activo": True, "actualizado": True}]},
    "502": {"discos": [{"nombre": "C:", "total_mb": 250000, "libre_mb": 20000}],
            "antivirus": [{"nombre": "Windows Defender", "activo": False, "actualizado": False}]},
    "503": {"discos": [{"nombre": "C:", "total_mb": 250000, "libre_mb": 120000}], "antivirus": []},
}


_DEMO_USUARIOS: dict[str, dict] = {}


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

    def _delete(self, ruta: str, params: dict | None = None) -> Any:
        self._asegurar_sesion()
        r = self._client().delete(f"{self._base}{ruta}", headers=self._headers_sesion(), params=params)
        if r.status_code not in (200, 204):
            raise AdapterError("GLPI", f"DELETE {ruta} → {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

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

    # -- regla de asignación de entidad por tag (inventario) -----------------

    def asegurar_regla_tag(self, nombre: str, tag: str, entity_id: int) -> int:
        """Crea (si falta) la regla que manda a la entidad del cliente todo
        inventario que llegue con ese TAG del agente. Idempotente por nombre.

        ⚠ GLPI 11: RuleImportEntity + RuleCriteria (criteria=tag) + RuleAction
          (assign entities_id). Verificar contra el server real.
        """
        if self.demo:
            return 800 + (abs(hash(tag)) % 99)
        try:
            existentes = self._get("/RuleImportEntity", {"range": "0-999"})
        except AdapterError:
            existentes = []
        for r in existentes if isinstance(existentes, list) else []:
            if r.get("name") == nombre:
                return int(r["id"])
        rid = self._id_de(self._post("/RuleImportEntity", {"input": {
            "name": nombre, "sub_type": "RuleImportEntity", "match": "AND",
            "is_active": 1, "entities_id": 0, "is_recursive": 1,
            "comment": "Creada por la consola de iTier"}}))
        if rid is None:
            raise AdapterError("GLPI", "RuleImportEntity sin id en la respuesta")
        # condition 0 = "es"
        self._post("/RuleCriteria", {"input": {"rules_id": rid, "criteria": "tag", "condition": 0, "pattern": tag}})
        self._post("/RuleAction", {"input": {"rules_id": rid, "action_type": "assign",
                                             "field": "entities_id", "value": int(entity_id)}})
        return rid

    # -- descubrimiento de red (SNMP) ---------------------------------------

    @staticmethod
    def _id_de(resp: Any) -> int | None:
        if isinstance(resp, list):
            resp = resp[0] if resp else {}
        return resp.get("id") if isinstance(resp, dict) else None

    def crear_credencial_snmp(
        self, nombre: str, version: str, *, community: str | None = None,
        username: str | None = None, auth_proto: str | None = None, auth_pass: str | None = None,
        priv_proto: str | None = None, priv_pass: str | None = None,
    ) -> int:
        """Crea una credencial SNMP (core). Es global (no por entidad); por eso el
        servicio prefija el nombre con el tenant, para ubicarla en la lista."""
        if self.demo:
            return 700 + (abs(hash(nombre)) % 99)
        inp: dict[str, Any] = {"name": nombre, "snmpversion": _SNMP_VER_COD.get(version, 2)}
        if version in ("1", "2c"):
            inp["community"] = community or "public"
        else:  # v3
            inp["username"] = username or ""
            if auth_proto:
                inp["authentication"] = _SNMP_AUTH_COD.get(auth_proto.upper(), 2)
            if auth_pass:
                inp["auth_passphrase"] = auth_pass
            if priv_proto:
                inp["encryption"] = _SNMP_PRIV_COD.get(priv_proto.upper(), 2)
            if priv_pass:
                inp["priv_passphrase"] = priv_pass
        cid = self._id_de(self._post(f"/{IT_SNMPCRED}", {"input": inp}))
        if cid is None:
            raise AdapterError("GLPI", "crear_credencial_snmp sin id en la respuesta")
        return int(cid)

    def crear_rango_ip(self, entity_id: int, nombre: str, ip_start: str, ip_end: str) -> int:
        """Crea un rango de IP (plugin GlpiInventory), acotado a la entidad del cliente.
        ⚠ ip_start/ip_end como string dotted-quad; confirmá el formato en tu plugin."""
        if self.demo:
            return 800 + (abs(hash(nombre)) % 99)
        inp = {"name": nombre, "entities_id": entity_id, "ip_start": ip_start, "ip_end": ip_end}
        rid = self._id_de(self._post(f"/{IT_IPRANGE}", {"input": inp}))
        if rid is None:
            raise AdapterError("GLPI", "crear_rango_ip sin id en la respuesta")
        return int(rid)

    def vincular_rango_credencial(self, range_id: int, cred_id: int, rank: int = 1) -> None:
        if self.demo:
            return
        self._post(
            f"/{IT_IPRANGE_CRED}",
            {"input": {"plugin_glpiinventory_ipranges_id": range_id,
                       "snmpcredentials_id": cred_id, "rank": rank}},
        )

    # -- criticidad (curación de CMDB) --------------------------------------

    def listar_cis(self, entity_id) -> list[dict]:
        """CIs (computers) de una entidad, con su criticidad actual (para curar)."""
        if self.demo:
            return [dict(c) for c in _DEMO_CIS if str(c["entities_id"]) == str(entity_id)]
        if entity_id in (None, ""):
            return []
        try:
            comps = self._get("/Computer", params={"range": "0-500"})
        except AdapterError as e:
            if "RANGE_EXCEED_TOTAL" in str(e):
                return []
            raise
        if not isinstance(comps, list):
            return []
        comps = [c for c in comps if str(c.get("entities_id")) == str(entity_id)]
        nombres: dict[str, str] = {}  # id de business criticity → nombre (valor actual)
        try:
            for x in self._get(f"/{IT_BUSINESSCRIT}", params={"range": "0-200"}) or []:
                if isinstance(x, dict):
                    nombres[str(x.get("id"))] = x.get("name") or ""
        except AdapterError:
            pass
        return [{"itemtype": "Computer", "id": str(c.get("id")), "name": c.get("name"),
                 "criticidad": nombres.get(str(c.get("businesscriticities_id")), "")}
                for c in comps]

    # -- usuarios de plataforma (técnicos / PM / administradores) ---------------

    # rol de iTier → (nombre del perfil de GLPI, id por defecto de una instalación estándar)
    PERFILES = {"admin": ("Super-Admin", 4), "pm": ("Supervisor", 7), "tecnico": ("Technician", 6)}

    def _perfil_id(self, rol: str) -> int:
        nombre, por_defecto = self.PERFILES[rol]
        try:
            for p in self._get("/Profile", params={"range": "0-200"}) or []:
                if isinstance(p, dict) and (p.get("name") or "").strip().lower() == nombre.lower():
                    return int(p["id"])
        except AdapterError:
            pass
        return por_defecto

    def buscar_usuario(self, username: str) -> dict | None:
        if self.demo:
            return _DEMO_USUARIOS.get(username.lower())
        for u in self._get("/User", params={"range": "0-1999"}) or []:
            if isinstance(u, dict) and (u.get("name") or "").lower() == username.lower():
                return {"id": str(u["id"]), "activo": bool(int(u.get("is_active") or 0))}
        return None

    def _asignar_perfil(self, uid: str, rol: str) -> None:
        """Deja al usuario con UN solo perfil (el del rol), sobre la entidad raíz y sus hijas
        (todos los clientes, presentes y futuros)."""
        try:
            for pu in self._get(f"/User/{uid}/Profile_User") or []:
                if isinstance(pu, dict) and pu.get("id") is not None:
                    self._delete(f"/Profile_User/{pu['id']}")
        except AdapterError:
            pass
        self._post("/Profile_User", {"input": {"users_id": int(uid), "profiles_id": self._perfil_id(rol),
                                               "entities_id": 0, "is_recursive": 1}})

    def asegurar_usuario(self, username: str, password: str, email: str, nombre: str, rol: str) -> tuple[str, str]:
        """Crea el usuario o, si ya existe, le fija clave, perfil y lo activa. → (id, 'creado'|'actualizado')."""
        if self.demo:
            ya = username.lower() in _DEMO_USUARIOS
            _DEMO_USUARIOS[username.lower()] = {"id": str(700 + len(_DEMO_USUARIOS)), "activo": True}
            return _DEMO_USUARIOS[username.lower()]["id"], "actualizado" if ya else "creado"
        ex = self.buscar_usuario(username)
        if ex:
            self._put(f"/User/{ex['id']}", {"input": {"password": password, "password2": password, "is_active": 1}})
            self._asignar_perfil(ex["id"], rol)
            return ex["id"], "actualizado"
        partes = (nombre or username).split(" ", 1)
        r = self._post("/User", {"input": {
            "name": username, "password": password, "password2": password, "is_active": 1,
            "firstname": partes[0], "realname": partes[1] if len(partes) > 1 else "",
            "_useremails": [email], "_profiles_id": self._perfil_id(rol), "_entities_id": 0, "_is_recursive": 1}})
        uid = str(r["id"] if isinstance(r, dict) else r[0]["id"])
        return uid, "creado"

    def fijar_password_usuario(self, uid: str, password: str) -> None:
        if not self.demo:
            self._put(f"/User/{uid}", {"input": {"password": password, "password2": password}})

    def cambiar_rol_usuario(self, uid: str, rol: str) -> None:
        if not self.demo:
            self._asignar_perfil(uid, rol)

    def activar_usuario(self, uid: str, activo: bool) -> None:
        if not self.demo:
            self._put(f"/User/{uid}", {"input": {"is_active": 1 if activo else 0}})

    def eliminar_usuario(self, uid: str) -> None:
        if not self.demo:
            self._delete(f"/User/{uid}", params={"force_purge": 1})

    def salud_cis(self, ids: list[str]) -> dict[str, dict]:
        """Discos y antivirus que el GLPI Agent inventarió, por id de Computer.

        Dos consultas en bloque (no una por equipo): /Item_Disk y /ItemAntivirus (en GLPI < 10.0.x
        el antivirus era /ComputerAntivirus, con `computers_id`). tamaños de disco en MB.
        ⚠ Es lo del último inventario del agente (suele ser diario), no tiempo real."""
        quiere = {str(i) for i in ids}
        salida: dict[str, dict] = {i: {"discos": [], "antivirus": [], "inventariado": False} for i in quiere}
        if self.demo:
            for i in quiere:
                d = _DEMO_SALUD.get(i)
                if d:
                    salida[i] = {**d, "inventariado": True}
            return salida
        if not quiere:
            return salida

        def listar(clase: str) -> list[dict]:
            try:
                r = self._get(f"/{clase}", params={"range": "0-4999"})
            except AdapterError as e:
                if "RANGE_EXCEED_TOTAL" in str(e):
                    return []
                raise
            return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []

        for d in listar("Item_Disk"):
            i = str(d.get("items_id"))
            if i in quiere and (d.get("itemtype") or "Computer") == "Computer":
                salida[i]["inventariado"] = True
                salida[i]["discos"].append({
                    "nombre": d.get("mountpoint") or d.get("name") or d.get("device") or "?",
                    "total_mb": int(d.get("totalsize") or 0), "libre_mb": int(d.get("freesize") or 0)})
        av: list[dict] = []
        for clase in ("ItemAntivirus", "ComputerAntivirus"):
            try:
                av = listar(clase)
                break
            except AdapterError:
                continue
        for a in av:
            i = str(a.get("items_id") if a.get("items_id") is not None else a.get("computers_id"))
            if i in quiere:
                salida[i]["inventariado"] = True
                salida[i]["antivirus"].append({
                    "nombre": a.get("name") or "Antivirus", "activo": bool(int(a.get("is_active") or 0)),
                    "actualizado": bool(int(a.get("is_uptodate") or 0))})
        return salida

    def _criticidad_id(self, nombre: str) -> int:
        """Id del valor de business criticity en GLPI (lo crea si no existe)."""
        if self.demo:
            return 900 + (abs(hash(nombre)) % 99)
        try:
            for x in self._get(f"/{IT_BUSINESSCRIT}", params={"range": "0-200"}) or []:
                if isinstance(x, dict) and (x.get("name") or "") == nombre:
                    return int(x["id"])
        except AdapterError:
            pass
        cid = self._id_de(self._post(f"/{IT_BUSINESSCRIT}", {"input": {"name": nombre}}))
        if cid is None:
            raise AdapterError("GLPI", "no se pudo crear el valor de criticidad")
        return int(cid)

    def fijar_criticidad(self, itemtype: str, items_id: str, criticidad: str) -> dict:
        """Escribe la criticidad del CI en GLPI (campo businesscriticities_id). Es la
        fuente que lee el agente de borde para el techo de autonomía."""
        nombre = CRITICIDADES.get(criticidad)
        if not nombre:
            raise AdapterError("GLPI", f"criticidad inválida: {criticidad}")
        if self.demo:
            for c in _DEMO_CIS:
                if c["itemtype"] == itemtype and str(c["id"]) == str(items_id):
                    c["criticidad"] = nombre
            return {"ok": True, "criticidad": nombre}
        crit_id = self._criticidad_id(nombre)
        self._put(f"/{itemtype}/{items_id}",
                  {"input": {"id": int(items_id), "businesscriticities_id": crit_id}})
        return {"ok": True, "criticidad": nombre}

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
