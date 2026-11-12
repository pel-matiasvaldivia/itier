"""Tablero: salud de los sistemas y resumen de la flota."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..adapters import glpi as glpi_mod
from ..adapters import zabbix as zbx_mod
from ..deps import get_db, render, require_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg

    with glpi_mod.cliente(cfg) as g:
        salud_glpi = g.health()
    with zbx_mod.cliente(cfg) as z:
        salud_zbx = z.health()

    filas = conn.execute("SELECT estado, COUNT(*) AS n FROM clients GROUP BY estado").fetchall()
    por_estado = {f["estado"]: f["n"] for f in filas}
    n_clientes = sum(por_estado.values())

    return render(
        request, "dashboard.html",
        salud_glpi=salud_glpi, salud_zbx=salud_zbx,
        n_clientes=n_clientes, por_estado=por_estado,
        glpi_conf=cfg.glpi_configurado, zbx_conf=cfg.zbx_configurado,
    )
