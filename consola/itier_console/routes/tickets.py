"""Tickets de GLPI: listar, ver, crear y asignar desde la consola."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_user
from ..services import onboarding
from ..services import tickets as svc

router = APIRouter()


def _clientes_con_entidad(conn: sqlite3.Connection) -> list[dict]:
    return [c for c in onboarding.listar_clientes(conn) if c.get("glpi_entity_id")]


@router.get("/tickets", response_class=HTMLResponse)
def listar(request: Request, cliente: str = "", user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    clientes = _clientes_con_entidad(conn)
    entity_id = None
    sel = None
    if cliente:
        sel = onboarding.obtener_cliente_por_slug(conn, cliente)
        if sel and sel.get("glpi_entity_id"):
            try:
                entity_id = int(sel["glpi_entity_id"])
            except (TypeError, ValueError):
                entity_id = None
    error, lista = None, []
    try:
        lista = svc.listar(cfg, entity_id=entity_id)
    except AdapterError as e:
        error = e.detalle
    return render(request, "tickets_lista.html", tickets=lista, clientes=clientes, sel=cliente, error=error)


@router.get("/tickets/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    clientes = _clientes_con_entidad(conn)
    return render(request, "tickets_nuevo.html", clientes=clientes)


@router.post("/tickets/nuevo")
def nuevo_post(
    request: Request,
    cliente: str = Form(...),
    titulo: str = Form(...),
    contenido: str = Form(""),
    prioridad: int = Form(3),
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/tickets/nuevo", status_code=303)
    cli = onboarding.obtener_cliente_por_slug(conn, cliente)
    if not cli or not cli.get("glpi_entity_id"):
        flash(request, "Elegí un cliente con entidad de GLPI válida.", "error")
        return RedirectResponse("/tickets/nuevo", status_code=303)
    cfg = request.app.state.cfg
    try:
        tid = svc.crear(cfg, titulo.strip(), contenido.strip(), int(cli["glpi_entity_id"]), int(prioridad))
    except AdapterError as e:
        flash(request, f"No se pudo crear el ticket: {e.detalle}", "error")
        return RedirectResponse("/tickets/nuevo", status_code=303)
    audit.registrar(conn, user["user"], "ticket.creado",
                    {"ticket_id": tid, "cliente": cli["nombre"], "titulo": titulo.strip()})
    flash(request, f"Ticket #{tid} creado.", "ok")
    return RedirectResponse(f"/tickets/{tid}", status_code=303)


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def detalle(request: Request, ticket_id: int, user: dict = Depends(require_user)):
    cfg = request.app.state.cfg
    try:
        t = svc.ver(cfg, ticket_id)
    except AdapterError as e:
        flash(request, f"No se pudo leer el ticket: {e.detalle}", "error")
        return RedirectResponse("/tickets", status_code=303)
    return render(request, "tickets_detalle.html", t=t)


@router.post("/tickets/{ticket_id}/asignar")
def asignar(
    request: Request,
    ticket_id: int,
    users_id: int = Form(...),
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    cfg = request.app.state.cfg
    try:
        svc.asignar(cfg, ticket_id, int(users_id))
    except AdapterError as e:
        flash(request, f"No se pudo asignar: {e.detalle}", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    audit.registrar(conn, user["user"], "ticket.asignado", {"ticket_id": ticket_id, "users_id": users_id})
    flash(request, f"Ticket #{ticket_id} asignado (usuario {users_id}).", "ok")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
