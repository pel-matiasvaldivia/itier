"""Canales de entrada: webhook de WhatsApp (OpenWA), bandeja de triage, contactos y dominios del cliente."""

from __future__ import annotations

import hmac
import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, onboarding
from ..services import canales as svc

log = logging.getLogger("itier.canales")
router = APIRouter()

MAX_WEBHOOK = 64 * 1024


@router.post("/hooks/whatsapp/{token}")
async def hook_whatsapp(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    """Lo llama OpenWA por cada mensaje. La autenticación es el token en la URL (OpenWA no firma)."""
    cfg = request.app.state.cfg
    if not cfg.wa_hook_token or not hmac.compare_digest(token.encode(), cfg.wa_hook_token.encode()):
        raise HTTPException(status_code=404)
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK:
        return JSONResponse({"ok": False, "motivo": "demasiado grande"}, status_code=413)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "motivo": "json inválido"}, status_code=400)
    # El trabajo toca GLPI (bloqueante): fuera del loop de eventos.
    from starlette.concurrency import run_in_threadpool
    res = await run_in_threadpool(_procesar, cfg, body)
    return {"ok": True, "estado": res.get("estado")}


def _procesar(cfg, body):
    from .. import db as db_mod
    conn = db_mod.connect(cfg.db_path)
    try:
        return svc.procesar_whatsapp(cfg, conn, body)
    except Exception:  # noqa: BLE001 — no devolver 500: OpenWA reintentaría y duplicaría
        log.exception("Error procesando el webhook de WhatsApp")
        return {"estado": "error"}
    finally:
        conn.close()


@router.get("/bandeja", response_class=HTMLResponse)
def bandeja(request: Request, user: dict = Depends(require_permiso("bandeja")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    pend = [dict(r) for r in conn.execute(
        "SELECT * FROM entrantes WHERE estado IN ('sin_cliente','revisar','error') ORDER BY id DESC LIMIT 100")]
    hist = [dict(r) for r in conn.execute(
        "SELECT e.*, c.nombre AS cliente FROM entrantes e LEFT JOIN clients c ON c.id = e.client_id "
        "WHERE e.estado NOT IN ('sin_cliente','revisar','error') ORDER BY e.id DESC LIMIT 50")]
    clientes = [c for c in onboarding.listar_clientes(conn) if c.get("glpi_entity_id")]
    return render(request, "bandeja.html", pend=pend, hist=hist, clientes=clientes,
                  imap_ok=cfg.imap_configurado, wa_ok=cfg.wa_configurado,
                  soporte_email=cfg.soporte_email, wa_numero=cfg.wa_numero)


@router.post("/bandeja/{eid}/asignar")
def asignar(request: Request, eid: int, cliente: str = Form(...), recordar: str = Form(""), csrf: str = Form(""),
            user: dict = Depends(require_permiso("bandeja")), conn: sqlite3.Connection = Depends(get_db)):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/bandeja", status_code=303)
    cli = onboarding.obtener_cliente_por_slug(conn, cliente)
    if not cli:
        flash(request, "Elegí un cliente.", "error")
        return RedirectResponse("/bandeja", status_code=303)
    try:
        r = svc.asignar_entrante(request.app.state.cfg, conn, eid, cli["id"], user["user"], recordar=bool(recordar))
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse("/bandeja", status_code=303)
    flash(request, f"Ticket #{r['ticket_id']} {'creado' if r['accion'] == 'ticket' else 'actualizado'} para {cli['nombre']}.", "ok")
    return RedirectResponse(f"/tickets/{r['ticket_id']}", status_code=303)


@router.post("/bandeja/{eid}/descartar")
def descartar(request: Request, eid: int, csrf: str = Form(""),
              user: dict = Depends(require_permiso("bandeja")), conn: sqlite3.Connection = Depends(get_db)):
    if security.verificar_csrf(request.session, csrf):
        svc.descartar_entrante(conn, eid, user["user"])
        flash(request, "Mensaje descartado.", "ok")
    return RedirectResponse("/bandeja", status_code=303)


# ---- dominios y contactos de un cliente -------------------------------------------------

def _cli_o_volver(request, conn, slug):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
    return cli


@router.post("/clientes/{slug}/dominios")
def dominio_agregar(request: Request, slug: str, dominio: str = Form(...), csrf: str = Form(""),
                    user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli_o_volver(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
    else:
        try:
            d = svc.agregar_dominio(conn, cli["id"], dominio, user["user"])
            flash(request, f"Dominio «{d}» asociado: los correos de @{d} abren tickets de {cli['nombre']}.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/dominios/{did}/quitar")
def dominio_quitar(request: Request, slug: str, did: int, csrf: str = Form(""),
                   user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli_o_volver(request, conn, slug)
    if cli and security.verificar_csrf(request.session, csrf):
        svc.quitar_dominio(conn, cli["id"], did, user["user"])
        flash(request, "Dominio quitado.", "ok")
    return RedirectResponse(f"/clientes/{slug}" if cli else "/clientes", status_code=303)


@router.post("/clientes/{slug}/contactos")
def contacto_agregar(request: Request, slug: str, nombre: str = Form(""), email: str = Form(""), whatsapp: str = Form(""),
                     csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
                     conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli_o_volver(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
    else:
        try:
            svc.agregar_contacto(conn, cli["id"], nombre, email, whatsapp, user["user"])
            flash(request, "Contacto agregado.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/contactos/{cid}/quitar")
def contacto_quitar(request: Request, slug: str, cid: int, csrf: str = Form(""),
                    user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli_o_volver(request, conn, slug)
    if cli and security.verificar_csrf(request.session, csrf):
        svc.quitar_contacto(conn, cli["id"], cid, user["user"])
        flash(request, "Contacto quitado.", "ok")
    return RedirectResponse(f"/clientes/{slug}" if cli else "/clientes", status_code=303)
