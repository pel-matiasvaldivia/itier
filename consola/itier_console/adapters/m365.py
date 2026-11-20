"""Microsoft 365 (Entra ID) por Microsoft Graph: ABM de usuarios del tenant de un cliente.

Modelo: UNA aplicación multi-tenant registrada por el MSP (client id + secreto, en la consola). Cada
cliente (su administrador global) le da «consentimiento de administrador» una sola vez; desde ahí la consola
pide un token por tenant (client credentials, permisos de aplicación) y opera sobre sus usuarios.

Permisos de aplicación que necesita la app (se piden en el consentimiento con `.default`):
  User.ReadWrite.All · User-PasswordProfile.ReadWrite.All · Organization.Read.All
  (+ opcionalmente Directory.ReadWrite.All si el tenant exige más para reiniciar claves).

Modo demo: un tenant de ejemplo en memoria, sin red.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import httpx

from . import AdapterError

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SISTEMA = "Microsoft 365"

SELECT = ("id,displayName,givenName,surname,userPrincipalName,mail,jobTitle,department,"
          "accountEnabled,assignedLicenses,usageLocation,createdDateTime,userType")

# Nombres comerciales de los planes más comunes (skuPartNumber → nombre). El resto se muestra con su código.
PLANES = {
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    "SPB": "Microsoft 365 Business Premium",
    "SMB_BUSINESS": "Microsoft 365 Apps for business",
    "SMB_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic (antiguo)",
    "SMB_BUSINESS_PREMIUM": "Microsoft 365 Business Standard (antiguo)",
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "STANDARDPACK": "Office 365 E1",
    "O365_BUSINESS": "Microsoft 365 Apps for business",
    "TEAMS_EXPLORATORY": "Microsoft Teams Exploratory",
    "FLOW_FREE": "Power Automate (gratis)",
    "POWER_BI_STANDARD": "Power BI (gratis)",
    "MICROSOFT_365_COPILOT": "Microsoft 365 Copilot",
}


def nombre_plan(sku_part_number: str) -> str:
    return PLANES.get(sku_part_number, sku_part_number)


# -- token por tenant (client credentials) ---------------------------------------------------
_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}
_LOCK = threading.Lock()


def _token(client_id: str, secret: str, tenant: str) -> str:
    clave = (tenant, client_id)
    with _LOCK:
        t = _TOKENS.get(clave)
        if t and t[1] > time.monotonic() + 60:
            return t[0]
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{LOGIN}/{tenant}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default"})
    if r.status_code != 200:
        try:
            j = r.json()
            det = j.get("error_description") or j.get("error") or r.text
        except ValueError:
            det = r.text
        det = str(det).splitlines()[0][:220]
        if "AADSTS700016" in str(det) or "AADSTS65001" in str(det) or "AADSTS7000229" in str(det) or "AADSTS500011" in str(det):
            det = "El tenant todavía no autorizó la aplicación (falta el consentimiento del administrador). " + det
        elif "AADSTS7000215" in str(det) or "AADSTS7000222" in str(det):
            det = "El secreto de la aplicación es inválido o venció. " + det
        raise AdapterError(SISTEMA, f"No se pudo obtener el token: {det}")
    j = r.json()
    tok = j["access_token"]
    with _LOCK:
        _TOKENS[clave] = (tok, time.monotonic() + int(j.get("expires_in", 3300)))
    return tok


def olvidar_token(tenant: str, client_id: str) -> None:
    with _LOCK:
        _TOKENS.pop((tenant, client_id), None)


def _msg_error(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        code, msg = e.get("code", ""), e.get("message", "")
    except ValueError:
        code, msg = "", r.text[:200]
    amigables = {
        "Authorization_RequestDenied": "Microsoft rechazó la operación: la aplicación no tiene el permiso necesario en este tenant "
                                       "(o el usuario es un administrador protegido).",
        "Request_ResourceNotFound": "El usuario no existe (¿ya fue eliminado?).",
        "Request_BadRequest": "Datos inválidos.",
        "InvalidAuthenticationToken": "El token venció; reintentá.",
    }
    base = amigables.get(code, code or f"HTTP {r.status_code}")
    return f"{base} {msg}".strip()[:300]


class M365Client:
    def __init__(self, cfg, tenant: str | None):
        self.cfg = cfg
        self.tenant = tenant
        self.demo = bool(cfg.demo)

    # -- HTTP -----------------------------------------------------------------------------
    def _req(self, metodo: str, ruta: str, *, params=None, cuerpo=None, ok=(200, 201, 204), reintento=True) -> Any:
        if not self.tenant:
            raise AdapterError(SISTEMA, "El cliente todavía no tiene un tenant de Microsoft 365 conectado.")
        if not (self.cfg.m365_client_id and self.cfg.m365_client_secret):
            raise AdapterError(SISTEMA, "Falta configurar la aplicación de Microsoft 365 (client id y secreto).")
        tok = _token(self.cfg.m365_client_id, self.cfg.m365_client_secret, self.tenant)
        url = ruta if ruta.startswith("http") else f"{GRAPH}{ruta}"
        with httpx.Client(timeout=25) as c:
            r = c.request(metodo, url, headers={"Authorization": f"Bearer {tok}"}, params=params, json=cuerpo)
        if r.status_code == 401 and reintento:
            olvidar_token(self.tenant, self.cfg.m365_client_id)
            return self._req(metodo, ruta, params=params, cuerpo=cuerpo, ok=ok, reintento=False)
        if r.status_code not in ok:
            raise AdapterError(SISTEMA, f"{metodo} {ruta.split('?')[0]} → {r.status_code}: {_msg_error(r)}")
        return r.json() if r.content else {}

    # -- tenant ---------------------------------------------------------------------------
    def organizacion(self) -> dict:
        """Nombre, id y dominios verificados del tenant (sirve también para comprobar la conexión)."""
        if self.demo:
            return dict(_DEMO_ORG)
        d = self._req("GET", "/organization", params={"$select": "id,displayName,verifiedDomains"})
        o = (d.get("value") or [{}])[0]
        doms = o.get("verifiedDomains") or []
        return {"id": o.get("id", ""), "nombre": o.get("displayName", ""),
                "dominios": [x["name"] for x in doms],
                "dominio_principal": next((x["name"] for x in doms if x.get("isDefault")), (doms[0]["name"] if doms else ""))}

    def planes(self) -> list[dict]:
        """Planes (SKUs) del tenant con cuántas licencias tiene y cuántas usadas."""
        if self.demo:
            return [dict(x) for x in _DEMO_SKUS]
        d = self._req("GET", "/subscribedSkus")
        salida = []
        for s in d.get("value", []):
            hab = (s.get("prepaidUnits") or {}).get("enabled", 0)
            if not hab:
                continue                                   # planes vencidos o sin unidades
            salida.append({"sku_id": s["skuId"], "codigo": s.get("skuPartNumber", ""),
                           "nombre": nombre_plan(s.get("skuPartNumber", "")),
                           "total": hab, "usadas": s.get("consumedUnits", 0), "libres": hab - s.get("consumedUnits", 0)})
        return sorted(salida, key=lambda x: x["nombre"])

    # -- usuarios -------------------------------------------------------------------------
    @staticmethod
    def _norm(u: dict) -> dict:
        return {
            "id": u["id"], "nombre": u.get("displayName") or "", "nombre_pila": u.get("givenName") or "",
            "apellido": u.get("surname") or "", "upn": u.get("userPrincipalName") or "", "correo": u.get("mail") or "",
            "cargo": u.get("jobTitle") or "", "departamento": u.get("department") or "",
            "habilitado": bool(u.get("accountEnabled", True)), "pais": u.get("usageLocation") or "",
            "licencias": [x.get("skuId") for x in (u.get("assignedLicenses") or []) if x.get("skuId")],
            "tipo": u.get("userType") or "Member", "creado": (u.get("createdDateTime") or "")[:10],
        }

    def listar_usuarios(self, limite: int = 999) -> list[dict]:
        if self.demo:
            return [self._norm(u) for u in _DEMO_USERS.values()]
        url, params, salida = "/users", {"$select": SELECT, "$top": 100}, []
        while url and len(salida) < limite:
            d = self._req("GET", url, params=params)
            salida += [self._norm(u) for u in d.get("value", [])]
            url, params = d.get("@odata.nextLink"), None
        return salida

    def ver_usuario(self, uid: str) -> dict:
        if self.demo:
            if uid not in _DEMO_USERS:
                raise AdapterError(SISTEMA, "El usuario no existe (demo).")
            return self._norm(_DEMO_USERS[uid])
        return self._norm(self._req("GET", f"/users/{uid}", params={"$select": SELECT}))

    def crear_usuario(self, *, upn: str, nombre: str, apellido: str, clave: str, cargo: str = "", departamento: str = "",
                      pais: str = "AR", sku_ids: list[str] | None = None, cambiar_clave: bool = True) -> dict:
        """Crea el usuario (con clave temporal) y le asigna las licencias. Devuelve el usuario creado."""
        mail_nick = upn.split("@", 1)[0]
        display = f"{nombre} {apellido}".strip()
        if self.demo:
            if any(u["userPrincipalName"].lower() == upn.lower() for u in _DEMO_USERS.values()):
                raise AdapterError(SISTEMA, "POST /users → 400: Ya existe otro usuario con ese nombre principal.")
            uid = str(uuid.uuid4())
            _DEMO_USERS[uid] = {"id": uid, "displayName": display, "givenName": nombre, "surname": apellido,
                                "userPrincipalName": upn, "mail": upn, "jobTitle": cargo, "department": departamento,
                                "accountEnabled": True, "usageLocation": pais, "userType": "Member",
                                "assignedLicenses": [{"skuId": s} for s in (sku_ids or [])], "createdDateTime": "2026-11-19T00:00:00Z"}
            return self._norm(_DEMO_USERS[uid])
        cuerpo = {"accountEnabled": True, "displayName": display, "givenName": nombre, "surname": apellido,
                  "mailNickname": mail_nick, "userPrincipalName": upn, "usageLocation": pais,
                  "passwordProfile": {"forceChangePasswordNextSignIn": cambiar_clave, "password": clave}}
        if cargo:
            cuerpo["jobTitle"] = cargo
        if departamento:
            cuerpo["department"] = departamento
        u = self._req("POST", "/users", cuerpo=cuerpo)
        if sku_ids:
            try:
                self.asignar_licencias(u["id"], sku_ids, [], verificar_ubicacion=False)
            except AdapterError as e:
                raise AdapterError(SISTEMA, f"El usuario se creó, pero no se le pudo asignar la licencia: {e.detalle}") from e
        return self._norm(u)

    def actualizar_usuario(self, uid: str, campos: dict) -> None:
        """campos: nombre, apellido, cargo, departamento, pais (solo los presentes)."""
        mapa = {"nombre": "givenName", "apellido": "surname", "cargo": "jobTitle", "departamento": "department",
                "pais": "usageLocation"}
        cuerpo = {mapa[k]: v for k, v in campos.items() if k in mapa}
        if "nombre" in campos or "apellido" in campos:
            ref = self.ver_usuario(uid) if (self.demo or "nombre" not in campos or "apellido" not in campos) else None
            n = campos.get("nombre", ref["nombre_pila"] if ref else "")
            a = campos.get("apellido", ref["apellido"] if ref else "")
            cuerpo["displayName"] = f"{n} {a}".strip()
        if not cuerpo:
            return
        if self.demo:
            _DEMO_USERS[uid].update(cuerpo)
            return
        self._req("PATCH", f"/users/{uid}", cuerpo=cuerpo)

    def reiniciar_clave(self, uid: str, clave: str, cambiar_clave: bool = True) -> None:
        if self.demo:
            if uid not in _DEMO_USERS:
                raise AdapterError(SISTEMA, "El usuario no existe (demo).")
            return
        self._req("PATCH", f"/users/{uid}", cuerpo={"passwordProfile": {"forceChangePasswordNextSignIn": cambiar_clave, "password": clave}})

    def habilitar(self, uid: str, habilitado: bool) -> None:
        if self.demo:
            _DEMO_USERS[uid]["accountEnabled"] = habilitado
            return
        self._req("PATCH", f"/users/{uid}", cuerpo={"accountEnabled": habilitado})

    def eliminar(self, uid: str) -> None:
        """Mueve el usuario a la papelera de Microsoft (se puede restaurar 30 días)."""
        if self.demo:
            _DEMO_USERS.pop(uid, None)
            return
        self._req("DELETE", f"/users/{uid}")

    def asignar_licencias(self, uid: str, agregar: list[str], quitar: list[str], verificar_ubicacion: bool = True) -> None:
        if self.demo:
            u = _DEMO_USERS[uid]
            act = {x["skuId"] for x in u["assignedLicenses"]}
            act = (act | set(agregar)) - set(quitar)
            u["assignedLicenses"] = [{"skuId": s} for s in sorted(act)]
            return
        if not agregar and not quitar:
            return
        if agregar and verificar_ubicacion:                # sin ubicación de uso, Microsoft no deja asignar licencias
            u = self._req("GET", f"/users/{uid}", params={"$select": "usageLocation"})
            if not u.get("usageLocation"):
                self._req("PATCH", f"/users/{uid}", cuerpo={"usageLocation": "AR"})
        self._req("POST", f"/users/{uid}/assignLicense", cuerpo={
            "addLicenses": [{"skuId": s, "disabledPlans": []} for s in agregar], "removeLicenses": list(quitar)})


# -- tenant de ejemplo (demo) -----------------------------------------------------------------
_SKU_BASICO, _SKU_STD = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
_DEMO_ORG = {"id": "99999999-9999-9999-9999-999999999999", "nombre": "Cliente Demo SRL",
             "dominios": ["clientedemo.onmicrosoft.com", "clientedemo.com.ar"], "dominio_principal": "clientedemo.com.ar"}
_DEMO_SKUS = [
    {"sku_id": _SKU_BASICO, "codigo": "O365_BUSINESS_ESSENTIALS", "nombre": "Microsoft 365 Business Basic", "total": 10, "usadas": 2, "libres": 8},
    {"sku_id": _SKU_STD, "codigo": "O365_BUSINESS_PREMIUM", "nombre": "Microsoft 365 Business Standard", "total": 5, "usadas": 1, "libres": 4},
]
_DEMO_USERS: dict[str, dict] = {
    "u-ana": {"id": "u-ana", "displayName": "Ana Pérez", "givenName": "Ana", "surname": "Pérez", "userPrincipalName": "ana@clientedemo.com.ar",
              "mail": "ana@clientedemo.com.ar", "jobTitle": "Administración", "department": "Finanzas", "accountEnabled": True,
              "usageLocation": "AR", "userType": "Member", "assignedLicenses": [{"skuId": _SKU_STD}], "createdDateTime": "2025-03-01T00:00:00Z"},
    "u-luis": {"id": "u-luis", "displayName": "Luis Gómez", "givenName": "Luis", "surname": "Gómez", "userPrincipalName": "luis@clientedemo.com.ar",
               "mail": "luis@clientedemo.com.ar", "jobTitle": "Ventas", "department": "Comercial", "accountEnabled": True,
               "usageLocation": "AR", "userType": "Member", "assignedLicenses": [{"skuId": _SKU_BASICO}], "createdDateTime": "2025-06-10T00:00:00Z"},
    "u-eva": {"id": "u-eva", "displayName": "Eva Ruiz", "givenName": "Eva", "surname": "Ruiz", "userPrincipalName": "eva@clientedemo.com.ar",
              "mail": "", "jobTitle": "", "department": "", "accountEnabled": False,
              "usageLocation": "AR", "userType": "Member", "assignedLicenses": [], "createdDateTime": "2024-11-20T00:00:00Z"},
}


def cliente(cfg, tenant: str | None) -> M365Client:
    return M365Client(cfg, tenant)


def url_consentimiento(cfg, redirect_uri: str, state: str, tenant_hint: str | None = None) -> str:
    """Enlace para que el administrador global del cliente autorice la aplicación en su tenant."""
    from urllib.parse import urlencode
    q = urlencode({"client_id": cfg.m365_client_id, "scope": "https://graph.microsoft.com/.default",
                   "redirect_uri": redirect_uri, "state": state})
    return f"{LOGIN}/{tenant_hint or 'organizations'}/v2.0/adminconsent?{q}"
