"""Clientes: listado, alta (onboarding) y detalle con el bundle de enrolamiento."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import security
from ..deps import flash, get_db, render, require_user
from ..services import ServiceError
from ..services import onboarding

router = APIRouter()


@router.get("/clientes", response_class=HTMLResponse)
def listar(request: Request, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    clientes = onboarding.listar_clientes(conn)
    return render(request, "clientes_lista.html", clientes=clientes)


@router.get("/clientes/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, user: dict = Depends(require_user)):
    return render(request, "clientes_nuevo.html")


@router.post("/clientes/nuevo")
def nuevo_post(
    request: Request,
    nombre: str = Form(...),
    notas: str = Form(""),
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/clientes/nuevo", status_code=303)
    cfg = request.app.state.cfg
    try:
        cli = onboarding.alta_cliente(conn, cfg, user["user"], nombre, notas)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        if e.cliente_id:
            # Alta parcial: igual existe el registro, llevamos al detalle.
            cli = onboarding.obtener_cliente(conn, e.cliente_id)
            return RedirectResponse(f"/clientes/{cli['slug']}", status_code=303)
        return RedirectResponse("/clientes/nuevo", status_code=303)
    flash(request, f"Cliente «{cli['nombre']}» dado de alta. Copiá el bundle de enrolamiento.", "ok")
    return RedirectResponse(f"/clientes/{cli['slug']}", status_code=303)


@router.get("/clientes/{slug}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    bundle = onboarding.bundle_env(cfg, cli)
    return render(request, "clientes_detalle.html", cli=cli, bundle=bundle)


@router.post("/clientes/{slug}/reintentar")
def reintentar(
    request: Request,
    slug: str,
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        cli = onboarding.reprovisionar(conn, request.app.state.cfg, user["user"], cli["id"])
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    flash(request, f"«{cli['nombre']}» quedó aprovisionado.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/eliminar")
def eliminar(
    request: Request,
    slug: str,
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        onboarding.eliminar_cliente(conn, user["user"], cli["id"])
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    flash(request, f"Registro de «{cli['nombre']}» eliminado (GLPI/Zabbix no se tocaron).", "ok")
    return RedirectResponse("/clientes", status_code=303)
