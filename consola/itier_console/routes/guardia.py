"""Guardia: lo primero que se abre desde el celular. Alertas de red activas y tickets que necesitan a alguien, con acciones de un toque.

  GET /guardia
Cada usuario ve lo que su rol permite (un técnico/PM/admin de cliente, solo su empresa).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..adapters import AdapterError
from ..deps import get_db, render, require_permiso
from ..services import onboarding, tickets as tickets_svc

router = APIRouter()


def _hace(fecha: str) -> str:
    """'2026-11-11 09:14' → 'hace 3 h'. Si no se puede leer, vacío."""
    try:
        d = datetime.strptime((fecha or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    seg = max(0, int((datetime.now() - d).total_seconds()))
    if seg < 3600:
        return f"hace {max(1, seg // 60)} min"
    if seg < 86400:
        return f"hace {seg // 3600} h"
    return f"hace {seg // 86400} d"


@router.get("/guardia", response_class=HTMLResponse)
def guardia(request: Request, user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    alcance = user.get("alcance")
    clientes = {c["id"]: c for c in onboarding.listar_clientes(conn) if not alcance or c["id"] == alcance["id"]}
    # Alertas de red en problema (las mantiene el vigía de red: se refresca cada minuto)
    alertas = []
    for r in conn.execute("SELECT * FROM red_chequeos WHERE estado = 'problema' ORDER BY desde DESC"):
        cli = clientes.get(r["client_id"])
        if not cli:
            continue
        abierta = conn.execute("SELECT ticket_id FROM red_alertas WHERE chequeo_id = ? AND fin IS NULL ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        alertas.append({"cliente": cli["nombre"], "slug": cli["slug"], "chequeo": r["nombre"], "destino": r["destino"], "desde": r["desde"] or "",
                        "ticket_id": abierta["ticket_id"] if abierta else None})
    # Tickets abiertos que requieren acción: nuevos (sin tomar) y urgentes primero
    tickets, error = [], None
    por_entidad = {str(c["glpi_entity_id"]): c["nombre"] for c in clientes.values() if c.get("glpi_entity_id")}
    if cfg.glpi_configurado:
        try:
            ent = int(alcance["entity"]) if alcance and alcance.get("entity") else None
            if alcance and ent is None:
                lista = []
            else:
                lista = tickets_svc.listar(cfg, entity_id=ent, limit=100)
            for t in lista:
                st, pr = int(t.get("status", 0) or 0), int(t.get("priority", 0) or 0)
                if st not in (1, 2, 3, 4) or str(t.get("entities_id")) not in por_entidad and alcance:
                    continue
                tickets.append({"id": t["id"], "titulo": t.get("name", ""), "estado": t.get("estado_label", ""), "prioridad": pr, "prioridad_label": t.get("prioridad_label", ""),
                                "nuevo": st == 1, "urgente": pr >= 4, "cliente": por_entidad.get(str(t.get("entities_id")), ""), "hace": _hace(t.get("date", ""))})
        except AdapterError as e:
            error = e.detalle
    tickets.sort(key=lambda t: (not t["urgente"], not t["nuevo"], -t["prioridad"], -int(t["id"])))
    mi = conn.execute("SELECT glpi_user_id FROM users WHERE id = ?", (user["uid"],)).fetchone()
    return render(request, "guardia.html", alertas=alertas, tickets=tickets[:20], total_tickets=len(tickets), error=error,
                  nuevos=sum(1 for t in tickets if t["nuevo"]), urgentes=sum(1 for t in tickets if t["urgente"]),
                  puedo_tomar=bool(mi and mi["glpi_user_id"]))
