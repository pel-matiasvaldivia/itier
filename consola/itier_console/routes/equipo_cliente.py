"""Equipo de TI de una empresa cliente (invitar colaboradores, reiniciar claves, bajas).

  GET  /clientes/{slug}/equipo                   lista + invitación
  POST /clientes/{slug}/equipo                   invitar (la clave se muestra UNA vez)
  POST /clientes/{slug}/equipo/{uid}/rol|clave|baja|alta|eliminar
Permiso `equipo.cliente`: administrador de iTiers, administrador de cliente y PM de cliente (ver services/equipo_cliente.py).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, correo, onboarding, rbac
from ..services import equipo_cliente as svc

router = APIRouter()


def _cli(conn, slug):
    return onboarding.obtener_cliente_por_slug(conn, slug)


def _volver(slug) -> RedirectResponse:
    return RedirectResponse(f"/clientes/{slug}/equipo", status_code=303)


def _ok_csrf(request, csrf) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


def _malos(estados: dict) -> str:
    return " · ".join(f"{k}: {v}" for k, v in estados.items() if str(v).startswith("error"))


@router.get("/clientes/{slug}/equipo", response_class=HTMLResponse)
def ver(request: Request, slug: str, user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    nueva = request.session.pop("_clave_equipo", None)   # se muestra UNA sola vez
    miembros = svc.listar(conn, cli["id"])
    return render(request, "equipo_cliente.html", cli=cli, miembros=miembros, nueva=nueva, yo=user["uid"],
                  roles_dar=svc.roles_que_puede_dar(user.get("rol")), roles_todos=rbac.ROLES,
                  gestionables=rbac.PUEDE_INVITAR.get(user.get("rol") or "", set()),
                  smtp=correo.configurado(request.app.state.cfg))


@router.post("/clientes/{slug}/equipo")
def invitar(request: Request, slug: str, username: str = Form(""), email: str = Form(""), nombre: str = Form(""),
            rol: str = Form("tecnico_cliente"), enviar: str = Form(""), csrf: str = Form(""),
            user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _ok_csrf(request, csrf):
        try:
            _uid, clave, estados = svc.invitar(conn, request.app.state.cfg, user, cli["id"], username, email, nombre, rol, bool(enviar))
            request.session["_clave_equipo"] = {"usuario": username.strip().lower(), "clave": clave, "estados": estados, "motivo": "alta"}
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug)


def _accion(request, slug, csrf, user, conn, fn, ok_msg):
    cli = _cli(conn, slug)
    if cli and _ok_csrf(request, csrf):
        try:
            r = fn(cli)
            malos = _malos(r) if isinstance(r, dict) else ""
            flash(request, ok_msg + (f" Con problemas en: {malos}" if malos else ""), "error" if malos else "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug)


@router.post("/clientes/{slug}/equipo/{uid}/rol")
def rol(request: Request, slug: str, uid: int, rol: str = Form(""), csrf: str = Form(""),
        user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, csrf, user, conn, lambda c: svc.cambiar_rol(conn, request.app.state.cfg, user, c["id"], uid, rol), "Rol actualizado.")


@router.post("/clientes/{slug}/equipo/{uid}/clave")
def clave(request: Request, slug: str, uid: int, csrf: str = Form(""),
          user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _ok_csrf(request, csrf):
        try:
            u, nueva, estados = svc.reiniciar_clave(conn, request.app.state.cfg, user, cli["id"], uid)
            request.session["_clave_equipo"] = {"usuario": u["username"], "clave": nueva, "estados": estados, "motivo": "reinicio"}
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug)


@router.post("/clientes/{slug}/equipo/{uid}/baja")
def baja(request: Request, slug: str, uid: int, csrf: str = Form(""),
         user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, csrf, user, conn, lambda c: svc.activar(conn, request.app.state.cfg, user, c["id"], uid, False), "Usuario dado de baja: pierde el acceso al instante.")


@router.post("/clientes/{slug}/equipo/{uid}/alta")
def alta(request: Request, slug: str, uid: int, csrf: str = Form(""),
         user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, csrf, user, conn, lambda c: svc.activar(conn, request.app.state.cfg, user, c["id"], uid, True), "Usuario activado.")


@router.post("/clientes/{slug}/equipo/{uid}/eliminar")
def eliminar(request: Request, slug: str, uid: int, csrf: str = Form(""),
             user: dict = Depends(require_permiso("equipo.cliente")), conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, csrf, user, conn, lambda c: svc.eliminar(conn, request.app.state.cfg, user, c["id"], uid), "Usuario eliminado.")
