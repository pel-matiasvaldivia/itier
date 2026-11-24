"""Dependencias y utilidades compartidas por las rutas.

Centraliza: el motor de plantillas, la conexión por request, el guardia de sesión
(require_user) y los mensajes flash. Mantiene las rutas finas y consistentes.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import db as db_mod
from . import security
from .services import rbac

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _version_estatico(nombre: str, _cache: dict = {}) -> str:  # noqa: B006 — caché intencional de módulo
    """Huella del contenido de un archivo estático: la URL cambia solo si el archivo cambia, así el navegador
    lo guarda un año (sin revalidar en cada pantalla) y igual toma los cambios al desplegar."""
    if nombre not in _cache:
        import hashlib
        try:
            _cache[nombre] = hashlib.sha1((BASE / "static" / nombre).read_bytes()).hexdigest()[:10]
        except OSError:
            _cache[nombre] = "0"
    return _cache[nombre]


templates.env.globals["static_v"] = _version_estatico


class AuthRequired(Exception):
    """Se lanza cuando una ruta protegida no tiene sesión válida."""


class CambioClaveRequerido(Exception):
    """El usuario tiene una clave inicial que debe cambiar antes de continuar."""


class AccesoPortal(Exception):
    """Un usuario de portal (cliente) intentó una ruta de operador → va al portal."""


class AccesoConsola(Exception):
    """Un operador intentó una ruta del portal → va a la consola."""


def get_config(request: Request):
    return request.app.state.cfg


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db_mod.connect(request.app.state.cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def sesion_usuario(request: Request) -> dict | None:
    s = request.session
    if s.get("stage") == "authed" and s.get("uid"):
        return {
            "uid": s["uid"],
            "user": s.get("user"),
            "is_admin": bool(s.get("is_admin")),
            "client_id": s.get("client_id"),
            "alcance": s.get("alcance"),
            "rol": rbac.rol_de_fila(bool(s.get("is_admin")), s.get("client_id"), s.get("rol")),
        }
    return None


def _refrescar(request: Request, u: dict) -> dict:
    """Relee de la base el estado del usuario en CADA request: una baja o un cambio de rol rige al
    instante (no al vencer la sesión). Un usuario eliminado o dado de baja pierde la sesión."""
    conn = db_mod.connect(request.app.state.cfg.db_path)
    try:
        f = conn.execute("SELECT is_admin, disabled, client_id, rol, scope_client_id FROM users WHERE id=?", (u["uid"],)).fetchone()
        cli = None
        if f and f["scope_client_id"]:
            cli = conn.execute("SELECT id, slug, nombre, glpi_entity_id FROM clients WHERE id=?", (f["scope_client_id"],)).fetchone()
    finally:
        conn.close()
    if not f or f["disabled"]:
        request.session.clear()
        raise AuthRequired()
    rol = rbac.rol_de_fila(bool(f["is_admin"]), f["client_id"], f["rol"])
    alcance = None
    if rbac.es_de_un_cliente(rol):
        if not cli:                                   # su empresa ya no existe: sin acceso a nada
            request.session.clear()
            raise AuthRequired()
        alcance = {"id": cli["id"], "slug": cli["slug"], "nombre": cli["nombre"], "entity": cli["glpi_entity_id"]}
    request.session["is_admin"] = bool(f["is_admin"])
    request.session["rol"] = rol
    request.session["alcance"] = alcance
    return dict(u, is_admin=bool(f["is_admin"]), rol=rol, alcance=alcance)


_RE_CLIENTE = re.compile(r"^/clientes/([^/]+)(/.*)?$")
_RE_EQUIPO = re.compile(r"^/equipos/([^/]+)/")
_RE_TICKET = re.compile(r"^/tickets/(\d+)(/|$)")
_SUBPANTALLAS_CLIENTE = {"activos", "personas", "m365", "gws"}
_GENERALES = ("/tickets", "/equipos", "/manual", "/cambiar-clave", "/logout", "/activos/plantilla.csv")


def _guardia_alcance(request: Request, u: dict) -> None:
    """Un usuario limitado a un cliente (técnico de cliente) solo llega a las pantallas de SU empresa:
    el slug de la ruta tiene que ser el suyo, los tickets tienen que ser de su entidad de GLPI y las pantallas
    generales (tablero, lista de clientes) lo llevan a su ficha."""
    a = u["alcance"]
    path = request.url.path
    if path in ("/", "/clientes"):
        raise AlcanceRedirigir(f"/clientes/{a['slug']}")
    m = _RE_CLIENTE.match(path)
    if m:
        resto = (m.group(2) or "").strip("/")
        if m.group(1) != a["slug"] or (resto and resto.split("/")[0] not in _SUBPANTALLAS_CLIENTE):
            raise PermisoDenegado()
        return
    m = _RE_EQUIPO.match(path)
    if m and m.group(1) != a["slug"]:
        raise PermisoDenegado()
    if not any(path == g or path.startswith(g + "/") for g in _GENERALES):
        raise PermisoDenegado()
    m = _RE_TICKET.match(path)
    if m:
        from .adapters import AdapterError
        from .services import tickets as tickets_svc
        try:
            t = tickets_svc.ver(request.app.state.cfg, int(m.group(1)))
        except AdapterError:
            raise PermisoDenegado()
        if not a.get("entity") or str(t.get("entities_id")) != str(a["entity"]):
            raise PermisoDenegado()


def require_user(request: Request) -> dict:
    """Guard de rutas de OPERADOR (consola)."""
    u = sesion_usuario(request)
    if not u:
        raise AuthRequired()
    u = _refrescar(request, u)
    if u.get("client_id"):
        raise AccesoPortal()  # un cliente no entra a la consola de operador
    if request.session.get("must_change") and request.url.path not in ("/cambiar-clave", "/logout"):
        raise CambioClaveRequerido()
    if u.get("alcance"):
        _guardia_alcance(request, u)
    return u


class AlcanceRedirigir(Exception):
    """Un usuario limitado a un cliente pidió una pantalla general: se lo lleva a la de su empresa."""

    def __init__(self, url: str):
        self.url = url


class PermisoDenegado(Exception):
    """El rol del operador no alcanza para esa pantalla."""


def require_permiso(permiso: str):
    """Guard de operador + permiso del rol (services/rbac.py)."""
    def dep(request: Request) -> dict:
        u = require_user(request)
        if not rbac.puede(u.get("rol"), permiso):
            raise PermisoDenegado()
        return u
    return dep


def require_cliente(request: Request) -> dict:
    """Guard de rutas del PORTAL (cliente). Devuelve el usuario con su client_id."""
    u = sesion_usuario(request)
    if not u:
        raise AuthRequired()
    u = _refrescar(request, u)
    if not u.get("client_id"):
        raise AccesoConsola()  # un operador usa la consola, no el portal
    if request.session.get("must_change") and request.url.path not in ("/cambiar-clave", "/logout"):
        raise CambioClaveRequerido()
    return u


# ── Mensajes flash (una sola vez, vía sesión) ───────────────────────────────

def flash(request: Request, mensaje: str, categoria: str = "info") -> None:
    request.session.setdefault("_flash", []).append({"m": mensaje, "c": categoria})


def _tomar_flash(request: Request) -> list[dict]:
    return request.session.pop("_flash", [])


# ── Render con contexto común ───────────────────────────────────────────────

def render(request: Request, name: str, *, user: dict | None = None, status_code: int = 200, **ctx):
    base = {
        "user": user if user is not None else sesion_usuario(request),
        "csrf": security.asegurar_csrf(request.session),
        "flashes": _tomar_flash(request),
        "demo": request.app.state.cfg.demo,
        "ruta": request.url.path,
        "puede": lambda permiso: rbac.puede(((user if user is not None else sesion_usuario(request)) or {}).get("rol"), permiso),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status_code)
