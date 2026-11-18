"""Tablero: salud de los sistemas, resumen de la flota y tickets recientes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import perf
from ..adapters import AdapterError
from ..adapters import glpi as glpi_mod
from ..adapters import zabbix as zbx_mod
from ..deps import get_db, render, require_permiso
from ..services import onboarding
from ..services import canales
from ..services import tickets as tickets_svc

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(require_permiso("tablero")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg

    def _glpi():
        with glpi_mod.cliente(cfg) as g:
            return g.health()

    def _zbx():
        with zbx_mod.cliente(cfg) as z:
            return z.health()

    def _tickets():
        return tickets_svc.recientes(cfg, limit=10) if cfg.glpi_configurado else []

    # Las tres consultas son independientes: en paralelo el tablero tarda lo de la más lenta, no la suma.
    (ok_g, salud_glpi), (ok_z, salud_zbx), (ok_t, res_tickets) = perf.paralelo([_glpi, _zbx, _tickets])
    if not ok_g:
        salud_glpi = {"ok": False, "detalle": str(getattr(salud_glpi, "detalle", salud_glpi))[:120]}
    if not ok_z:
        salud_zbx = {"ok": False, "detalle": str(getattr(salud_zbx, "detalle", salud_zbx))[:120]}

    clientes = onboarding.listar_clientes(conn)
    filas = conn.execute("SELECT estado, COUNT(*) AS n FROM clients GROUP BY estado").fetchall()
    por_estado = {f["estado"]: f["n"] for f in filas}
    n_clientes = sum(por_estado.values())

    # Tickets recientes (en vivo desde GLPI), con el nombre del cliente mapeado
    # desde la entidad de GLPI. Si GLPI falla, el tablero igual se muestra.
    ent_a_cliente = {str(c["glpi_entity_id"]): c["nombre"] for c in clientes if c.get("glpi_entity_id")}
    tickets, error_tickets = [], None
    if cfg.glpi_configurado:
        if ok_t:
            tickets = res_tickets
            for t in tickets:
                t["cliente"] = ent_a_cliente.get(str(t.get("entities_id")), "—")
        elif isinstance(res_tickets, AdapterError):
            error_tickets = res_tickets.detalle
        else:
            raise res_tickets

    return render(
        request, "dashboard.html",
        salud_glpi=salud_glpi, salud_zbx=salud_zbx,
        n_clientes=n_clientes, por_estado=por_estado,
        glpi_conf=cfg.glpi_configurado, zbx_conf=cfg.zbx_configurado,
        tickets=tickets, error_tickets=error_tickets,
        bandeja_pendientes=canales.pendientes(conn),
    )
