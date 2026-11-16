"""Curación de la CMDB: clasificación de criticidad de los CIs, por cliente.

La **criticidad de cada CI alimenta el techo de autonomía** del agente (docs/07
§6.2): no es cosmético, es de seguridad. El operador la fija acá y la consola la
escribe en GLPI (campo *business criticity*), que es la **misma fuente** que lee
el agente de borde (`reference/edge-agent/.../datos.py`). Así hay una sola fuente
de verdad: la CMDB.
"""

from __future__ import annotations

from ..adapters import glpi as glpi_mod
from ..adapters.glpi import CRITICIDADES
from . import ServiceError

NIVELES = ["critica", "alta", "media", "baja"]
_NOMBRE_A_CLAVE = {v: k for k, v in CRITICIDADES.items()}


def listar(cfg, cli: dict) -> list[dict]:
    """CIs del cliente con su criticidad actual (clave iTier + nombre GLPI)."""
    entity = cli.get("glpi_entity_id")
    if not entity:
        return []
    with glpi_mod.cliente(cfg) as g:
        cis = g.listar_cis(entity)
    for c in cis:
        c["criticidad_clave"] = _NOMBRE_A_CLAVE.get(c.get("criticidad") or "", "")
    return cis


def fijar(cfg, cli: dict, itemtype: str, items_id: str, criticidad: str) -> dict:
    """Escribe la criticidad del CI en GLPI. Devuelve {ok, criticidad}."""
    if criticidad not in NIVELES:
        raise ServiceError(f"Criticidad inválida: {criticidad}.")
    if not cli.get("glpi_entity_id"):
        raise ServiceError("El cliente no tiene entidad GLPI (alta incompleta).")
    with glpi_mod.cliente(cfg) as g:
        return g.fijar_criticidad(itemtype, str(items_id), criticidad)
