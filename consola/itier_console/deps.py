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

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


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
        }
    return None


def require_user(request: Request) -> dict:
    """Guard de rutas de OPERADOR (consola)."""
    u = sesion_usuario(request)
    if not u:
        raise AuthRequired()
    if u.get("client_id"):
        raise AccesoPortal()  # un cliente no entra a la consola de operador
    if request.session.get("must_change") and request.url.path not in ("/cambiar-clave", "/logout"):
        raise CambioClaveRequerido()
    return u


def require_cliente(request: Request) -> dict:
    """Guard de rutas del PORTAL (cliente). Devuelve el usuario con su client_id."""
    u = sesion_usuario(request)
    if not u:
        raise AuthRequired()
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
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status_code)
