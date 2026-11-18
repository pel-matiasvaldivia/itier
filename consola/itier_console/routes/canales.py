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
        from ..services import ajustes
        return svc.procesar_whatsapp(ajustes.efectiva(conn, cfg), conn, body)
    except Exception:  # noqa: BLE001 — no devolver 500: OpenWA reintentaría y duplicaría
        log.exception("Error procesando el webhook de WhatsApp")
        return {"estado": "error"}
    finally:
        conn.close()


@router.get("/bandeja", response_class=HTMLResponse)
def bandeja(request: Request, user: dict = Depends(require_permiso("bandeja")), conn: sqlite3.Connection = Depends(get_db)):
    from ..services import ajustes
    cfg = ajustes.efectiva(conn, request.app.state.cfg)
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


# ---- configuración de los canales (solo administrador) -----------------------------------

def _vista(conn, cfg):
    from ..services import ajustes
    ef = ajustes.efectiva(conn, cfg)
    guardados = set(ajustes.leer(conn, cfg))
    return ef, guardados


@router.get("/canales", response_class=HTMLResponse)
def canales(request: Request, user: dict = Depends(require_permiso("canales")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    ef, guardados = _vista(conn, cfg)
    pend = svc.pendientes(conn)
    base = str(request.base_url).rstrip("/")
    return render(request, "canales.html", ef=ef, guardados=guardados, pendientes=pend,
                  hook_url=(f"{base}/hooks/whatsapp/<token>" if cfg.wa_hook_token else None),
                  wa_hook_ok=cfg.wa_hook_token is not None, smtp_ok=cfg.smtp_host is not None)


def _flash_ok(request, texto):
    flash(request, texto, "ok")
    return RedirectResponse("/canales", status_code=303)


@router.post("/canales/correo")
def correo_guardar(
    request: Request, soporte_email: str = Form(""), imap_host: str = Form(""), imap_port: int = Form(993),
    imap_user: str = Form(""), imap_password: str = Form(""), imap_folder: str = Form("INBOX"),
    imap_tls: str = Form("ssl"), imap_poll_s: int = Form(60), probar: str = Form(""), csrf: str = Form(""),
    user: dict = Depends(require_permiso("canales")), conn: sqlite3.Connection = Depends(get_db),
):
    from .. import audit
    from ..services import ajustes
    cfg = request.app.state.cfg
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/canales", status_code=303)
    if imap_tls not in ("ssl", "starttls", "no"):
        imap_tls = "ssl"
    if not 1 <= imap_port <= 65535 or not 15 <= imap_poll_s <= 3600:
        flash(request, "Puerto (1-65535) o frecuencia (15-3600 s) fuera de rango.", "error")
        return RedirectResponse("/canales", status_code=303)
    if soporte_email.strip() and not svc.normalizar_email(soporte_email):
        flash(request, "La casilla de soporte no es un correo válido.", "error")
        return RedirectResponse("/canales", status_code=303)
    vals = {"soporte_email": soporte_email, "imap_host": imap_host, "imap_port": imap_port, "imap_user": imap_user,
            "imap_folder": imap_folder or "INBOX", "imap_tls": imap_tls, "imap_poll_s": imap_poll_s}
    if imap_password.strip():                        # vacío = conservar la guardada
        vals["imap_password"] = imap_password
    ajustes.guardar(conn, cfg, user["user"], vals)
    audit.registrar(conn, user["user"], "canal.correo_configurado",
                    {"host": imap_host, "usuario": imap_user, "puerto": imap_port, "tls": imap_tls,
                     "clave_cambiada": bool(imap_password.strip())})
    if probar:
        try:
            return _flash_ok(request, "Guardado. " + svc.probar_imap(ajustes.efectiva(conn, cfg)))
        except ServiceError as e:
            flash(request, "Guardado, pero la prueba falló: " + e.mensaje, "error")
            return RedirectResponse("/canales", status_code=303)
    return _flash_ok(request, "Configuración del correo guardada. Se aplica en la próxima lectura del buzón (menos de un minuto).")


@router.post("/canales/correo/borrar-clave")
def correo_borrar_clave(request: Request, csrf: str = Form(""), user: dict = Depends(require_permiso("canales")),
                        conn: sqlite3.Connection = Depends(get_db)):
    from .. import audit
    from ..services import ajustes
    if security.verificar_csrf(request.session, csrf):
        ajustes.guardar(conn, request.app.state.cfg, user["user"], {"imap_password": ""})
        audit.registrar(conn, user["user"], "canal.correo_clave_borrada", {})
        flash(request, "Contraseña del buzón borrada: la lectura de correo queda apagada.", "ok")
    return RedirectResponse("/canales", status_code=303)


@router.post("/canales/whatsapp")
def whatsapp_guardar(request: Request, wa_api_url: str = Form(""), wa_api_key: str = Form(""), wa_numero: str = Form(""),
                     csrf: str = Form(""), user: dict = Depends(require_permiso("canales")),
                     conn: sqlite3.Connection = Depends(get_db)):
    from .. import audit
    from ..services import ajustes
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/canales", status_code=303)
    if wa_api_url.strip() and not wa_api_url.strip().startswith(("http://", "https://")):
        flash(request, "La URL de OpenWA debe empezar con http:// o https://", "error")
        return RedirectResponse("/canales", status_code=303)
    vals = {"wa_api_url": wa_api_url, "wa_numero": wa_numero}
    if wa_api_key.strip():
        vals["wa_api_key"] = wa_api_key
    ajustes.guardar(conn, request.app.state.cfg, user["user"], vals)
    audit.registrar(conn, user["user"], "canal.whatsapp_configurado",
                    {"api_url": wa_api_url, "numero": wa_numero, "clave_cambiada": bool(wa_api_key.strip())})
    return _flash_ok(request, "Configuración de WhatsApp guardada.")


@router.get("/canales/whatsapp/estado")
def whatsapp_estado(request: Request, user: dict = Depends(require_permiso("canales")),
                    conn: sqlite3.Connection = Depends(get_db)):
    """JSON para la pantalla: estado de la sesión y el QR (se consulta cada pocos segundos)."""
    from ..services import ajustes, whatsapp
    return JSONResponse(whatsapp.estado(ajustes.efectiva(conn, request.app.state.cfg)),
                        headers={"Cache-Control": "no-store"})
