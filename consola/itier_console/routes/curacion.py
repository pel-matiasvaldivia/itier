"""Curación de la CMDB: criticidad de los CIs de un cliente (alimenta la autonomía)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_user
from ..services import ServiceError, curacion, onboarding

router = APIRouter()


@router.get("/clientes/{slug}/criticidad", response_class=HTMLResponse)
def panel(request: Request, slug: str, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    cis, error = [], None
    try:
        cis = curacion.listar(request.app.state.cfg, cli)
    except AdapterError as e:
        error = e.detalle
    return render(request, "criticidad.html", cli=cli, cis=cis, niveles=curacion.NIVELES, error=error)


@router.post("/clientes/{slug}/criticidad")
def fijar(
    request: Request,
    slug: str,
    itemtype: str = Form(...),
    items_id: str = Form(...),
    nombre: str = Form(""),
    criticidad: str = Form(...),
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
        return RedirectResponse(f"/clientes/{slug}/criticidad", status_code=303)
    try:
        r = curacion.fijar(request.app.state.cfg, cli, itemtype, items_id, criticidad)
    except (ServiceError, AdapterError) as e:
        flash(request, f"No se pudo fijar: {getattr(e, 'mensaje', getattr(e, 'detalle', str(e)))}", "error")
        return RedirectResponse(f"/clientes/{slug}/criticidad", status_code=303)
    audit.registrar(conn, user["user"], "criticidad.fijada",
                    {"cliente": cli["nombre"], "ci": nombre or items_id, "criticidad": r.get("criticidad")})
    flash(request, f"«{nombre or items_id}» → criticidad {r.get('criticidad')}.", "ok")
    return RedirectResponse(f"/clientes/{slug}/criticidad", status_code=303)
