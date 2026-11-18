"""Reporte de horas por cliente y rentabilidad (solo administrador)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, ajustes, horas

router = APIRouter()


def _volver(mes: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"/reportes/horas?mes={horas.mes_valido(mes)}", status_code=303)


@router.get("/reportes/horas", response_class=HTMLResponse)
def ver(request: Request, mes: str = "", user: dict = Depends(require_permiso("reportes")),
        conn: sqlite3.Connection = Depends(get_db)):
    cfg = ajustes.efectiva(conn, request.app.state.cfg)
    mes = horas.mes_valido(mes)
    error, rep = None, None
    try:
        rep = horas.reporte(conn, cfg, mes)
    except AdapterError as e:
        error = e.detalle
    operadores = [dict(r) for r in conn.execute(
        "SELECT username, nombre, rol FROM users WHERE client_id IS NULL AND disabled = 0 ORDER BY username")]
    clientes = [dict(r) for r in conn.execute("SELECT slug, nombre, facturacion_ars FROM clients ORDER BY nombre")]
    return render(request, "reporte_horas.html", mes=mes, rep=rep, error=error, cfg_ef=cfg,
                  tarifas=horas.tarifas(conn), operadores=operadores, clientes=clientes)


@router.post("/reportes/parametros")
def parametros(request: Request, fx_ars_usd: str = Form(""), costo_plataforma_usd: str = Form("60"), mes: str = Form(""),
               csrf: str = Form(""), user: dict = Depends(require_permiso("reportes")),
               conn: sqlite3.Connection = Depends(get_db)):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return _volver(mes)
    try:
        fx = horas.numero_ar(fx_ars_usd) if fx_ars_usd.strip() else None
        plat = horas.numero_ar(costo_plataforma_usd) if costo_plataforma_usd.strip() else None
        if (fx is not None and fx <= 0) or (plat is not None and plat < 0):
            raise ValueError
    except ValueError:
        flash(request, "Cotización y costo de plataforma tienen que ser números válidos.", "error")
        return _volver(mes)
    ajustes.guardar(conn, request.app.state.cfg, user["user"],
                    {"fx_ars_usd": fx, "costo_plataforma_usd": plat, "fx_fecha": None if fx is None else "manual"})
    audit.registrar(conn, user["user"], "reporte.parametros", {"fx": fx, "plataforma_usd": plat})
    flash(request, "Parámetros guardados.", "ok")
    return _volver(mes)


@router.post("/reportes/fx-bcra")
def fx_bcra(request: Request, mes: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("reportes")),
            conn: sqlite3.Connection = Depends(get_db)):
    if security.verificar_csrf(request.session, csrf):
        try:
            valor, fecha = horas.cotizacion_bcra()
            ajustes.guardar(conn, request.app.state.cfg, user["user"], {"fx_ars_usd": valor, "fx_fecha": fecha})
            audit.registrar(conn, user["user"], "reporte.fx_bcra", {"fx": valor, "fecha": fecha})
            flash(request, f"Cotización BCRA del {fecha}: ${valor:,.2f} por USD.".replace(",", "X").replace(".", ",").replace("X", "."), "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return _volver(mes)


@router.post("/reportes/tarifas")
async def tarifas(request: Request, user: dict = Depends(require_permiso("reportes")),
                  conn: sqlite3.Connection = Depends(get_db)):
    f = await request.form()
    if not security.verificar_csrf(request.session, str(f.get("csrf", ""))):
        flash(request, "Sesión expirada, reintentá.", "error")
        return _volver(str(f.get("mes", "")))
    valores = {k[len("t_"):]: str(v) for k, v in f.items() if k.startswith("t_")}
    try:
        horas.guardar_tarifas(conn, valores)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return _volver(str(f.get("mes", "")))
    audit.registrar(conn, user["user"], "reporte.tarifas", {"personas": sorted(valores)})
    flash(request, "Tarifas por hora guardadas.", "ok")
    return _volver(str(f.get("mes", "")))


@router.post("/reportes/facturacion")
async def facturacion(request: Request, user: dict = Depends(require_permiso("reportes")),
                      conn: sqlite3.Connection = Depends(get_db)):
    f = await request.form()
    if not security.verificar_csrf(request.session, str(f.get("csrf", ""))):
        flash(request, "Sesión expirada, reintentá.", "error")
        return _volver(str(f.get("mes", "")))
    valores = {k[len("f_"):]: str(v) for k, v in f.items() if k.startswith("f_")}
    try:
        horas.guardar_facturacion(conn, valores)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return _volver(str(f.get("mes", "")))
    audit.registrar(conn, user["user"], "reporte.facturacion", {"clientes": sorted(valores)})
    flash(request, "Facturación mensual por cliente guardada.", "ok")
    return _volver(str(f.get("mes", "")))
