"""Red del cliente: equipos activos en su LAN (barrido por el proxy de Zabbix) y alertas de conectividad.

  GET  /clientes/{slug}/red                        pantalla
  POST /clientes/{slug}/red/escanear               pide un barrido ya
  POST /clientes/{slug}/red/rango[/{id}/quitar]    rangos a barrer
  POST /clientes/{slug}/red/chequeo[/{id}/quitar]  chequeos de conectividad (Internet, gateway, WAN por SNMP, puerto TCP)
  GET  /clientes/{slug}/red/hosts.csv              exporta los equipos activos
"""

from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import audit, security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, onboarding, red as svc

router = APIRouter()


def _cli(conn, slug):
    return onboarding.obtener_cliente_por_slug(conn, slug)


def _volver(slug: str, ancla: str = "") -> RedirectResponse:
    return RedirectResponse(f"/clientes/{slug}/red{ancla}", status_code=303)


def _csrf(request, csrf) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


@router.get("/clientes/{slug}/red", response_class=HTMLResponse)
def ver(request: Request, slug: str, user: dict = Depends(require_permiso("red")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    return render(request, "red.html", cli=cli, rangos=svc.rangos(conn, cli["id"]), activos=svc.hosts_activos(conn, cfg, cli),
                  est=svc.estado(conn, cfg, cli), tipos=svc.TIPOS, tipos_crear={k: v for k, v in svc.TIPOS.items() if k != "dispositivo"}, tipos_disp=svc.red_tipos.TIPOS, tiene_proxy=bool(cli.get("zbx_proxy_id")))


@router.post("/clientes/{slug}/red/escanear")
def escanear(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("red")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            n = svc.escanear(conn, request.app.state.cfg, cli, user["user"])
            audit.registrar(conn, user["user"], "red.escaneo", {"cliente": cli["nombre"], "rangos": n})
            flash(request, "Barrido iniciado. Los equipos aparecen a medida que el proxy del cliente los encuentra (puede tardar un par de minutos).", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/dispositivo")
def dispositivo(request: Request, slug: str, ip: str = Form(""), nombre: str = Form(""), tipo: str = Form(""), fabricante: str = Form(""),
                notas: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("red")), conn: sqlite3.Connection = Depends(get_db)):
    """Identificación manual de un dispositivo de la red (queda guardada; vacía = vuelve a la automática)."""
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            svc.guardar_dispositivo(conn, cli["id"], user["user"], ip, nombre, tipo, fabricante, notas)
            audit.registrar(conn, user["user"], "red.dispositivo_identificado", {"cliente": cli["nombre"], "ip": ip.strip(), "tipo": tipo})
            flash(request, f"{ip.strip()} identificado.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/monitorear")
def monitorear(request: Request, slug: str, ip: str = Form(""), nombre: str = Form(""), csrf: str = Form(""),
               user: dict = Depends(require_permiso("red.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            svc.monitorear(conn, request.app.state.cfg, cli, user["user"], ip, nombre)
            audit.registrar(conn, user["user"], "red.dispositivo_monitoreado", {"cliente": cli["nombre"], "ip": ip.strip()})
            flash(request, f"{ip.strip()} en monitoreo: si deja de responder se avisa y se abre un ticket.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/a-activos")
def a_activos(request: Request, slug: str, ip: str = Form(""), nombre: str = Form(""), tipo: str = Form(""), fabricante: str = Form(""),
              csrf: str = Form(""), user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            svc.a_activos(conn, cli, user["user"], ip, nombre, tipo, fabricante)
            audit.registrar(conn, user["user"], "red.dispositivo_a_activos", {"cliente": cli["nombre"], "ip": ip.strip(), "tipo": tipo})
            flash(request, f"{ip.strip()} agregado a los activos de {cli['nombre']}.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/rango")
def rango(request: Request, slug: str, nombre: str = Form(""), iprange: str = Form(""), csrf: str = Form(""),
          user: dict = Depends(require_permiso("red.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            svc.agregar_rango(conn, request.app.state.cfg, cli, user["user"], nombre, iprange)
            audit.registrar(conn, user["user"], "red.rango_agregado", {"cliente": cli["nombre"], "rango": iprange.strip()})
            flash(request, "Rango agregado. Pedí un barrido para ver los equipos.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/rango/{rid}/quitar")
def rango_quitar(request: Request, slug: str, rid: int, csrf: str = Form(""),
                 user: dict = Depends(require_permiso("red.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        svc.quitar_rango(conn, request.app.state.cfg, cli, rid)
        audit.registrar(conn, user["user"], "red.rango_quitado", {"cliente": cli["nombre"], "id": rid})
    return _volver(slug, "#activos")


@router.post("/clientes/{slug}/red/chequeo")
def chequeo(request: Request, slug: str, tipo: str = Form(""), nombre: str = Form(""), destino: str = Form(""),
            community: str = Form(""), ifindex: str = Form(""), csrf: str = Form(""),
            user: dict = Depends(require_permiso("red.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        try:
            svc.crear_chequeo(conn, request.app.state.cfg, cli, user["user"], tipo, nombre, destino, {"community": community, "ifindex": ifindex})
            audit.registrar(conn, user["user"], "red.chequeo_creado", {"cliente": cli["nombre"], "tipo": tipo, "destino": destino.strip()})
            flash(request, "Chequeo creado: Zabbix empieza a medirlo en el proxy del cliente y la alerta se activa sola.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(slug, "#alertas")


@router.post("/clientes/{slug}/red/chequeo/{chid}/quitar")
def chequeo_quitar(request: Request, slug: str, chid: int, csrf: str = Form(""),
                   user: dict = Depends(require_permiso("red.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if cli and _csrf(request, csrf):
        svc.quitar_chequeo(conn, request.app.state.cfg, cli, chid)
        audit.registrar(conn, user["user"], "red.chequeo_quitado", {"cliente": cli["nombre"], "id": chid})
        flash(request, "Chequeo eliminado.", "ok")
    return _volver(slug, "#alertas")


@router.get("/clientes/{slug}/red/hosts.csv")
def exportar(request: Request, slug: str, user: dict = Depends(require_permiso("red")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    a = svc.hosts_activos(conn, request.app.state.cfg, cli)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ip", "nombre", "tipo", "origen", "fabricante", "estado", "ultimo_visto_utc", "puertos_abiertos", "ya_monitoreado"])
    for h in a["hosts"]:
        w.writerow([h["ip"], h.get("nombre") or h.get("dns", ""), h["tipo"], "+".join(h["origen"]), h.get("fabricante", ""), h["estado"], h.get("visto_ts", ""), " ".join(str(p) for p in h.get("puertos", [])), "si" if h["monitoreado"] else "no"])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="red-{cli["slug"]}.csv"'})
