"""Tablero: salud de los sistemas, resumen de la flota y tickets recientes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..adapters import AdapterError
from ..adapters import glpi as glpi_mod
from ..adapters import zabbix as zbx_mod
from ..deps import get_db, render, require_user
from ..services import onboarding
from ..services import tickets as tickets_svc

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg

    with glpi_mod.cliente(cfg) as g:
        salud_glpi = g.health()
    with zbx_mod.cliente(cfg) as z:
        salud_zbx = z.health()

    clientes = onboarding.listar_clientes(conn)
    filas = conn.execute("SELECT estado, COUNT(*) AS n FROM clients GROUP BY estado").fetchall()
    por_estado = {f["estado"]: f["n"] for f in filas}
    n_clientes = sum(por_estado.values())

    # Tickets recientes (en vivo desde GLPI), con el nombre del cliente mapeado
    # desde la entidad de GLPI. Si GLPI falla, el tablero igual se muestra.
    ent_a_cliente = {str(c["glpi_entity_id"]): c["nombre"] for c in clientes if c.get("glpi_entity_id")}
    tickets, error_tickets = [], None
    if cfg.glpi_configurado:
        try:
            tickets = tickets_svc.recientes(cfg, limit=10)
            for t in tickets:
                t["cliente"] = ent_a_cliente.get(str(t.get("entities_id")), "—")
        except AdapterError as e:
            error_tickets = e.detalle

    return render(
        request, "dashboard.html",
        salud_glpi=salud_glpi, salud_zbx=salud_zbx,
        n_clientes=n_clientes, por_estado=por_estado,
        glpi_conf=cfg.glpi_configurado, zbx_conf=cfg.zbx_configurado,
        tickets=tickets, error_tickets=error_tickets,
    )
