"""Equipos de un cliente, para el portal: une lo que ven GLPI, MeshCentral y Zabbix.

Un equipo existe para el cliente si lo ve ALGUNA fuente (con Zabbix solo ya no alcanza:
el camino normal es GLPI Agent + Mesh Agent, ADR-0011). Cada fuente es best-effort: si una
no responde, se muestra lo de las otras y se avisa. Todo se scopea por la entidad de GLPI,
el grupo de Mesh y el grupo de Zabbix del cliente.
"""

from __future__ import annotations

from ..adapters import glpi as glpi_mod, mesh as mesh_mod, zabbix as zbx_mod
from . import mesh_cliente as mesh_propio


# Montajes internos de contenedores y del sistema: no son discos de nadie y ensucian la lista (p. ej. /usr/sbin/docker-init).
_PSEUDO = ("/usr/sbin/docker-init", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf", "/dev", "/proc", "/sys", "/run")


def _es_pseudo(nombre: str) -> bool:
    n = (nombre or "")
    return any(n == p or n.startswith(p + "/") for p in _PSEUDO)


def evaluar(salud: dict | None, umbral: int, en_linea: bool | None = None) -> dict:
    """Discos, antivirus y alertas de UN equipo a partir de lo inventariado.

    Alertas: [(nivel, texto)] con nivel «alta» · «media» · «baja». Reglas:
      - disco con uso >= umbral %  (alta si >= 95 %, media si no)
      - antivirus: ninguno detectado (media) · todos apagados (alta) · activo pero desactualizado (baja)
      - sin conexión ahora (baja)
    """
    discos, alertas = [], []
    antivirus = (salud or {}).get("antivirus") or []
    for d in (salud or {}).get("discos") or []:
        if _es_pseudo(d.get("nombre", "")):
            continue
        total, libre = int(d.get("total_mb") or 0), int(d.get("libre_mb") or 0)
        if total <= 0:
            continue
        pct = round(100 * (total - libre) / total)
        discos.append({"nombre": d["nombre"], "pct": pct, "total_gb": round(total / 1024, 1),
                       "libre_gb": round(libre / 1024, 1), "alerta": pct >= umbral})
        if pct >= umbral:
            alertas.append(("alta" if pct >= 95 else "media", f"Disco {d['nombre']} al {pct} % ({round(libre / 1024, 1)} GB libres)"))
    montajes = [d["nombre"] for d in (salud or {}).get("discos") or [] if d.get("nombre")]
    unix = bool(montajes) and all(n.startswith("/") for n in montajes)   # Linux/macOS (p. ej. el appliance): el antivirus no aplica
    if not (salud or {}).get("inventariado"):
        av_estado = "sin_datos"
    elif unix and not antivirus:
        av_estado = "no_aplica"
    elif not antivirus:
        av_estado = "ninguno"
        alertas.append(("media", "No se detectó antivirus"))
    elif not any(a["activo"] for a in antivirus):
        av_estado = "off"
        alertas.append(("alta", f"Antivirus APAGADO ({antivirus[0]['nombre']})"))
    else:
        av_estado = "on"
        if not all(a["actualizado"] for a in antivirus if a["activo"]):
            alertas.append(("baja", "Antivirus desactualizado"))
    if en_linea is False:
        alertas.append(("baja", "Sin conexión ahora"))
    orden = {"alta": 0, "media": 1, "baja": 2}
    alertas.sort(key=lambda a: orden[a[0]])
    return {"discos": discos, "antivirus": antivirus, "av_estado": av_estado, "alertas": alertas}


def listar(cfg, cli: dict, salud: bool = False) -> dict:
    """{'equipos': [...], 'proxy': {...}|None, 'notas': [...]} con los equipos ordenados por nombre.

    Cada equipo: nombre, en_glpi, en_mesh, en_linea, zbx ('1' disponible · '2' con problemas · '0' sin datos · None).
    Con `salud=True` suma además ci_id, nodeid, url_remoto, discos, antivirus, av_estado y alertas."""
    por_nombre: dict[str, dict] = {}
    notas: list[str] = []
    proxy = None

    def equipo(nombre: str) -> dict:
        n = (nombre or "").strip().upper()
        return por_nombre.setdefault(n, {"nombre": n, "en_glpi": False, "en_mesh": False, "en_linea": False, "zbx": None})

    estado_ci: dict[str, dict] = {}
    if cli.get("glpi_entity_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                cis = g.listar_cis(cli["glpi_entity_id"])
                for ci in cis:
                    if ci.get("name"):
                        x = equipo(ci["name"])
                        x["en_glpi"], x["ci_id"] = True, str(ci.get("id"))
                if salud:
                    estado_ci = g.salud_cis([str(ci.get("id")) for ci in cis])
        except Exception:  # noqa: BLE001
            notas.append("inventario")
    cfg_m = mesh_propio.cfg_de(cfg, cli) if mesh_propio.externo(cli) else cfg
    if cfg_m.mesh_enabled and (cli.get("mesh_id") or mesh_propio.externo(cli)):
        try:
            with mesh_mod.cliente(cfg_m) as m:
                for e in m.equipos(None if mesh_propio.externo(cli) else cli["mesh_id"]):
                    if e["nombre"]:
                        x = equipo(e["nombre"])
                        x["en_mesh"], x["en_linea"] = True, bool(e["conectado"])
                        x["nodeid"] = e.get("nodeid") or ""
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
    if salud:
        for x in por_nombre.values():
            x.update(evaluar(estado_ci.get(x.get("ci_id") or ""), cfg.alerta_disco_pct,
                             x["en_linea"] if x["en_mesh"] else None))
            x["url_remoto"] = mesh_mod.url_remoto(cfg_m, x["nodeid"]) if x.get("nodeid") and cfg_m.mesh_url else ""
    return {"equipos": sorted(por_nombre.values(), key=lambda e: e["nombre"]), "proxy": proxy, "notas": notas}
