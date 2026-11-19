"""Portal del cliente: superficie acotada por tenant (ver docs/08).

Un usuario de portal está atado a un `client_id`. TODO el acceso a datos se
scopea por la entidad de GLPI y el grupo de Zabbix de ese cliente: nunca ve otros
clientes ni las rutas del operador. El cliente no recibe credenciales de
GLPI/Zabbix; el portal es el mediador de confianza del lado del servidor.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_cliente
from ..services import agents, canales as canales_svc, equipos as equipos_svc, notificaciones, onboarding
from ..services import tickets as tickets_svc

router = APIRouter(prefix="/portal")


def _cliente(conn: sqlite3.Connection, user: dict) -> dict | None:
    cid = user.get("client_id")
    return onboarding.obtener_cliente(conn, int(cid)) if cid else None


def _entity(cli: dict) -> int | None:
    try:
        return int(cli["glpi_entity_id"]) if cli.get("glpi_entity_id") else None
    except (TypeError, ValueError):
        return None


# ── Inicio ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def inicio(request: Request, user: dict = Depends(require_cliente), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    cfg = request.app.state.cfg
    entity = _entity(cli)

    abiertos = None
    hosts_total = hosts_ok = None   # equipos (todas las fuentes) y cuántos están en línea
    try:
        if entity is not None:
            tickets = tickets_svc.listar(cfg, entity_id=entity, limit=100)
            abiertos = sum(1 for t in tickets if int(t.get("status", 0) or 0) in (1, 2, 3, 4))
    except AdapterError:
        pass
    datos = equipos_svc.listar(cfg, cli)
    if len(datos["notas"]) < 3:   # al menos una fuente respondió
        hosts_total = len(datos["equipos"])
        hosts_ok = sum(1 for e in datos["equipos"] if e["en_linea"] or e["zbx"] == "1")

    return render(request, "portal_inicio.html", cli=cli,
                  abiertos=abiertos, hosts_total=hosts_total, hosts_ok=hosts_ok)


# ── Mis tickets ─────────────────────────────────────────────────────────────

@router.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, user: dict = Depends(require_cliente), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    entity = _entity(cli)
    lista, error = [], None
    if entity is not None:
        try:
            lista = tickets_svc.listar(request.app.state.cfg, entity_id=entity, limit=100)
        except AdapterError as e:
            error = e.detalle
    return render(request, "portal_tickets.html", cli=cli, tickets=lista, error=error)


@router.get("/tickets/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, user: dict = Depends(require_cliente), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    return render(request, "portal_ticket_nuevo.html", cli=cli)


@router.post("/tickets/nuevo")
def nuevo_post(
    request: Request,
    titulo: str = Form(...),
    contenido: str = Form(""),
    prioridad: int = Form(3),
    csrf: str = Form(""),
    user: dict = Depends(require_cliente),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/portal/tickets/nuevo", status_code=303)
    entity = _entity(cli)
    if entity is None:
        flash(request, "Tu empresa no tiene entidad de GLPI configurada. Avisá a soporte.", "error")
        return RedirectResponse("/portal/tickets", status_code=303)
    try:
        # La entidad se FUERZA a la del cliente: nunca se toma del formulario.
        tid = tickets_svc.crear(request.app.state.cfg, titulo.strip(), contenido.strip(), entity, int(prioridad))
    except AdapterError as e:
        flash(request, f"No se pudo crear el ticket: {e.detalle}", "error")
        return RedirectResponse("/portal/tickets/nuevo", status_code=303)
    # Se anota de dónde vino: a ese usuario se le avisa cuando soporte responde.
    conn.execute("INSERT OR REPLACE INTO ticket_origen(ticket_id, client_id, canal, remitente, created_at) VALUES (?,?,?,?,?)",
                 (tid, cli["id"], "portal", user["user"], canales_svc.ahora()))
    conn.commit()
    audit.registrar(conn, f"portal:{user['user']}", "portal.ticket.creado",
                    {"cliente": cli["nombre"], "ticket_id": tid, "titulo": titulo.strip()})
    flash(request, f"Ticket #{tid} abierto. Te vamos avisando el avance.", "ok")
    return RedirectResponse(f"/portal/tickets/{tid}", status_code=303)


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ver(request: Request, ticket_id: int, user: dict = Depends(require_cliente), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    entity = _entity(cli)
    try:
        t = tickets_svc.ver(request.app.state.cfg, ticket_id)
    except AdapterError:
        flash(request, "Ticket no encontrado.", "error")
        return RedirectResponse("/portal/tickets", status_code=303)
    # Aislamiento: el ticket DEBE ser de la entidad del cliente (evita IDOR).
    if entity is None or str(t.get("entities_id")) != str(entity):
        flash(request, "Ticket no encontrado.", "error")
        return RedirectResponse("/portal/tickets", status_code=303)
    t["content"] = tickets_svc.texto_plano(t.get("content", ""))
    try:
        mensajes = [m for m in tickets_svc.conversacion(request.app.state.cfg, ticket_id) if not m["privado"]]
    except AdapterError:
        mensajes = []
    for m in mensajes:
        m["es_cliente"] = notificaciones.es_del_cliente(m["content"])
        m["content"] = _limpiar_marca(m["content"])
    return render(request, "portal_ticket_detalle.html", cli=cli, t=t, mensajes=mensajes)


def _limpiar_marca(texto: str) -> str:
    """Quita la marca técnica del mensaje del cliente («Cliente · Nombre:» o el pie de WhatsApp/correo)."""
    t = texto.strip()
    if t.startswith("Cliente ·") and "\n" in t:
        t = t.split("\n", 1)[1].strip()
    if "\n\n— Recibido por " in t:
        t = t.split("\n\n— Recibido por ", 1)[0].strip()
    return t


@router.post("/tickets/{ticket_id}/responder")
def responder(request: Request, ticket_id: int, texto: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_cliente),
              conn: sqlite3.Connection = Depends(get_db)):
    """El cliente agrega información a un ticket suyo. Solo tickets de SU entidad."""
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    volver = RedirectResponse(f"/portal/tickets/{ticket_id}", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return volver
    texto = texto.strip()
    if not texto or len(texto) > 5000:
        flash(request, "Escribí tu mensaje (hasta 5000 caracteres).", "error")
        return volver
    cfg = request.app.state.cfg
    entity = _entity(cli)
    try:
        t = tickets_svc.ver(cfg, ticket_id)
    except AdapterError:
        flash(request, "Ticket no encontrado.", "error")
        return RedirectResponse("/portal/tickets", status_code=303)
    if entity is None or str(t.get("entities_id")) != str(entity):   # evita IDOR
        flash(request, "Ticket no encontrado.", "error")
        return RedirectResponse("/portal/tickets", status_code=303)
    if int(t.get("status", 0) or 0) == 6:
        flash(request, "El ticket está cerrado: abrí uno nuevo si el problema sigue.", "error")
        return volver
    quien = (conn.execute("SELECT nombre FROM users WHERE id = ?", (user["uid"],)).fetchone() or {"nombre": None})["nombre"] or user["user"]
    try:
        tickets_svc.responder(cfg, ticket_id, f"Cliente · {quien}:\n{texto}", False, None)
        if int(t.get("status", 0) or 0) == 5:                      # respondió a un ticket «resuelto»: sigue el problema
            tickets_svc.cambiar_estado(cfg, ticket_id, "reabrir")
    except AdapterError as e:
        flash(request, f"No se pudo enviar tu mensaje: {e.detalle}", "error")
        return volver
    audit.registrar(conn, f"portal:{user['user']}", "portal.ticket.respuesta", {"cliente": cli["nombre"], "ticket_id": ticket_id})
    notificaciones.avisar_a_soporte(cfg, ticket_id, t.get("name", ""), cli["nombre"], quien, texto)
    flash(request, "Tu mensaje fue enviado a soporte.", "ok")
    return volver


# ── Mis equipos ─────────────────────────────────────────────────────────────

@router.get("/equipos", response_class=HTMLResponse)
def equipos(request: Request, user: dict = Depends(require_cliente), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(conn, user)
    if not cli:
        return render(request, "portal_sin_cliente.html")
    datos = equipos_svc.listar(request.app.state.cfg, cli)
    return render(request, "portal_equipos.html", cli=cli, proxy=datos["proxy"], equipos=datos["equipos"],
                  notas=datos["notas"])
