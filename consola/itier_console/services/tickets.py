"""Operación de tickets de GLPI desde la consola.

Listar, ver, crear y asignar — para empezar a reemplazar el dashboard de GLPI en
los flujos de mesa de ayuda más frecuentes, sin perder a GLPI como fuente de
verdad.
"""

from __future__ import annotations

from ..adapters import glpi as glpi_mod


def _enriquecer(t: dict) -> dict:
    t = dict(t)
    t["estado_label"] = glpi_mod.ESTADOS_TICKET.get(int(t.get("status", 0) or 0), "—")
    t["prioridad_label"] = glpi_mod.PRIORIDADES.get(int(t.get("priority", 0) or 0), "—")
    return t


def listar(cfg, entity_id: int | None = None, limit: int = 100) -> list[dict]:
    with glpi_mod.cliente(cfg) as g:
        return [_enriquecer(t) for t in g.listar_tickets(entity_id=entity_id, limit=limit)]


def ver(cfg, ticket_id: int) -> dict:
    with glpi_mod.cliente(cfg) as g:
        return _enriquecer(g.ver_ticket(ticket_id))


def crear(cfg, titulo: str, contenido: str, entity_id: int, priority: int = 3) -> int:
    with glpi_mod.cliente(cfg) as g:
        return g.crear_ticket(titulo, contenido, entity_id, priority)


def asignar(cfg, ticket_id: int, users_id: int) -> None:
    with glpi_mod.cliente(cfg) as g:
        g.asignar_ticket(ticket_id, users_id)
