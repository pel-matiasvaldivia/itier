"""Instaladores por cliente: página del operador + endpoints públicos por token.

  Operador (sesión + 2FA):
    GET  /clientes/{slug}/instaladores                  página con todo preconfigurado
    POST /clientes/{slug}/instaladores/mesh-token       guarda el token del nodo mesh
    POST /clientes/{slug}/instaladores/enlace           genera el comando de una línea
    POST /clientes/{slug}/instaladores/enlace/{id}/revocar
    GET  /clientes/{slug}/instaladores/appliance.sh     descarga el instalador Linux
    GET  /clientes/{slug}/instaladores/windows.ps1      descarga el instalador Windows

  Appliance (sin sesión; el token del enlace es la credencial):
    GET  /enrolar/{token}/install.sh                    el instalador (consume un uso)
    POST /enrolar/{token}/reporte                       el appliance avisa cómo quedó
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .. import audit, security
from ..deps import flash, get_db, render, require_user
from ..services import ServiceError, enrolamiento, onboarding

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _base_publica(request: Request) -> str:
    cfg = request.app.state.cfg
    return cfg.console_public_url or str(request.base_url).rstrip("/")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _cliente(request: Request, conn: sqlite3.Connection, slug: str) -> dict | None:
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
    return cli


# ── Operador ─────────────────────────────────────────────────────────────────

@router.get("/clientes/{slug}/instaladores", response_class=HTMLResponse)
def pagina(request: Request, slug: str, user: dict = Depends(require_user),
           conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    # El token en claro se muestra UNA sola vez, recién creado.
    nuevo = request.session.pop("_enlace_nuevo", None)
    comando = None
    if nuevo and nuevo.get("slug") == slug:
        comando = f"curl -fsSL {_base_publica(request)}/enrolar/{nuevo['token']}/install.sh | sudo bash"
    return render(
        request, "instaladores.html", cli=cli, comando=comando,
        faltantes=enrolamiento.faltantes(cfg, cli),
        enlaces=enrolamiento.listar_enlaces(conn, cli["id"]),
        ttl_h=cfg.enroll_ttl_h, max_usos=cfg.enroll_max_usos,
    )


@router.post("/clientes/{slug}/instaladores/mesh-token")
def mesh_token(request: Request, slug: str, mesh_token: str = Form(""), csrf: str = Form(""),
               user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    destino = RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return destino
    try:
        enrolamiento.guardar_mesh_token(conn, user["user"], cli, mesh_token)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return destino
    flash(request, "Token del nodo mesh guardado.", "ok")
    return destino


@router.post("/clientes/{slug}/instaladores/enlace")
def crear_enlace(request: Request, slug: str, csrf: str = Form(""),
                 user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    destino = RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return destino
    try:
        token = enrolamiento.crear_enlace(conn, request.app.state.cfg, user["user"], cli)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return destino
    request.session["_enlace_nuevo"] = {"slug": slug, "token": token}
    flash(request, "Comando de instalación generado. Copialo ahora: no se vuelve a mostrar.", "ok")
    return destino


@router.post("/clientes/{slug}/instaladores/enlace/{tid}/revocar")
def revocar(request: Request, slug: str, tid: int, csrf: str = Form(""),
            user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    if security.verificar_csrf(request.session, csrf):
        enrolamiento.revocar_enlace(conn, user["user"], cli, tid)
        flash(request, "Enlace revocado.", "ok")
    else:
        flash(request, "Sesión expirada, reintentá.", "error")
    return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)


def _descarga(cuerpo: str, nombre: str) -> Response:
    return Response(cuerpo, media_type="application/octet-stream",
                    headers={**_NO_CACHE, "Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/clientes/{slug}/instaladores/appliance.sh")
def bajar_appliance(request: Request, slug: str, user: dict = Depends(require_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    try:
        falta = enrolamiento.faltantes(cfg, cli)
        if falta:
            raise ServiceError("No se puede generar el instalador: " + "; ".join(falta) + ".")
        # Descarga manual: sin token, así que el instalador no reporta a la consola.
        script = enrolamiento.instalador_linux(cfg, cli)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    audit.registrar(conn, user["user"], "enrolamiento.instalador.descargado",
                    {"cliente": cli["nombre"], "tipo": "appliance", "via": "consola"})
    return _descarga(script, f"itier-{slug}-appliance.sh")


@router.get("/clientes/{slug}/instaladores/windows.ps1")
def bajar_windows(request: Request, slug: str, user: dict = Depends(require_user),
                  conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    try:
        script = enrolamiento.instalador_windows(request.app.state.cfg, cli)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    audit.registrar(conn, user["user"], "enrolamiento.instalador.descargado",
                    {"cliente": cli["nombre"], "tipo": "windows", "via": "consola"})
    # CRLF: lo esperan las herramientas de Windows.
    return _descarga(script.replace("\r\n", "\n").replace("\n", "\r\n"), f"itier-{slug}-windows.ps1")


# ── Appliance (público, por token) ───────────────────────────────────────────

_RECHAZO = "echo '[iTier] Enlace de instalacion invalido, vencido o agotado. Genera uno nuevo en la consola.' >&2; exit 1\n"


@router.get("/enrolar/{token}/install.sh")
def instalar(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    cli = enrolamiento.canjear(conn, token, _ip(request))
    if not cli:
        # 404 + cuerpo que falla limpio si igual lo pasan por bash.
        return PlainTextResponse(_RECHAZO, status_code=404, headers=_NO_CACHE)
    try:
        script = enrolamiento.instalador_linux(
            cfg, cli, url_reporte=f"{_base_publica(request)}/enrolar/{token}/reporte")
    except ServiceError:
        return PlainTextResponse(_RECHAZO, status_code=404, headers=_NO_CACHE)
    return PlainTextResponse(script, media_type="text/x-shellscript", headers=_NO_CACHE)


@router.post("/enrolar/{token}/reporte")
def reporte(request: Request, token: str, estado: str = Form(""), host: str = Form(""),
            ip: str = Form(""), conn: sqlite3.Connection = Depends(get_db)):
    ok = enrolamiento.registrar_reporte(conn, token, _ip(request), estado, host, ip)
    return PlainTextResponse("ok\n" if ok else "rechazado\n", status_code=200 if ok else 404, headers=_NO_CACHE)
