"""Dependencias y utilidades compartidas por las rutas.

Centraliza: el motor de plantillas, la conexión por request, el guardia de sesión
(require_user) y los mensajes flash. Mantiene las rutas finas y consistentes.
"""

from __future__ import annotations

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
            "rol": rbac.rol_de_fila(bool(s.get("is_admin")), s.get("client_id"), s.get("rol")),
        }
    return None


def _refrescar(request: Request, u: dict) -> dict:
    """Relee de la base el estado del usuario en CADA request: una baja o un cambio de rol rige al
    instante (no al vencer la sesión). Un usuario eliminado o dado de baja pierde la sesión."""
    conn = db_mod.connect(request.app.state.cfg.db_path)
    try:
        f = conn.execute("SELECT is_admin, disabled, client_id, rol FROM users WHERE id=?", (u["uid"],)).fetchone()
    finally:
        conn.close()
    if not f or f["disabled"]:
        request.session.clear()
        raise AuthRequired()
    request.session["is_admin"] = bool(f["is_admin"])
    request.session["rol"] = rbac.rol_de_fila(bool(f["is_admin"]), f["client_id"], f["rol"])
    return dict(u, is_admin=bool(f["is_admin"]), rol=request.session["rol"])


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
    return u


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
