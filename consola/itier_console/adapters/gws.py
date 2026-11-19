"""Google Workspace: ABM de usuarios del dominio de un cliente (Admin SDK · Directory API).

Modelo: UNA cuenta de servicio del MSP con «delegación en todo el dominio». Cada cliente (un super administrador suyo)
autoriza el ID de cliente de esa cuenta con los permisos (scopes) necesarios, una sola vez, en su Admin console. La consola
se hace pasar por un administrador del cliente (`sub`) para llamar a la API. Por eso no hace falta el flujo OAuth de usuario
(que para estos permisos exige verificación de la app por Google).

Scopes: admin.directory.user (ABM) y admin.directory.domain.readonly (dominios del cliente).
La firma del JWT (RS256) se hace con `cryptography`: no se necesitan librerías de Google.

Modo demo: un dominio de ejemplo en memoria, sin red.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import AdapterError

API = "https://admin.googleapis.com/admin/directory/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SISTEMA = "Google Workspace"
SCOPES = ["https://www.googleapis.com/auth/admin.directory.user",
          "https://www.googleapis.com/auth/admin.directory.domain.readonly"]
CAMPOS_USUARIO = ("users(id,primaryEmail,name,suspended,isAdmin,isDelegatedAdmin,orgUnitPath,creationTime,lastLoginTime,"
                  "organizations,changePasswordAtNextLogin,archived),nextPageToken")

_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}
_LOCK = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def parsear_cuenta_servicio(texto: str) -> dict:
    """Valida el JSON de la cuenta de servicio y devuelve {client_email, client_id, private_key}. Levanta ValueError."""
    try:
        d = json.loads(texto)
    except ValueError as e:
        raise ValueError("No es un JSON válido.") from e
    if not isinstance(d, dict) or d.get("type") != "service_account":
        raise ValueError("No parece la clave JSON de una cuenta de servicio de Google (falta «type: service_account»).")
    for k in ("client_email", "client_id", "private_key"):
        if not d.get(k):
            raise ValueError(f"Al JSON le falta el campo «{k}».")
    try:
        serialization.load_pem_private_key(d["private_key"].encode(), password=None)
    except Exception as e:  # noqa: BLE001
        raise ValueError("La clave privada del JSON no se pudo leer.") from e
    return {"client_email": d["client_email"], "client_id": str(d["client_id"]), "private_key": d["private_key"]}


def _firmar_jwt(sa: dict, sub: str, scopes: list[str]) -> str:
    ahora = int(time.time())
    cab = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    cuerpo = _b64(json.dumps({"iss": sa["client_email"], "sub": sub, "scope": " ".join(scopes), "aud": TOKEN_URL,
                              "iat": ahora, "exp": ahora + 3600}, separators=(",", ":")).encode())
    clave = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    firma = clave.sign(f"{cab}.{cuerpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{cab}.{cuerpo}.{_b64(firma)}"


def _token(sa: dict, admin: str) -> str:
    clave = (sa["client_email"], admin.lower())
    with _LOCK:
        t = _TOKENS.get(clave)
        if t and t[1] > time.monotonic() + 60:
            return t[0]
    with httpx.Client(timeout=20) as c:
        r = c.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                    "assertion": _firmar_jwt(sa, admin, SCOPES)})
    if r.status_code != 200:
        try:
            j = r.json()
            err, desc = j.get("error", ""), j.get("error_description", "")
        except ValueError:
            err, desc = "", r.text[:200]
        if err == "unauthorized_client":
            det = (f"El dominio todavía no autorizó la delegación: en la Admin console del cliente → Seguridad → Controles de API → "
                   f"Delegación en todo el dominio, agregá el ID de cliente {sa['client_id']} con los permisos indicados. ({desc})")
        elif err == "invalid_grant":
            det = (f"Google rechazó la identidad de «{admin}»: tiene que ser un super administrador activo de ese dominio. ({desc})")
        else:
            det = f"{err} {desc}".strip() or f"HTTP {r.status_code}"
        raise AdapterError(SISTEMA, f"No se pudo obtener el token: {det[:350]}")
    j = r.json()
    with _LOCK:
        _TOKENS[clave] = (j["access_token"], time.monotonic() + int(j.get("expires_in", 3300)))
    return j["access_token"]


def olvidar_token(sa: dict, admin: str) -> None:
    with _LOCK:
        _TOKENS.pop((sa["client_email"], admin.lower()), None)


def _msg_error(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        msg, motivo = e.get("message", ""), (e.get("errors") or [{}])[0].get("reason", "")
    except ValueError:
        msg, motivo = r.text[:200], ""
    amigables = {
        "forbidden": "El administrador con el que se conecta no tiene privilegios para esta operación (o es un administrador protegido).",
        "notFound": "El usuario no existe (¿ya fue eliminado?).",
        "duplicate": "Ya existe un usuario o alias con esa dirección.",
        "invalid": "Datos inválidos.",
        "quotaExceeded": "Se alcanzó el límite de la API; reintentá en unos minutos.",
    }
    return f"{amigables.get(motivo, motivo or f'HTTP {r.status_code}')} {msg}".strip()[:300]


class GWSClient:
    def __init__(self, cfg, admin: str | None):
        self.cfg = cfg
        self.admin = admin
        self.demo = bool(cfg.demo)

    def _sa(self) -> dict:
        if not getattr(self.cfg, "gws_sa_json", None):
            raise AdapterError(SISTEMA, "Falta configurar la cuenta de servicio de Google (clave JSON).")
        try:
            return parsear_cuenta_servicio(self.cfg.gws_sa_json)
        except ValueError as e:
            raise AdapterError(SISTEMA, f"La cuenta de servicio guardada no es válida: {e}") from e

    def _req(self, metodo: str, ruta: str, *, params=None, cuerpo=None, ok=(200, 204), reintento=True) -> Any:
        if not self.admin:
            raise AdapterError(SISTEMA, "El cliente todavía no tiene un dominio de Google Workspace conectado.")
        sa = self._sa()
        tok = _token(sa, self.admin)
        with httpx.Client(timeout=25) as c:
            r = c.request(metodo, f"{API}{ruta}", headers={"Authorization": f"Bearer {tok}"}, params=params, json=cuerpo)
        if r.status_code == 401 and reintento:
            olvidar_token(sa, self.admin)
            return self._req(metodo, ruta, params=params, cuerpo=cuerpo, ok=ok, reintento=False)
        if r.status_code not in ok:
            raise AdapterError(SISTEMA, f"{metodo} {ruta} → {r.status_code}: {_msg_error(r)}")
        return r.json() if r.content else {}

    # -- dominio ---------------------------------------------------------------------------
    def dominios(self) -> dict:
        """Dominios del cliente (también comprueba la conexión): {dominios: [...], principal: str}."""
        if self.demo:
            return dict(_DEMO_DOMINIOS)
        d = self._req("GET", "/customer/my_customer/domains")
        doms = [x for x in d.get("domains", []) if x.get("verified")]
        return {"dominios": [x["domainName"] for x in doms],
                "principal": next((x["domainName"] for x in doms if x.get("isPrimary")), (doms[0]["domainName"] if doms else ""))}

    # -- usuarios --------------------------------------------------------------------------
    @staticmethod
    def _norm(u: dict) -> dict:
        n = u.get("name") or {}
        org = (u.get("organizations") or [{}])[0]
        return {
            "id": u["id"], "email": u.get("primaryEmail", ""), "nombre_pila": n.get("givenName", ""), "apellido": n.get("familyName", ""),
            "nombre": n.get("fullName") or f"{n.get('givenName', '')} {n.get('familyName', '')}".strip(),
            "suspendido": bool(u.get("suspended")), "admin": bool(u.get("isAdmin") or u.get("isDelegatedAdmin")),
            "ou": u.get("orgUnitPath", "/"), "cargo": org.get("title", ""), "departamento": org.get("department", ""),
            "creado": (u.get("creationTime") or "")[:10],
            "ultimo_ingreso": (u.get("lastLoginTime") or "")[:10] if not str(u.get("lastLoginTime", "")).startswith("1970") else "",
            "cambiar_clave": bool(u.get("changePasswordAtNextLogin")), "archivado": bool(u.get("archived")),
        }

    def listar_usuarios(self, limite: int = 2000) -> list[dict]:
        if self.demo:
            return [self._norm(u) for u in _DEMO_USERS.values()]
        salida, token = [], None
        while len(salida) < limite:
            p = {"customer": "my_customer", "maxResults": 200, "orderBy": "email", "fields": CAMPOS_USUARIO}
            if token:
                p["pageToken"] = token
            d = self._req("GET", "/users", params=p)
            salida += [self._norm(u) for u in d.get("users", [])]
            token = d.get("nextPageToken")
            if not token:
                break
        return salida

    def ver_usuario(self, uid: str) -> dict:
        if self.demo:
            if uid not in _DEMO_USERS:
                raise AdapterError(SISTEMA, "El usuario no existe (demo).")
            return self._norm(_DEMO_USERS[uid])
        return self._norm(self._req("GET", f"/users/{uid}"))

    def crear_usuario(self, *, email: str, nombre: str, apellido: str, clave: str, cargo: str = "", departamento: str = "",
                      cambiar_clave: bool = True) -> dict:
        if self.demo:
            if any(u["primaryEmail"].lower() == email.lower() for u in _DEMO_USERS.values()):
                raise AdapterError(SISTEMA, "POST /users → 409: Ya existe un usuario o alias con esa dirección.")
            uid = "g-" + uuid.uuid4().hex[:12]
            _DEMO_USERS[uid] = {"id": uid, "primaryEmail": email, "name": {"givenName": nombre, "familyName": apellido, "fullName": f"{nombre} {apellido}"},
                                "suspended": False, "isAdmin": False, "orgUnitPath": "/", "creationTime": "2026-11-19T00:00:00.000Z",
                                "organizations": [{"title": cargo, "department": departamento}] if (cargo or departamento) else [],
                                "changePasswordAtNextLogin": cambiar_clave}
            return self._norm(_DEMO_USERS[uid])
        cuerpo = {"primaryEmail": email, "name": {"givenName": nombre, "familyName": apellido}, "password": clave,
                  "changePasswordAtNextLogin": cambiar_clave}
        if cargo or departamento:
            cuerpo["organizations"] = [{"title": cargo, "department": departamento, "primary": True}]
        return self._norm(self._req("POST", "/users", cuerpo=cuerpo))

    def actualizar_usuario(self, uid: str, campos: dict) -> None:
        cuerpo: dict = {}
        if "nombre" in campos or "apellido" in campos:
            cuerpo["name"] = {k: campos[c] for k, c in (("givenName", "nombre"), ("familyName", "apellido")) if c in campos}
        if "cargo" in campos or "departamento" in campos:
            cuerpo["organizations"] = [{"title": campos.get("cargo", ""), "department": campos.get("departamento", ""), "primary": True}]
        if not cuerpo:
            return
        if self.demo:
            u = _DEMO_USERS[uid]
            if "name" in cuerpo:
                u["name"].update(cuerpo["name"])
                u["name"]["fullName"] = f"{u['name'].get('givenName', '')} {u['name'].get('familyName', '')}".strip()
            if "organizations" in cuerpo:
                u["organizations"] = cuerpo["organizations"]
            return
        self._req("PATCH", f"/users/{uid}", cuerpo=cuerpo)

    def reiniciar_clave(self, uid: str, clave: str, cambiar_clave: bool = True) -> None:
        if self.demo:
            if uid not in _DEMO_USERS:
                raise AdapterError(SISTEMA, "El usuario no existe (demo).")
            _DEMO_USERS[uid]["changePasswordAtNextLogin"] = cambiar_clave
            return
        self._req("PATCH", f"/users/{uid}", cuerpo={"password": clave, "changePasswordAtNextLogin": cambiar_clave})

    def suspender(self, uid: str, suspendido: bool) -> None:
        if self.demo:
            _DEMO_USERS[uid]["suspended"] = suspendido
            return
        self._req("PATCH", f"/users/{uid}", cuerpo={"suspended": suspendido})

    def eliminar(self, uid: str) -> None:
        """Elimina al usuario (Google lo puede restaurar durante unos 20 días desde la Admin console)."""
        if self.demo:
            _DEMO_USERS.pop(uid, None)
            return
        self._req("DELETE", f"/users/{uid}")


# -- dominio de ejemplo (demo) ---------------------------------------------------------------
_DEMO_DOMINIOS = {"dominios": ["gdemo.com.ar", "gdemo.com"], "principal": "gdemo.com.ar"}
_DEMO_USERS: dict[str, dict] = {
    "g-admin": {"id": "g-admin", "primaryEmail": "admin@gdemo.com.ar", "name": {"givenName": "Admin", "familyName": "Demo", "fullName": "Admin Demo"},
                "suspended": False, "isAdmin": True, "orgUnitPath": "/", "creationTime": "2023-01-10T00:00:00.000Z", "lastLoginTime": "2026-11-18T10:00:00.000Z",
                "organizations": [{"title": "Sistemas", "department": "IT"}]},
    "g-sofia": {"id": "g-sofia", "primaryEmail": "sofia@gdemo.com.ar", "name": {"givenName": "Sofía", "familyName": "Paz", "fullName": "Sofía Paz"},
                "suspended": False, "isAdmin": False, "orgUnitPath": "/", "creationTime": "2024-05-02T00:00:00.000Z", "lastLoginTime": "2026-11-17T09:00:00.000Z",
                "organizations": [{"title": "Ventas", "department": "Comercial"}]},
    "g-raul": {"id": "g-raul", "primaryEmail": "raul@gdemo.com.ar", "name": {"givenName": "Raúl", "familyName": "Díaz", "fullName": "Raúl Díaz"},
               "suspended": True, "isAdmin": False, "orgUnitPath": "/", "creationTime": "2022-09-20T00:00:00.000Z", "lastLoginTime": "1970-01-01T00:00:00.000Z",
               "organizations": []},
}


def cliente(cfg, admin: str | None) -> GWSClient:
    return GWSClient(cfg, admin)
