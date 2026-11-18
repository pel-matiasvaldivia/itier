"""Equipos de un cliente, para el portal: une lo que ven GLPI, MeshCentral y Zabbix.

Un equipo existe para el cliente si lo ve ALGUNA fuente (con Zabbix solo ya no alcanza:
el camino normal es GLPI Agent + Mesh Agent, ADR-0011). Cada fuente es best-effort: si una
no responde, se muestra lo de las otras y se avisa. Todo se scopea por la entidad de GLPI,
el grupo de Mesh y el grupo de Zabbix del cliente.
"""

from __future__ import annotations

from ..adapters import glpi as glpi_mod, mesh as mesh_mod, zabbix as zbx_mod


def listar(cfg, cli: dict) -> dict:
    """{'equipos': [...], 'proxy': {...}|None, 'notas': [...]} con los equipos ordenados por nombre.

    Cada equipo: nombre, en_glpi, en_mesh, en_linea, zbx ('1' disponible · '2' con problemas · '0' sin datos · None)."""
    por_nombre: dict[str, dict] = {}
    notas: list[str] = []
    proxy = None

    def equipo(nombre: str) -> dict:
        n = (nombre or "").strip().upper()
        return por_nombre.setdefault(n, {"nombre": n, "en_glpi": False, "en_mesh": False, "en_linea": False, "zbx": None})

    if cli.get("glpi_entity_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                for ci in g.listar_cis(cli["glpi_entity_id"]):
                    if ci.get("name"):
                        equipo(ci["name"])["en_glpi"] = True
        except Exception:  # noqa: BLE001
            notas.append("inventario")
    if cfg.mesh_enabled and cli.get("mesh_id"):
        try:
            with mesh_mod.cliente(cfg) as m:
                for e in m.equipos(cli["mesh_id"]):
                    if e["nombre"]:
                        x = equipo(e["nombre"])
                        x["en_mesh"], x["en_linea"] = True, bool(e["conectado"])
        except Exception:  # noqa: BLE001
            notas.append("conexión")
    if cli.get("zbx_hostgroup_id"):
        try:
            with zbx_mod.cliente(cfg) as z:
                proxy = z.estado_proxy(cli.get("zbx_proxy_name") or "")
                for h in z.listar_hosts(cli["zbx_hostgroup_id"]):
                    equipo(h.get("host") or h.get("name") or "")["zbx"] = str(h.get("available") or "0")
        except Exception:  # noqa: BLE001
            notas.append("monitoreo")
    return {"equipos": sorted(por_nombre.values(), key=lambda e: e["nombre"]), "proxy": proxy, "notas": notas}
