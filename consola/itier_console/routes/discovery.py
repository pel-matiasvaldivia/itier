"""Descubrimiento de red (SNMP) de un cliente: rangos de IP + credencial SNMP.

El operador los carga acá y la consola los empuja a GLPI (plugin GlpiInventory),
sin entrar al dashboard de GLPI.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..deps import flash, get_db, render, require_user
from ..services import ServiceError, discovery, onboarding

router = APIRouter()


def _cli_o_redir(request: Request, conn, slug: str):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return None, RedirectResponse("/clientes", status_code=303)
    return cli, None


@router.get("/clientes/{slug}/descubrimiento", response_class=HTMLResponse)
def panel(request: Request, slug: str, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli, redir = _cli_o_redir(request, conn, slug)
    if redir:
        return redir
    rangos = discovery.listar(conn, int(cli["id"]))
    return render(request, "descubrimiento.html", cli=cli, rangos=rangos)


@router.post("/clientes/{slug}/descubrimiento")
def agregar(
    request: Request,
    slug: str,
    nombre: str = Form(...),
    ip_start: str = Form(...),
    ip_end: str = Form(...),
    version: str = Form(...),
    community: str = Form(""),
    username: str = Form(""),
    auth_proto: str = Form(""),
    auth_pass: str = Form(""),
    priv_proto: str = Form(""),
    priv_pass: str = Form(""),
    csrf: str = Form(""),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli, redir = _cli_o_redir(request, conn, slug)
    if redir:
        return redir
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
    try:
        row = discovery.agregar(
            conn, request.app.state.cfg, user["user"], cli,
            nombre=nombre, ip_start=ip_start, ip_end=ip_end, version=version,
            community=community, username=username, auth_proto=auth_proto,
            auth_pass=auth_pass, priv_proto=priv_proto, priv_pass=priv_pass,
        )
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
    # Auditoría SIN secretos (solo metadatos).
    audit.registrar(conn, user["user"], "descubrimiento.alta", {
        "cliente": cli["nombre"], "rango": f"{row['ip_start']}–{row['ip_end']}",
        "snmp": row["snmp_version"], "estado": row["estado"]})
    if row["estado"] == "aplicado":
        flash(request, f"Rango «{row['nombre']}» cargado y aplicado en GLPI.", "ok")
    else:
        flash(request, f"Rango «{row['nombre']}» guardado, pero no se pudo aplicar en GLPI: "
                       f"{row.get('detalle') or 'error'}. Reintentá.", "error")
    return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)


@router.post("/clientes/{slug}/descubrimiento/{rid}/reaplicar")
def reaplicar(
    request: Request, slug: str, rid: int, csrf: str = Form(""),
    user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db),
):
    cli, redir = _cli_o_redir(request, conn, slug)
    if redir:
        return redir
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
    try:
        row = discovery.aplicar(conn, request.app.state.cfg, cli, rid)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
    audit.registrar(conn, user["user"], "descubrimiento.reaplicado",
                    {"cliente": cli["nombre"], "rango_id": rid, "estado": row["estado"]})
    flash(request, f"Rango «{row['nombre']}»: {row['estado']}" +
          (f" — {row.get('detalle')}" if row["estado"] == "error" and row.get("detalle") else ""),
          "ok" if row["estado"] == "aplicado" else "error")
    return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)


@router.post("/clientes/{slug}/descubrimiento/{rid}/eliminar")
def eliminar(
    request: Request, slug: str, rid: int, csrf: str = Form(""),
    user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db),
):
    cli, redir = _cli_o_redir(request, conn, slug)
    if redir:
        return redir
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
    row = discovery.eliminar(conn, int(cli["id"]), rid)
    if row:
        audit.registrar(conn, user["user"], "descubrimiento.baja",
                        {"cliente": cli["nombre"], "rango": f"{row['ip_start']}–{row['ip_end']}"})
        flash(request, f"Rango «{row['nombre']}» eliminado del registro (no se tocó GLPI).", "ok")
    return RedirectResponse(f"/clientes/{slug}/descubrimiento", status_code=303)
