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


def recientes(cfg, limit: int = 10) -> list[dict]:
    """Tickets más recientes (de todos los clientes) con técnico asignado.

    Para el Tablero. Enriquece cada uno con estado/prioridad y el técnico (lo que
    cuesta una llamada por ticket, por eso se acota con `limit`). Una sola sesión.
    """
    with glpi_mod.cliente(cfg) as g:
        salida = []
        for t in g.listar_tickets(limit=limit)[:limit]:
            t = _enriquecer(t)
            t["tecnico"] = g.tecnico_de_ticket(int(t["id"]))
            salida.append(t)
        return salida


def ver(cfg, ticket_id: int) -> dict:
    with glpi_mod.cliente(cfg) as g:
        t = _enriquecer(g.ver_ticket(ticket_id))
        t["tecnico"] = g.tecnico_de_ticket(int(t["id"]))
        return t


def crear(cfg, titulo: str, contenido: str, entity_id: int, priority: int = 3) -> int:
    with glpi_mod.cliente(cfg) as g:
        return g.crear_ticket(titulo, contenido, entity_id, priority)


def asignar(cfg, ticket_id: int, users_id: int, reemplazar: bool = False) -> None:
    with glpi_mod.cliente(cfg) as g:
        g.asignar_ticket(ticket_id, users_id, reemplazar=reemplazar)


def seguimiento(cfg, ticket_id: int, contenido: str) -> None:
    with glpi_mod.cliente(cfg) as g:
        g.agregar_seguimiento(ticket_id, contenido)


def texto_plano(contenido: str) -> str:
    """GLPI guarda el texto enriquecido como HTML (a veces escapado): se muestra como texto plano."""
    import html
    import re
    t = html.unescape(html.unescape(contenido or ""))
    if re.search(r"<[a-zA-Z/][^>]*>", t):             # es HTML: los saltos de línea del código no cuentan, solo las etiquetas
        t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def conversacion(cfg, ticket_id: int) -> list[dict]:
    with glpi_mod.cliente(cfg) as g:
        segs = g.seguimientos(ticket_id)
    for x in segs:
        x["content"] = texto_plano(x["content"])
    return segs


def responder(cfg, ticket_id: int, texto: str, privado: bool, users_id: int | None) -> None:
    with glpi_mod.cliente(cfg) as g:
        g.agregar_seguimiento(ticket_id, texto, privado=privado, users_id=users_id)


def cambiar_estado(cfg, ticket_id: int, accion: str, solucion: str = "") -> None:
    with glpi_mod.cliente(cfg) as g:
        g.cambiar_estado(ticket_id, accion, solucion)
