"""Red del cliente desde la consola: equipos activos en su LAN y chequeos de conectividad con alertas.

Todo lo ejecuta el **proxy de Zabbix** que ya está instalado en la red del cliente (la consola no llega a esa LAN):
  - barrido de hosts: una regla de descubrimiento de Zabbix (ICMP + puertos típicos) sobre los rangos que se definan;
  - chequeos: ping a Internet (8.8.8.8, 1.1.1.1…), al gateway, a un puerto TCP, o el estado de la interfaz WAN del router
    por SNMP. Cada chequeo crea items y una alerta (trigger) en Zabbix; el vigía avisa cuando cae o se recupera.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone

from ..adapters import AdapterError, glpi as glpi_mod, zabbix as zbx_mod
from . import ServiceError, red_tipos

log = logging.getLogger("itier.red")

TIPOS = {
    "internet": "Internet (ping a 8.8.8.8 y 1.1.1.1)",
    "gateway": "Router / gateway (ping)",
    "wan_snmp": "Interfaz WAN del router (SNMP)",
    "puerto": "Servicio TCP (puerto abierto)",
    "router_snmp": "Router o switch por SNMP (todas sus interfaces)",
    "dispositivo": "Dispositivo monitoreado (ping)",     # se crea desde la tabla de equipos con «Monitorear»
}
_RE_HOST = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_RE_COMM = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(v) -> str:
    try:
        return datetime.fromtimestamp(int(v), timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


# ── validaciones ────────────────────────────────────────────────────────────────────────────────

def rango_valido(texto: str) -> str:
    """Rango para barrer (formato Zabbix): 192.168.1.0/24 · 192.168.1.1-254 · varios separados por coma. Solo redes privadas."""
    partes = [p.strip() for p in (texto or "").split(",") if p.strip()]
    if not partes or len(partes) > 6:
        raise ServiceError("Cargá entre 1 y 6 rangos, separados por coma (ej.: 192.168.1.0/24).")
    total = 0
    for p in partes:
        try:
            if "/" in p:
                red = ipaddress.ip_network(p, strict=False)
                if red.version != 4 or not red.is_private:
                    raise ValueError
                if red.prefixlen < 22:
                    raise ServiceError(f"«{p}» es demasiado grande: usá /22 o más chica (hasta 1.022 equipos por rango).")
                total += red.num_addresses
            else:
                m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.)(\d{1,3})-(\d{1,3})$", p)
                if not m:
                    raise ValueError
                a, b = int(m.group(2)), int(m.group(3))
                if not (0 <= a <= b <= 255) or not ipaddress.ip_address(m.group(1) + "1").is_private:
                    raise ValueError
                total += b - a + 1
        except ServiceError:
            raise
        except ValueError:
            raise ServiceError(f"Rango inválido: «{p}». Usá 192.168.1.0/24 o 192.168.1.1-254 (solo redes privadas).")
    if total > 4096:
        raise ServiceError("Demasiadas direcciones en total (máximo 4.096 por escaneo).")
    return ",".join(partes)


def destino_valido(d: str) -> str:
    d = (d or "").strip().lower()
    try:
        ipaddress.IPv4Address(d)
        return d
    except ipaddress.AddressValueError:
        pass
    if not _RE_HOST.match(d) or "." not in d:
        raise ServiceError(f"Destino inválido: «{d}». Usá una IP (8.8.8.8) o un nombre (google.com).")
    return d


# ── acceso a Zabbix ─────────────────────────────────────────────────────────────────────────────

def _necesita_proxy(cli: dict) -> None:
    if not cli.get("zbx_proxy_id"):
        raise ServiceError("Este cliente todavía no tiene su proxy de Zabbix: reintentá el aprovisionamiento desde la ficha.")


def _host_red(cli: dict, sufijo: str = "") -> str:
    return f"itier-red-{cli['slug']}{sufijo}"[:128]


# ── barrido de equipos activos ──────────────────────────────────────────────────────────────────

def rangos(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM red_rangos WHERE client_id = ? ORDER BY id", (cid,))]


def agregar_rango(conn, cfg, cli: dict, actor: str, nombre: str, iprange: str) -> int:
    nombre = (nombre or "").strip()[:60] or "LAN"
    iprange = rango_valido(iprange)
    _necesita_proxy(cli)
    try:
        with zbx_mod.cliente(cfg) as z:
            drule = z.asegurar_regla_descubrimiento(f"itier-red-{cli['slug']}-{nombre}"[:255], iprange, cli["zbx_proxy_id"])
        estado = "ok"
    except AdapterError as e:
        raise ServiceError(f"No se pudo crear el barrido en Zabbix: {e.detalle}")
    cur = conn.execute("INSERT INTO red_rangos(client_id, nombre, iprange, drule_id, estado, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                       (cli["id"], nombre, iprange, drule, estado, _ahora(), actor))
    conn.commit()
    return int(cur.lastrowid)


def escanear(conn, cfg, cli: dict, actor: str) -> int:
    """Pide un barrido inmediato de todos los rangos del cliente. → cantidad de rangos."""
    rs = [r for r in rangos(conn, cli["id"]) if r.get("drule_id")]
    if not rs:
        raise ServiceError("Primero cargá un rango de red para barrer.")
    try:
        with zbx_mod.cliente(cfg) as z:
            for r in rs:
                z.escanear_ahora(r["drule_id"])
    except AdapterError as e:
        raise ServiceError(f"No se pudo iniciar el barrido: {e.detalle}")
    return len(rs)


def quitar_rango(conn, cfg, cli: dict, rid: int) -> None:
    r = conn.execute("SELECT * FROM red_rangos WHERE id = ? AND client_id = ?", (rid, cli["id"])).fetchone()
    if not r:
        return
    try:
        with zbx_mod.cliente(cfg) as z:
            if r["drule_id"]:
                z.eliminar_regla_descubrimiento(r["drule_id"])
    except AdapterError:
        pass                                  # si ya no existía en Zabbix, igual se quita de acá
    conn.execute("DELETE FROM red_rangos WHERE id = ?", (rid,))
    conn.commit()


def _ip_clave(ip: str):
    try:
        return int(ipaddress.IPv4Address(ip))
    except ipaddress.AddressValueError:
        return 0


def dispositivos_manuales(conn: sqlite3.Connection, cid: int) -> dict[str, dict]:
    return {r["ip"]: dict(r) for r in conn.execute("SELECT * FROM red_dispositivos WHERE client_id = ?", (cid,))}


def guardar_dispositivo(conn, cid: int, actor: str, ip: str, nombre: str, tipo: str, fabricante: str, notas: str = "") -> None:
    try:
        ipaddress.IPv4Address((ip or "").strip())
    except ipaddress.AddressValueError:
        raise ServiceError("IP inválida.")
    if tipo and tipo not in red_tipos.TIPOS:
        raise ServiceError("Tipo de dispositivo desconocido.")
    nombre, fabricante, notas = (nombre or "").strip()[:80], (fabricante or "").strip()[:60], (notas or "").strip()[:200]
    if not (nombre or tipo or fabricante or notas):            # todo vacío = volver a la identificación automática
        conn.execute("DELETE FROM red_dispositivos WHERE client_id=? AND ip=?", (cid, ip.strip()))
    else:
        conn.execute("INSERT INTO red_dispositivos(client_id, ip, nombre, tipo, fabricante, notas, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(client_id, ip) DO UPDATE SET nombre=excluded.nombre, tipo=excluded.tipo, fabricante=excluded.fabricante, "
                     "notas=excluded.notas, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (cid, ip.strip(), nombre, tipo, fabricante, notas, _ahora(), actor))
    conn.commit()


def _conocidos(cfg, cli: dict) -> tuple[dict, dict]:
    """(ip→dispositivo de GLPI, nombre en mayúsculas→equipo con agente/Mesh/Zabbix). Mejor esfuerzo: si algo falla, sigue sin eso."""
    glpi_ip, por_nombre = {}, {}
    if cfg.glpi_configurado and cli.get("glpi_entity_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                glpi_ip = g.inventario_por_ip(cli["glpi_entity_id"])
        except AdapterError:
            pass
    try:
        from . import equipos as equipos_svc
        for e in equipos_svc.listar(cfg, cli)["equipos"]:
            por_nombre[e["nombre"].upper()] = {"nombre": e["nombre"], "en_glpi": e.get("en_glpi"), "en_mesh": e.get("en_mesh"), "zbx": e.get("zbx") not in (None, "0")}
    except Exception:  # noqa: BLE001
        pass
    return glpi_ip, por_nombre


def hosts_activos(conn, cfg, cli: dict) -> dict:
    """{hosts, nota, resumen, tipos}. Cada host: ip, dns, estado, visto, puertos, monitoreado + tipo/nombre/origen (ver red_tipos)."""
    rs = [r["drule_id"] for r in rangos(conn, cli["id"]) if r.get("drule_id")]
    vacio = {"hosts": [], "nota": "", "resumen": {"total": 0, "up": 0, "down": 0, "sin_identificar": 0}, "tipos": []}
    if not rs:
        return vacio
    nota, lista, monitoreadas = "", [], set()
    try:
        with zbx_mod.cliente(cfg) as z:
            lista = z.hosts_descubiertos(rs)
            monitoreadas = z.ips_monitoreadas(cli.get("zbx_hostgroup_id"))
    except AdapterError as e:
        nota = f"Zabbix no respondió: {e.detalle[:120]}"
    glpi_ip, por_nombre = _conocidos(cfg, cli)
    manuales = dispositivos_manuales(conn, cli["id"])
    act = [(r["etiqueta"].lower(), r["notas"] or "") for r in conn.execute("SELECT etiqueta, notas FROM activos WHERE client_id=? AND estado != 'de_baja'", (cli["id"],))]
    mon = {r["destino"] for r in conn.execute("SELECT destino FROM red_chequeos WHERE client_id=? AND tipo='dispositivo'", (cli["id"],))}
    for h in lista:
        h["monitoreado"] = h["ip"] in monitoreadas
        h["visto"] = _ts(h.get("ultimo_up")) if h["estado"] == "down" else "ahora"
        h["visto_ts"] = _ts(h.get("ultimo_up"))
        corto = (h.get("dns") or "").split(".")[0].upper()
        conocido = por_nombre.get(corto) or por_nombre.get(((glpi_ip.get(h["ip"]) or {}).get("nombre") or "").upper())
        h.update(red_tipos.clasificar(h, glpi_ip.get(h["ip"]), manuales.get(h["ip"]), conocido))
        h["notas"] = (manuales.get(h["ip"]) or {}).get("notas", "")
        h["en_activos"] = any(f"IP {h['ip']}" in n or (h.get("nombre") and e == h["nombre"].lower()) for e, n in act)
        h["monitoreo_propio"] = h["ip"] in mon
        h["icono"] = red_tipos.ICONOS.get(h["tipo"], "❔")
    lista.sort(key=lambda h: _ip_clave(h["ip"]))
    up = sum(1 for h in lista if h["estado"] == "up")
    cuenta: dict[str, int] = {}
    for h in lista:
        cuenta[h["tipo"]] = cuenta.get(h["tipo"], 0) + 1
    return {"hosts": lista, "nota": nota,
            "resumen": {"total": len(lista), "up": up, "down": len(lista) - up, "sin_identificar": cuenta.get("Sin identificar", 0)},
            "tipos": sorted(cuenta.items(), key=lambda kv: (-kv[1], kv[0]))}


# ── chequeos de conectividad ────────────────────────────────────────────────────────────────────

def chequeos(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return [dict(r, params=json.loads(r["params"] or "{}")) for r in conn.execute("SELECT * FROM red_chequeos WHERE client_id = ? ORDER BY id", (cid,))]


def crear_chequeo(conn, cfg, cli: dict, actor: str, tipo: str, nombre: str, destino: str, extra: dict | None = None) -> int:
    extra = extra or {}
    if tipo not in TIPOS:
        raise ServiceError("Tipo de chequeo desconocido.")
    _necesita_proxy(cli)
    if not cli.get("zbx_hostgroup_id"):
        raise ServiceError("El cliente no tiene grupo de hosts en Zabbix.")
    nombre = (nombre or "").strip()[:80] or TIPOS[tipo].split(" (")[0]
    gid, pid = cli["zbx_hostgroup_id"], cli["zbx_proxy_id"]
    params: dict = {}
    items: list[str] = []
    try:
        with zbx_mod.cliente(cfg) as z:
            if tipo == "router_snmp":
                ip = destino_valido(destino)
                comm = (extra.get("community") or "").strip()
                if not _RE_COMM.match(comm):
                    raise ServiceError("Community SNMP inválido (letras, números y . _ @ + -).")
                host = _host_red(cli, "-router-" + ip.replace(".", "-"))
                hid = z.asegurar_host_router(host, f"Router {ip} · {cli['nombre']}", gid, pid, ip, comm)
                tid, destino_txt, params = None, ip, {"host": host}
            elif tipo in ("internet", "gateway", "puerto"):
                host = _host_red(cli)
                hid, iid = z.asegurar_host_chequeos(host, f"Red · {cli['nombre']}", gid, pid)
                if tipo == "internet":
                    dest = [destino_valido(d) for d in re.split(r"[,\s]+", destino or "8.8.8.8, 1.1.1.1") if d.strip()][:4]
                    if not dest:
                        raise ServiceError("Cargá al menos un destino.")
                    claves = []
                    for d in dest:
                        k = f"icmpping[{d}]"
                        items.append(z.asegurar_item(hid, iid, f"Ping a {d}", k))
                        items.append(z.asegurar_item(hid, iid, f"Latencia a {d}", f"icmppingsec[{d}]", unidades="s", flotante=True))
                        claves.append(k)
                    expr = " and ".join(f"max(/{host}/{k},#3)=0" for k in claves)
                    desc = f"Sin Internet · {cli['nombre']} ({', '.join(dest)})"
                    destino_txt, params = ", ".join(dest), {"destinos": dest}
                    prio = 5
                elif tipo == "gateway":
                    ip = destino_valido(destino)
                    k = f"icmpping[{ip}]"
                    items.append(z.asegurar_item(hid, iid, f"Ping al gateway {ip}", k))
                    items.append(z.asegurar_item(hid, iid, f"Latencia al gateway {ip}", f"icmppingsec[{ip}]", unidades="s", flotante=True))
                    expr, desc, destino_txt, prio = f"max(/{host}/{k},#3)=0", f"Router sin respuesta · {cli['nombre']} ({ip})", ip, 4
                else:
                    m = re.match(r"^([A-Za-z0-9.-]+):(\d{1,5})$", (destino or "").strip())
                    if not m or not 1 <= int(m.group(2)) <= 65535:
                        raise ServiceError("Para un servicio TCP cargá destino y puerto, ej.: 200.1.2.3:443")
                    ip, puerto = destino_valido(m.group(1)), int(m.group(2))
                    k = f"net.tcp.service[tcp,{ip},{puerto}]"
                    items.append(z.asegurar_item(hid, iid, f"Servicio TCP {ip}:{puerto}", k))
                    expr, desc, destino_txt, prio = f"max(/{host}/{k},#3)=0", f"Servicio caído · {cli['nombre']} ({ip}:{puerto})", f"{ip}:{puerto}", 4
                    params = {"puerto": puerto}
            else:   # wan_snmp
                ip = destino_valido(destino)
                comm = (extra.get("community") or "").strip()
                try:
                    idx = int(extra.get("ifindex") or 0)
                except ValueError:
                    idx = 0
                if not _RE_COMM.match(comm):
                    raise ServiceError("Community SNMP inválido (letras, números y . _ @ + -).")
                if not 1 <= idx <= 9999:
                    raise ServiceError("El índice de la interfaz (ifIndex) tiene que estar entre 1 y 9999.")
                host = _host_red(cli, "-snmp-" + ip.replace(".", "-"))
                hid, iid = z.asegurar_host_chequeos(host, f"Router {ip} · {cli['nombre']}", gid, pid, snmp={"ip": ip, "community": comm})
                k = f"wan.ifOperStatus[{idx}]"
                items.append(z.asegurar_item(hid, iid, f"Estado de la interfaz WAN {idx}", k, tipo="snmp", oid=f"1.3.6.1.2.1.2.2.1.8.{idx}"))
                expr, desc, destino_txt, prio = f"last(/{host}/{k})<>1", f"Interfaz WAN caída · {cli['nombre']} ({ip} if{idx})", ip, 4
                params = {"ifindex": idx}
            if tipo != "router_snmp":
                tid = z.asegurar_trigger(desc, expr, prio)
    except AdapterError as e:
        raise ServiceError(f"No se pudo crear el chequeo en Zabbix: {e.detalle}")
    cur = conn.execute("INSERT INTO red_chequeos(client_id, tipo, nombre, destino, params, zbx_hostid, zbx_trigger, zbx_items, estado, created_at, created_by) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (cli["id"], tipo, nombre, destino_txt, json.dumps(params), hid, tid, ",".join(items), "desconocido", _ahora(), actor))
    conn.commit()
    return int(cur.lastrowid)


def monitorear(conn, cfg, cli: dict, actor: str, ip: str, nombre: str = "") -> int:
    """Un clic desde la tabla de equipos: ping continuo a esa IP desde el proxy del cliente, con alerta (y aviso/ticket) si deja de responder."""
    try:
        ipaddress.IPv4Address((ip or "").strip())
    except ipaddress.AddressValueError:
        raise ServiceError("IP inválida.")
    ip = ip.strip()
    _necesita_proxy(cli)
    if not cli.get("zbx_hostgroup_id"):
        raise ServiceError("El cliente no tiene grupo de hosts en Zabbix.")
    if conn.execute("SELECT 1 FROM red_chequeos WHERE client_id=? AND tipo='dispositivo' AND destino=?", (cli["id"], ip)).fetchone():
        raise ServiceError(f"{ip} ya se está monitoreando.")
    etiqueta = (nombre or "").strip()[:80] or ip
    host = f"itier-mon-{cli['slug']}-{ip.replace('.', '-')}"[:128]
    try:
        with zbx_mod.cliente(cfg) as z:
            hid, iid = z.asegurar_host_chequeos(host, f"{etiqueta} ({ip})", cli["zbx_hostgroup_id"], cli["zbx_proxy_id"], ip=ip)
            items = [z.asegurar_item(hid, iid, f"Ping a {etiqueta}", "icmpping"),
                     z.asegurar_item(hid, iid, f"Latencia a {etiqueta}", "icmppingsec", unidades="s", flotante=True)]
            tid = z.asegurar_trigger(f"Sin respuesta · {cli['nombre']} · {etiqueta} ({ip})", f"max(/{host}/icmpping,#3)=0", 3)
    except AdapterError as e:
        raise ServiceError(f"No se pudo monitorear en Zabbix: {e.detalle}")
    cur = conn.execute("INSERT INTO red_chequeos(client_id, tipo, nombre, destino, params, zbx_hostid, zbx_trigger, zbx_items, estado, created_at, created_by) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cli["id"], "dispositivo", etiqueta, ip, json.dumps({"host": host}), hid, tid, ",".join(items), "desconocido", _ahora(), actor))
    conn.commit()
    return int(cur.lastrowid)


_TIPO_ACTIVO = {"Notebook": "notebook", "Desktop": "desktop", "PC Windows": "desktop", "Celular/Tablet": "celular"}


def a_activos(conn, cli: dict, actor: str, ip: str, nombre: str, tipo: str, fabricante: str) -> int:
    """Pasa un dispositivo identificado de la red al listado de activos del cliente. Los que no son PC/notebook/celular van como «Otro»."""
    from . import activos as activos_svc
    ip = (ip or "").strip()
    tipo = tipo if tipo and tipo != "Sin identificar" else "Otro"
    etiqueta = (nombre or "").strip()[:80] or f"{tipo}-{ip}"
    return activos_svc.crear(conn, cli["id"], actor, {"tipo": _TIPO_ACTIVO.get(tipo, "otro"), "etiqueta": etiqueta, "marca": (fabricante or "").strip()[:60],
                                                       "notas": f"Detectado en la red: {tipo} · IP {ip}"})


def quitar_chequeo(conn, cfg, cli: dict, chid: int) -> None:
    c = conn.execute("SELECT * FROM red_chequeos WHERE id = ? AND client_id = ?", (chid, cli["id"])).fetchone()
    if not c:
        return
    try:
        with zbx_mod.cliente(cfg) as z:
            if c["tipo"] in ("dispositivo", "router_snmp") and c["zbx_hostid"]:
                z.eliminar_host(c["zbx_hostid"])        # el host era solo de este dispositivo
            else:
                z.eliminar_chequeo(c["zbx_trigger"], [i for i in (c["zbx_items"] or "").split(",") if i])
    except AdapterError:
        pass
    conn.execute("DELETE FROM red_chequeos WHERE id = ?", (chid,))
    conn.commit()


def _router(cfg, c: dict) -> dict:
    """Estado vivo de un router/switch por SNMP: problema si no responde por SNMP o alguna alerta de sus plantillas está activa."""
    try:
        with zbx_mod.cliente(cfg) as z:
            r = z.estado_router(c["zbx_hostid"])
    except AdapterError:
        return {"estado_vivo": c["estado"] or "desconocido", "desde_txt": c["desde"] or "", "latencia_ms": None, "interfaces": [], "problemas_txt": []}
    probs = [p["descripcion"] for p in r["problemas"]]
    if r["disponible"] == "no":
        probs.insert(0, "No responde por SNMP" + (f": {r['error'][:80]}" if r["error"] else ""))
    est = "problema" if probs else ("ok" if r["disponible"] == "si" else "desconocido")
    desde = max((int(p["desde"]) for p in r["problemas"] if str(p["desde"]).isdigit()), default=0)
    return {"estado_vivo": est, "desde_txt": _ts(desde) if desde else (c["desde"] or ""), "latencia_ms": None,
            "interfaces": r["interfaces"], "problemas_txt": probs}


def estado(conn, cfg, cli: dict) -> dict:
    """Estado vivo de cada chequeo + historial de caídas. Si Zabbix no responde, muestra lo último que vio el vigía."""
    lista = chequeos(conn, cli["id"])
    nota, vivos, hist = "", {"triggers": {}, "items": {}}, []
    if lista:
        try:
            with zbx_mod.cliente(cfg) as z:
                vivos = z.estado_alertas([c["zbx_trigger"] for c in lista if c["zbx_trigger"]],
                                         [i for c in lista for i in (c["zbx_items"] or "").split(",") if i])
                hist = z.historial_caidas([c["zbx_trigger"] for c in lista if c["zbx_trigger"]])
        except AdapterError as e:
            nota = f"Zabbix no respondió: {e.detalle[:120]}"
    nombre_de = {c["zbx_trigger"]: c["nombre"] for c in lista}
    for c in lista:
        if c["tipo"] == "router_snmp":
            r = _router(cfg, c)
            c.update(r)
            continue
        t = vivos["triggers"].get(c["zbx_trigger"])
        c["estado_vivo"] = t["estado"] if t else (c["estado"] or "desconocido")
        c["desde_txt"] = _ts(t["desde"]) if t else (c["desde"] or "")
        lat = None
        for i in (c["zbx_items"] or "").split(","):
            v = vivos["items"].get(i)
            if v and v["valor"] not in ("", None):
                try:
                    f = float(v["valor"])
                    if 0 < f < 5 and c["tipo"] in ("internet", "gateway") and "." in str(v["valor"]):
                        lat = round(f * 1000) if lat is None else min(lat, round(f * 1000))
                except ValueError:
                    pass
        c["latencia_ms"] = lat
    caidas = [{"chequeo": nombre_de.get(h["triggerid"], "?"), "inicio": _ts(h["inicio"]), "fin": _ts(h["fin"]) if h["fin"] else "",
               "minutos": (round((int(h["fin"]) - int(h["inicio"])) / 60) if h["fin"] else None)} for h in hist]
    return {"chequeos": lista, "caidas": caidas, "nota": nota, "en_problema": sum(1 for c in lista if c["estado_vivo"] == "problema")}


# ── vigía: avisa cuando un chequeo cae o se recupera ────────────────────────────────────────────

def _aviso(cfg, cli: dict, ch: dict, cayo: bool, desde: str) -> str:
    desde = desde or _ahora()[:16].replace("T", " ")
    from . import correo
    if not (cfg.soporte_email and correo.configurado(cfg)):
        return "sin correo configurado"
    asunto = f"[Red] {'CAÍDA' if cayo else 'Recuperado'}: {ch['nombre']} · {cli['nombre']}"
    cuerpo = (f"{'Se detectó una caída' if cayo else 'Se recuperó el servicio'} en {cli['nombre']}.\n\nChequeo: {ch['nombre']} ({TIPOS.get(ch['tipo'], ch['tipo'])})\n"
              f"Destino: {ch['destino']}\nDesde: {desde} UTC\n"
              + (f"Detalle: {ch['_detalle']}\n" if ch.get("_detalle") else "")
              + (f"\nVer en la consola: {cfg.console_public_url}/clientes/{cli['slug']}/red\n" if cfg.console_public_url else ""))
    try:
        correo.enviar(cfg, cfg.soporte_email, asunto, cuerpo)
        return f"ok (enviado a {cfg.soporte_email})"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"[:200]


def sincronizar(conn: sqlite3.Connection, cfg) -> int:
    """Compara el estado de cada chequeo con el último visto; avisa y abre/cierra la caída. → cantidad de cambios."""
    cambios = 0
    for cli in [dict(r) for r in conn.execute("SELECT * FROM clients WHERE estado = 'activo'")]:
        lista = chequeos(conn, cli["id"])
        if not lista:
            continue
        try:
            with zbx_mod.cliente(cfg) as z:
                vivos = z.estado_alertas([c["zbx_trigger"] for c in lista if c["zbx_trigger"]], [])["triggers"]
        except AdapterError as e:
            log.info("Vigía de red: Zabbix no respondió para %s: %s", cli["slug"], e)
            continue
        for ch in lista:
            if ch["tipo"] == "router_snmp":                      # no tiene un trigger propio: se mira el host entero
                r = _router(cfg, ch)
                if r["estado_vivo"] == "desconocido":
                    continue
                vivos[ch["zbx_trigger"]] = {"estado": r["estado_vivo"], "desde": 0, "deshabilitado": False, "detalle": "; ".join(r["problemas_txt"])}
            t = vivos.get(ch["zbx_trigger"])
            if not t or t.get("deshabilitado"):
                continue
            ch["_detalle"] = t.get("detalle", "")
            nuevo, previo = t["estado"], ch["estado"] or "desconocido"
            if nuevo == previo:
                continue
            conn.execute("UPDATE red_chequeos SET estado=?, desde=? WHERE id=?", (nuevo, _ts(t["desde"]) or _ahora()[:16].replace("T", " "), ch["id"]))
            if nuevo == "problema":
                aviso = _aviso(cfg, cli, ch, True, _ts(t["desde"]))
                ticket = _abrir_ticket(cfg, cli, ch, _ts(t["desde"]))
                conn.execute("INSERT INTO red_alertas(client_id, chequeo_id, inicio, aviso, ticket_id) VALUES (?,?,?,?,?)",
                             (cli["id"], ch["id"], _ts(t["desde"]) or _ahora(), aviso, ticket))
                _push(conn, cfg, cli, f"🚨 CAÍDA · {cli['nombre']}", f"{ch['nombre']} ({ch['destino']}){' · ticket #' + str(ticket) if ticket else ''}",
                      f"/tickets/{ticket}" if ticket else "/guardia", True, f"red-{ch['id']}")
            elif previo == "problema":
                fila = conn.execute("SELECT * FROM red_alertas WHERE chequeo_id=? AND fin IS NULL ORDER BY id DESC LIMIT 1", (ch["id"],)).fetchone()
                aviso = _aviso(cfg, cli, ch, False, _ts(t["desde"]))
                _push(conn, cfg, cli, f"✅ Recuperado · {cli['nombre']}", f"{ch['nombre']} volvió a responder", "/guardia", False, f"red-{ch['id']}")
                if fila:
                    conn.execute("UPDATE red_alertas SET fin=?, aviso=COALESCE(aviso,'') || ' · ' || ? WHERE id=?", (_ts(t["desde"]) or _ahora(), aviso, fila["id"]))
                    _cerrar_ticket(cfg, fila["ticket_id"], ch)
            cambios += 1
        conn.commit()
    return cambios


def _push(conn, cfg, cli: dict, titulo: str, cuerpo: str, url: str, urgente: bool, tag: str) -> None:
    from . import push as push_svc
    push_svc.avisar(conn, cfg, titulo, cuerpo, url, cliente_id=cli["id"], urgente=urgente, tag=tag)


def _abrir_ticket(cfg, cli: dict, ch: dict, desde: str) -> int | None:
    if not (cfg.glpi_configurado and cli.get("glpi_entity_id")):
        return None
    try:
        from . import tickets as tickets_svc
        return tickets_svc.crear(cfg, f"[Red] Caída: {ch['nombre']}", f"Alerta automática de iTiers.\n\nCliente: {cli['nombre']}\nChequeo: {ch['nombre']}\n"
                                 f"Destino: {ch['destino']}\nDesde: {desde} UTC" + (f"\nDetalle: {ch['_detalle']}" if ch.get("_detalle") else ""), int(cli["glpi_entity_id"]), priority=5)
    except Exception as e:  # noqa: BLE001
        log.info("Vigía de red: no se pudo abrir el ticket: %s", e)
        return None


def _cerrar_ticket(cfg, ticket_id, ch: dict) -> None:
    if not ticket_id:
        return
    try:
        from . import tickets as tickets_svc
        tickets_svc.seguimiento(cfg, int(ticket_id), f"El servicio «{ch['nombre']}» se recuperó (aviso automático de la consola).")
    except Exception:  # noqa: BLE001
        pass


def iniciar_vigia(cfg, cada_s: int = 60) -> threading.Event | None:
    """Hilo que revisa las alertas de red cada `cada_s` segundos. No corre en modo demo ni sin Zabbix."""
    if cfg.demo or not cfg.zbx_configurado:
        return None
    from .. import db as db_mod
    parar = threading.Event()

    def _loop() -> None:
        while not parar.wait(cada_s):
            try:
                conn = db_mod.connect(cfg.db_path)
                try:
                    from . import ajustes
                    sincronizar(conn, ajustes.efectiva(conn, cfg))   # SMTP y casilla de soporte editables desde la consola
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                log.warning("Vigía de red: %s", e)

    threading.Thread(target=_loop, name="itier-red", daemon=True).start()
    return parar
