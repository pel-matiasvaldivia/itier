"""Vinculación y estado de los agentes de Zabbix de un cliente.

«Vincular un agente» = asignar un host de Zabbix al proxy del cliente y sumarlo a
su grupo, para que su monitoreo quede atribuido a ese cliente. También expone el
estado del proxy (última vez que se conectó) y de cada host (disponibilidad).
"""

from __future__ import annotations

from ..adapters import zabbix as zbx_mod
from . import ServiceError


def panel(cfg, cli: dict) -> dict:
    """Proxy + hosts del cliente, con etiquetas legibles."""
    with zbx_mod.cliente(cfg) as z:
        proxy = z.estado_proxy(cli.get("zbx_proxy_name") or "")
        hosts = z.listar_hosts(cli.get("zbx_hostgroup_id"))
    for h in hosts:
        h["disponibilidad"] = zbx_mod.DISPONIBILIDAD.get(str(h.get("available")), "—")
        h["estado_label"] = zbx_mod.ESTADO_HOST.get(str(h.get("status")), "—")
        h["vinculado"] = str(h.get("proxyid")) == str(cli.get("zbx_proxy_id"))
    return {"proxy": proxy, "hosts": hosts}


def vincular(cfg, cli: dict, hostid: str) -> None:
    proxyid = cli.get("zbx_proxy_id")
    groupid = cli.get("zbx_hostgroup_id")
    if not proxyid or not groupid:
        raise ServiceError(
            "El cliente no tiene proxy/grupo de Zabbix provisionados (estado incompleto). "
            "Revisá el alta antes de vincular agentes."
        )
    with zbx_mod.cliente(cfg) as z:
        z.vincular_host(hostid, proxyid, groupid)
