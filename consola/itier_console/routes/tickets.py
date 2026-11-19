"""Tickets de GLPI: listar, ver, crear y asignar desde la consola."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_permiso
from ..services import onboarding
from ..services import tickets as svc

router = APIRouter()


def _clientes_con_entidad(conn: sqlite3.Connection) -> list[dict]:
    return [c for c in onboarding.listar_clientes(conn) if c.get("glpi_entity_id")]


@router.get("/tickets", response_class=HTMLResponse)
def listar(request: Request, cliente: str = "", user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
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
    por_entidad = {str(c["glpi_entity_id"]): c["nombre"] for c in clientes}
    from ..services import canales
    origenes = canales.origenes(conn)
    for t in lista:
        t["canal"] = origenes.get(int(t["id"]), "")
        t["cliente"] = por_entidad.get(str(t.get("entities_id")), "")
    st = lambda t: int(t.get("status", 0) or 0)  # noqa: E731
    resumen = {"total": len(lista), "abiertos": sum(1 for t in lista if st(t) in (1, 2, 3, 4)),
               "nuevos": sum(1 for t in lista if st(t) == 1), "cerrados": sum(1 for t in lista if st(t) in (5, 6)),
               "urgentes": sum(1 for t in lista if int(t.get("priority", 0) or 0) >= 5 and st(t) in (1, 2, 3, 4))}
    return render(request, "tickets_lista.html", tickets=lista, clientes=clientes, sel=cliente, error=error, resumen=resumen)


@router.get("/tickets/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
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
    user: dict = Depends(require_permiso("tickets")),
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
def detalle(request: Request, ticket_id: int, user: dict = Depends(require_permiso("tickets")),
            conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    try:
        t = svc.ver(cfg, ticket_id)
    except AdapterError as e:
        flash(request, f"No se pudo leer el ticket: {e.detalle}", "error")
        return RedirectResponse("/tickets", status_code=303)
    cliente = next((c for c in _clientes_con_entidad(conn) if str(c["glpi_entity_id"]) == str(t.get("entities_id"))), None)
    tecnicos = [dict(r) for r in conn.execute(
        "SELECT username, nombre, glpi_user_id FROM users WHERE client_id IS NULL AND disabled = 0 "
        "AND glpi_user_id IS NOT NULL AND glpi_user_id != '' ORDER BY COALESCE(nombre, username)")]
    mi = conn.execute("SELECT glpi_user_id FROM users WHERE id = ?", (user["uid"],)).fetchone()
    origen = conn.execute("SELECT canal, remitente FROM ticket_origen WHERE ticket_id = ?", (ticket_id,)).fetchone()
    from ..services import horas as horas_svc
    try:
        tiempos = horas_svc.tiempo_de_ticket(cfg, ticket_id)
    except AdapterError:
        tiempos = []
    try:
        conversacion = svc.conversacion(cfg, ticket_id)
    except AdapterError:
        conversacion = []
    t["content"] = svc.texto_plano(t.get("content", ""))
    from ..services import notificaciones
    avisos = notificaciones.destinatarios(conn, ticket_id, cliente["id"] if cliente else None)
    return render(request, "tickets_detalle.html", t=t, cliente=cliente, avisos=avisos, tecnicos=tecnicos, tiempos=tiempos, conversacion=conversacion,
                  total_min=sum(x["minutos"] for x in tiempos),
                  puedo_tomar=bool(mi and mi["glpi_user_id"]), origen=dict(origen) if origen else None)


@router.post("/tickets/{ticket_id}/asignar")
def asignar(
    request: Request,
    ticket_id: int,
    users_id: int = Form(...),
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("tickets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    cfg = request.app.state.cfg
    try:
        svc.asignar(cfg, ticket_id, int(users_id), reemplazar=True)
    except AdapterError as e:
        flash(request, f"No se pudo asignar: {e.detalle}", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    audit.registrar(conn, user["user"], "ticket.asignado", {"ticket_id": ticket_id, "users_id": users_id})
    flash(request, f"Ticket #{ticket_id} asignado (usuario {users_id}).", "ok")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/tomar")
def tomar(request: Request, ticket_id: int, csrf: str = Form(""),
          user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    """El técnico se asigna el ticket a sí mismo (con su usuario de GLPI)."""
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    r = conn.execute("SELECT glpi_user_id FROM users WHERE id = ?", (user["uid"],)).fetchone()
    if not r or not r["glpi_user_id"]:
        flash(request, "Tu usuario no tiene cuenta en GLPI vinculada: pedile al administrador que la sincronice.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    try:
        svc.asignar(request.app.state.cfg, ticket_id, int(r["glpi_user_id"]))
    except (AdapterError, ValueError) as e:
        flash(request, f"No se pudo tomar el ticket: {getattr(e, 'detalle', e)}", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    audit.registrar(conn, user["user"], "ticket.tomado", {"ticket_id": ticket_id})
    flash(request, f"Tomaste el ticket #{ticket_id}.", "ok")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/tiempo")
def registrar_tiempo(request: Request, ticket_id: int, minutos: int = Form(...), nota: str = Form(""), csrf: str = Form(""),
                     user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    """El técnico carga el tiempo que le dedicó al ticket: es la base del reporte de horas por cliente."""
    from ..services import ServiceError
    from ..services import horas as horas_svc
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    r = conn.execute("SELECT glpi_user_id FROM users WHERE id = ?", (user["uid"],)).fetchone()
    if not r or not r["glpi_user_id"]:
        flash(request, "Tu usuario no tiene cuenta en GLPI vinculada: pedile al administrador que la sincronice.", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    try:
        horas_svc.registrar_tiempo(request.app.state.cfg, ticket_id, int(minutos), nota, int(r["glpi_user_id"]))
    except (ServiceError, AdapterError, ValueError) as e:
        flash(request, f"No se pudo registrar el tiempo: {getattr(e, 'mensaje', None) or getattr(e, 'detalle', None) or e}", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    audit.registrar(conn, user["user"], "ticket.tiempo", {"ticket_id": ticket_id, "minutos": int(minutos)})
    flash(request, f"Registraste {int(minutos)} min en el ticket #{ticket_id}.", "ok")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/responder")
def responder(request: Request, ticket_id: int, texto: str = Form(""), privado: str = Form(""), enviar: str = Form(""),
              csrf: str = Form(""), user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    """Agrega una respuesta (o nota interna) al ticket y, si vino por WhatsApp/correo, se la manda al solicitante."""
    from ..services import ajustes
    volver = RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return volver
    texto = texto.strip()
    if not texto:
        flash(request, "Escribí la respuesta.", "error")
        return volver
    if len(texto) > 5000:
        flash(request, "La respuesta es demasiado larga (máximo 5000 caracteres).", "error")
        return volver
    cfg = ajustes.efectiva(conn, request.app.state.cfg)
    r = conn.execute("SELECT glpi_user_id FROM users WHERE id = ?", (user["uid"],)).fetchone()
    es_privado = bool(privado)
    try:
        svc.responder(cfg, ticket_id, texto, es_privado, int(r["glpi_user_id"]) if r and r["glpi_user_id"] else None)
    except AdapterError as e:
        flash(request, f"No se pudo guardar la respuesta: {e.detalle}", "error")
        return volver
    audit.registrar(conn, user["user"], "ticket.nota" if es_privado else "ticket.respuesta", {"ticket_id": ticket_id})
    extra = ""
    if enviar and not es_privado:
        extra, error = _avisar(cfg, conn, ticket_id, texto, user["user"])
        if error:
            flash(request, f"La respuesta quedó en el ticket, pero {error}", "error")
            return volver
    flash(request, ("Nota interna agregada" if es_privado else "Respuesta agregada") + extra + ".", "ok")
    return volver


@router.post("/tickets/{ticket_id}/estado")
def cambiar_estado(request: Request, ticket_id: int, accion: str = Form(""), solucion: str = Form(""), csrf: str = Form(""),
                   enviar: str = Form(""), user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    """Cambia el estado del ticket: en curso, en espera, resolver (con solución), cerrar o reabrir."""
    from ..services import ajustes
    volver = RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return volver
    mensajes = {"en_curso": "El ticket quedó en curso.", "pendiente": "El ticket quedó en espera.", "resolver": "Ticket marcado como resuelto.",
                "cerrar": "Ticket cerrado.", "reabrir": "Ticket reabierto."}
    if accion not in mensajes:
        flash(request, "Acción inválida.", "error")
        return volver
    solucion = solucion.strip()
    if accion == "resolver" and len(solucion) < 3:
        flash(request, "Para resolver el ticket indicá qué se hizo (la solución).", "error")
        return volver
    cfg = ajustes.efectiva(conn, request.app.state.cfg)
    try:
        svc.cambiar_estado(cfg, ticket_id, accion, solucion[:5000])
    except AdapterError as e:
        flash(request, f"No se pudo cambiar el estado: {e.detalle}", "error")
        return volver
    audit.registrar(conn, user["user"], f"ticket.{accion}", {"ticket_id": ticket_id})
    msg = mensajes[accion].rstrip(".")
    if accion == "resolver" and enviar:
        ex, error = _avisar(cfg, conn, ticket_id, f"Resolvimos tu consulta #{ticket_id}: {solucion}\n\nSi todavía hay algo pendiente, respondé por acá.", user["user"])
        msg += ex
        if error:
            msg += f" (no se pudo avisar al cliente: {error})"
    flash(request, msg + ".", "ok")
    return volver


def _avisar(cfg, conn, ticket_id: int, texto: str, actor: str) -> tuple[str, str]:
    """Avisa al cliente (por el canal que corresponda). → («y se avisó a …», «mensaje de error o vacío»)."""
    from ..services import notificaciones
    try:
        t = svc.ver(cfg, ticket_id)
    except AdapterError as e:
        return "", f"no se pudo leer el ticket para avisar: {e.detalle}"
    cli = next((c for c in onboarding.listar_clientes(conn) if str(c.get("glpi_entity_id")) == str(t.get("entities_id"))), None)
    r = notificaciones.notificar(cfg, conn, ticket_id, cli["id"] if cli else None, t.get("name", ""), texto)
    if r["sin_destino"]:
        return "", ("no hay a quién avisar: cargá el correo de los usuarios del portal o de los contactos del cliente (ficha del cliente).")
    if r["errores"] and not r["enviados"]:
        return "", "no se pudo avisar al cliente: " + "; ".join(r["errores"])
    audit.registrar(conn, actor, "ticket.cliente_avisado", {"ticket_id": ticket_id, "a": r["enviados"]})
    msg = " y se avisó a " + ", ".join(r["enviados"])
    if r["errores"]:
        msg += " (falló: " + "; ".join(r["errores"]) + ")"
    return msg, ""
