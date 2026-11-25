"""Qué es cada IP que apareció en el barrido: cruza lo que ya saben GLPI (agente + descubrimiento) y Mesh con lo que se ve desde la red
(puertos abiertos, sysDescr por SNMP, nombre DNS). Prioridad: lo cargado a mano > GLPI > SNMP > puertos y nombre.

Es una clasificación **probable**: las cámaras, los DVR/NVR y los equipos sin agente solo se pueden inferir; por eso cada fila dice de dónde
sale el dato y se puede corregir a mano (queda guardado)."""

from __future__ import annotations

import re

TIPOS = ["Notebook", "Desktop", "Servidor", "Cámara IP", "DVR/NVR", "Cámara IP / NVR", "Impresora", "Router/Firewall", "Switch",
         "Access point", "NAS", "Teléfono IP", "UPS", "Celular/Tablet", "Otro"]
ICONOS = {"Notebook": "💻", "Desktop": "🖥", "Servidor": "🗄", "Cámara IP": "📹", "DVR/NVR": "📼", "Cámara IP / NVR": "📹", "Impresora": "🖨",
          "Router/Firewall": "🛡", "Switch": "🔀", "Access point": "📶", "NAS": "💾", "Teléfono IP": "☎️", "UPS": "🔋", "Celular/Tablet": "📱",
          "PC Windows": "🖥", "Equipo de red / Linux": "🐧", "Dispositivo web": "🌐", "Sin identificar": "❔", "Otro": "❔"}


def normalizar(texto: str) -> str:
    """Tipo de GLPI / texto libre → uno de TIPOS (o '' si no se reconoce)."""
    t = (texto or "").lower()
    pares = [(r"laptop|notebook|portátil|portatil|netbook", "Notebook"), (r"desktop|escritorio|workstation|all.?in.?one|torre|mini ?pc", "Desktop"),
             (r"server|servidor|hypervisor|vmware|esxi", "Servidor"), (r"\b(nvr|dvr)\b|grabador", "DVR/NVR"), (r"c[aá]mara|camera|ipcam|cctv", "Cámara IP"),
             (r"printer|impresora|multifunci|laserjet|plotter", "Impresora"), (r"router|firewall|gateway|mikrotik|fortigate|pfsense|opnsense|routeros", "Router/Firewall"),
             (r"switch|catalyst", "Switch"), (r"access ?point|\bap\b|unifi|ubiquiti|wifi|wireless", "Access point"), (r"\bnas\b|synology|qnap|storage", "NAS"),
             (r"phone|tel[eé]fono|voip|sip", "Teléfono IP"), (r"\bups\b|apc |eaton", "UPS"), (r"tablet|celular|smartphone|mobile", "Celular/Tablet")]
    for rx, tipo in pares:
        if re.search(rx, t):
            return tipo
    return ""


def _por_nombre(nombre: str) -> str:
    n = (nombre or "").lower()
    pares = [(r"(^|[-_.])(cam|ipc|hik|dahua)", "Cámara IP"), (r"(dvr|nvr|xvr)", "DVR/NVR"), (r"(print|impre|ricoh|epson|brother|laser)", "Impresora"),
             (r"(^|[-_.])(sw|switch)", "Switch"), (r"(^|[-_.])(ap|unifi|ubnt)", "Access point"), (r"(nas|synology|qnap)", "NAS"),
             (r"(ntb|notebook|laptop|^nb)", "Notebook"), (r"(srv|server|(^|[-_.])dc\d)", "Servidor"), (r"(ups)", "UPS")]
    for rx, tipo in pares:
        if re.search(rx, n):
            return tipo
    return ""


def _por_puertos(puertos: list[int], ip: str) -> tuple[str, str]:
    """(tipo, motivo) a partir de los puertos TCP abiertos. Vacío si no alcanza para opinar."""
    p = set(puertos or [])
    if {37777, 34567} & p or (8000 in p and 554 in p):
        return "Cámara IP / NVR", "puertos de cámara/NVR (554, 8000, 37777)"
    if 554 in p:
        return "Cámara IP", "puerto RTSP 554"
    if 8000 in p:
        return "Cámara IP / NVR", "puerto 8000"
    if {9100, 515, 631} & p:
        return "Impresora", "puertos de impresión (9100/515/631)"
    if 5060 in p:
        return "Teléfono IP", "puerto SIP 5060"
    if {135, 139, 445, 3389} & p:
        return "PC Windows", "puertos de Windows (135/445/3389)"
    if 5900 in p and not (80 in p or 443 in p):
        return "Desktop", "puerto VNC 5900"
    ultimo = ip.rsplit(".", 1)[-1]
    if (80 in p or 443 in p or 8080 in p or 8443 in p) and ultimo in ("1", "254"):
        return "Router/Firewall", "responde web y es una IP de gateway típica (.1/.254)"
    if 22 in p and not ({80, 443, 8080, 8443} & p):
        return "Equipo de red / Linux", "solo SSH (22)"
    if {80, 443, 8080, 8443, 22, 23, 21} & p:
        return "Dispositivo web", "solo ofrece web/SSH/telnet"
    return "", ""


def clasificar(h: dict, glpi: dict | None, manual: dict | None, conocido: dict | None) -> dict:
    """Devuelve {tipo, nombre, fabricante, origen: [..], motivo, confianza: alta|media|baja|ninguna, manual: bool}."""
    origen: list[str] = []
    nombre = h.get("dns") or ""
    tipo, fab, motivo, conf = "", "", "", "ninguna"
    if conocido:
        origen += [o for o, k in (("GLPI", "en_glpi"), ("Mesh", "en_mesh"), ("Zabbix", "zbx")) if conocido.get(k)]
        nombre = nombre or conocido.get("nombre", "")
    if glpi:
        origen.append("GLPI")
        nombre = glpi.get("nombre") or nombre
        fab = glpi.get("fabricante") or ""
        base = normalizar(glpi.get("tipo", ""))
        if not base:
            base = {"Computer": "PC Windows", "NetworkEquipment": "Switch", "Printer": "Impresora", "Phone": "Teléfono IP", "Peripheral": "Otro"}.get(glpi.get("itemtype", ""), "")
            base = normalizar(glpi.get("itemtype", "")) or base
        if glpi.get("itemtype") == "Computer" and base in ("PC Windows", ""):
            base = _por_nombre(glpi.get("nombre", "")) or "PC Windows"
        tipo, motivo, conf = base, "inventario de GLPI", "alta"
    if h.get("snmp"):
        origen.append("SNMP")
        if not tipo:
            t = normalizar(h["snmp"])
            if t:
                tipo, motivo, conf = t, f"SNMP: {h['snmp'][:60]}", "media"
        fab = fab or h["snmp"].split()[0][:30]
    if not tipo:
        t, m = _por_puertos(h.get("puertos") or [], h.get("ip", ""))
        n = _por_nombre(nombre)
        if n:
            tipo, motivo, conf = n, f"por el nombre «{nombre}»", "media"
        elif t:
            tipo, motivo, conf = t, m, "baja" if t in ("Dispositivo web", "Equipo de red / Linux", "PC Windows", "Cámara IP / NVR") else "media"
            origen.append("Puertos")
    elif tipo == "PC Windows":
        n = _por_nombre(nombre)
        tipo = n or tipo
    if conocido and conocido.get("en_mesh") and tipo in ("", "PC Windows"):
        tipo = "PC Windows" if not tipo else tipo
    if manual:
        origen.insert(0, "Manual")
        tipo = manual.get("tipo") or tipo
        nombre = manual.get("nombre") or nombre
        fab = manual.get("fabricante") or fab
        motivo, conf = "cargado a mano", "alta"
    return {"tipo": tipo or "Sin identificar", "nombre": nombre, "fabricante": fab, "origen": list(dict.fromkeys(origen)),
            "motivo": motivo, "confianza": conf, "manual": bool(manual)}
