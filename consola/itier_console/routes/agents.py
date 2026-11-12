"""Agentes de Zabbix de un cliente: estado del proxy y vinculación de hosts."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_user
from ..services import ServiceError, agents, onboarding

router = APIRouter()


@router.get("/clientes/{slug}/agentes", response_class=HTMLResponse)
def panel(request: Request, slug: str, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    datos, error = {"proxy": None, "hosts": []}, None
    try:
        datos = agents.panel(cfg, cli)
    except AdapterError as e:
        error = e.detalle
    return render(request, "agentes.html", cli=cli, proxy=datos["proxy"], hosts=datos["hosts"], error=error)


@router.post("/clientes/{slug}/agentes/vincular")
def vincular(
    request: Request,
    slug: str,
    hostid: str = Form(...),
    host_label: str = Form(""),
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
        return RedirectResponse(f"/clientes/{slug}/agentes", status_code=303)
    cfg = request.app.state.cfg
    try:
        agents.vincular(cfg, cli, hostid)
    except (ServiceError, AdapterError) as e:
        flash(request, f"No se pudo vincular: {getattr(e, 'mensaje', getattr(e, 'detalle', str(e)))}", "error")
        return RedirectResponse(f"/clientes/{slug}/agentes", status_code=303)
    audit.registrar(conn, user["user"], "agente.vinculado",
                    {"cliente": cli["nombre"], "hostid": hostid, "host": host_label,
                     "proxy": cli["zbx_proxy_name"]})
    flash(request, f"Host «{host_label or hostid}» vinculado al proxy de {cli['nombre']}.", "ok")
    return RedirectResponse(f"/clientes/{slug}/agentes", status_code=303)
