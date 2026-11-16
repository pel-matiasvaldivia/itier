"""Enrolamiento automatizado: instaladores preconfigurados por cliente.

Al dar de alta un cliente, la consola ya conoce TODOS los valores del appliance
(tenant, URL de GLPI, túnel al server Zabbix, PSK). En vez
de que el operador copie un `.env` a mano, acá se generan artefactos listos:

  1. APPLIANCE (Linux) — un instalador `install.sh` autocontenido: trae embebidos
     el docker-compose, el `.env` y la PSK; instala Docker si falta, deja el stack
     en /opt/itier como servicio de systemd (`itier.service`) y le avisa a la
     consola cómo quedó ("llama a casa"). Se entrega como:
       · comando de una línea  curl -fsSL <consola>/enrolar/<token>/install.sh | sudo bash
         (enlace con vencimiento y usos acotados; se guarda solo el hash), o
       · archivo descargable desde la consola (operador autenticado).
  2. PUESTOS WINDOWS — `itier-<tenant>-windows.ps1`: instala GLPI Agent (inventario,
     con el tag del cliente) y Zabbix Agent 2 (apuntando al proxy del appliance)
     como servicios, en silencio.

⚠ El instalador del appliance CONTIENE SECRETOS (PSK, service token del túnel). Por eso el
  enlace público vence, tiene usos limitados, es revocable y cada descarga queda
  en auditoría.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import audit
from . import ServiceError

_PLANTILLAS = Path(__file__).resolve().parent.parent / "deploy_templates"
_RE_SEGURO = re.compile(r"^[A-Za-z0-9_\-.:/@]*$")   # valores que van a un .env / script


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Túnel al servidor Zabbix (común a todos los clientes) ───────────────────

_RE_HOST = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")
_RE_CF = re.compile(r"^[A-Za-z0-9._\-]{8,200}$")


def tunel_configurado(cfg) -> bool:
    return bool(_RE_HOST.match(cfg.zbx_tunnel_hostname or "")
                and _RE_CF.match(cfg.cf_access_client_id or "")
                and _RE_CF.match(cfg.cf_access_client_secret or ""))


# ── Enlaces de instalación ───────────────────────────────────────────────────

def faltantes(cfg, cli: dict) -> list[str]:
    """Qué le falta al cliente para poder generar un instalador completo."""
    f = []
    if cli.get("estado") != "activo":
        f.append("el alta en GLPI/Zabbix no está completa (reintentá el aprovisionamiento)")
    if not cli.get("psk_key"):
        f.append("no hay PSK del proxy")
    if not tunel_configurado(cfg):
        f.append("falta configurar el túnel a Zabbix en la consola (ITIER_ZBX_TUNNEL_HOSTNAME, "
                 "ITIER_CF_ACCESS_CLIENT_ID, ITIER_CF_ACCESS_CLIENT_SECRET)")
    if not cfg.public_glpi_url and not cfg.demo:
        f.append("falta ITIER_PUBLIC_GLPI_URL en la consola")
    return f


def crear_enlace(conn: sqlite3.Connection, cfg, actor: str, cli: dict, tipo: str = "appliance") -> str:
    """Crea un enlace de instalación y devuelve el token EN CLARO (única vez).

    tipo='appliance' → install.sh (con secretos: vida corta, pocos usos).
    tipo='equipos'   → instalador de Windows para mandarle al cliente (sin
                       secretos: vida más larga, muchos usos).
    """
    if tipo == "appliance":
        falta = faltantes(cfg, cli)
        if falta:
            raise ServiceError("No se puede generar el instalador: " + "; ".join(falta) + ".")
        ttl, usos = cfg.enroll_ttl_h, cfg.enroll_max_usos
    elif tipo == "equipos":
        ttl, usos = cfg.equipos_ttl_h, cfg.equipos_max_usos
    else:
        raise ServiceError("Tipo de enlace inválido.")
    token = secrets.token_urlsafe(32)
    ahora = _ahora()
    conn.execute(
        "INSERT INTO enroll_tokens(client_id, token_hash, created_at, created_by, expires_at, max_usos, tipo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cli["id"], _hash(token), ahora.isoformat(timespec="seconds"), actor,
         (ahora + timedelta(hours=ttl)).isoformat(timespec="seconds"), usos, tipo),
    )
    conn.commit()
    audit.registrar(conn, actor, "enrolamiento.enlace.creado", {
        "cliente": cli["nombre"], "tipo": tipo, "vence_h": ttl, "max_usos": usos})
    return token


def listar_enlaces(conn: sqlite3.Connection, cid: int) -> list[dict]:
    ahora = _ahora().isoformat(timespec="seconds")
    out = []
    for r in conn.execute("SELECT * FROM enroll_tokens WHERE client_id=? ORDER BY id DESC LIMIT 20", (cid,)):
        d = dict(r)
        d["vigente"] = (not d["revocado"]) and d["expires_at"] > ahora and d["usos"] < d["max_usos"]
        out.append(d)
    return out


def revocar_enlace(conn: sqlite3.Connection, actor: str, cli: dict, tid: int) -> None:
    conn.execute("UPDATE enroll_tokens SET revocado=1 WHERE id=? AND client_id=?", (tid, cli["id"]))
    conn.commit()
    audit.registrar(conn, actor, "enrolamiento.enlace.revocado", {"cliente": cli["nombre"], "enlace": tid})


def _buscar(conn: sqlite3.Connection, token: str, tipo: str = "appliance") -> dict | None:
    if not token or len(token) > 128:
        return None
    r = conn.execute("SELECT * FROM enroll_tokens WHERE token_hash=? AND tipo=?", (_hash(token), tipo)).fetchone()
    if not r:
        return None
    d = dict(r)
    if d["revocado"] or d["expires_at"] <= _ahora().isoformat(timespec="seconds"):
        return None
    return d


def cliente_de_enlace(conn: sqlite3.Connection, token: str, tipo: str) -> dict | None:
    """Cliente de un enlace vigente, SIN consumir usos (para la página de descarga)."""
    t = _buscar(conn, token, tipo)
    if not t or t["usos"] >= t["max_usos"]:
        return None
    r = conn.execute("SELECT * FROM clients WHERE id=?", (t["client_id"],)).fetchone()
    return dict(r) if r else None


def canjear(conn: sqlite3.Connection, token: str, ip: str, tipo: str = "appliance") -> dict | None:
    """Valida el token y consume un uso. Devuelve el cliente o None."""
    t = _buscar(conn, token, tipo)
    if not t or t["usos"] >= t["max_usos"]:
        return None
    cur = conn.execute("UPDATE enroll_tokens SET usos=usos+1 WHERE id=? AND usos<max_usos", (t["id"],))
    conn.commit()
    if cur.rowcount != 1:
        return None
    r = conn.execute("SELECT * FROM clients WHERE id=?", (t["client_id"],)).fetchone()
    if not r:
        return None
    cli = dict(r)
    audit.registrar(conn, "enrolamiento", "enrolamiento.instalador.descargado", {
        "cliente": cli["nombre"], "tipo": tipo, "enlace": t["id"], "ip": ip, "uso": t["usos"] + 1})
    return cli


def registrar_reporte(conn: sqlite3.Connection, token: str, ip_origen: str,
                      estado: str, host: str, ip_lan: str) -> bool:
    """El appliance avisa cómo quedó. No consume usos (pero exige token vigente)."""
    t = _buscar(conn, token)
    if not t:
        return False
    estado = "ok" if estado == "ok" else "error"
    host = re.sub(r"[^A-Za-z0-9_.\-]", "", host or "")[:64]
    ip_lan = ip_lan if re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip_lan or "") else ""
    conn.execute(
        "UPDATE clients SET enrolado_at=?, enrolado_host=?, enrolado_ip=?, enrolado_estado=? WHERE id=?",
        (_ahora().isoformat(timespec="seconds"), host, ip_lan, estado, t["client_id"]))
    conn.commit()
    r = conn.execute("SELECT nombre FROM clients WHERE id=?", (t["client_id"],)).fetchone()
    audit.registrar(conn, "enrolamiento", "enrolamiento.appliance.reporte", {
        "cliente": r["nombre"] if r else t["client_id"], "estado": estado,
        "host": host, "ip_lan": ip_lan, "ip_origen": ip_origen})
    return True


# ── Generación de artefactos ─────────────────────────────────────────────────

def _seguro(nombre: str, valor: str) -> str:
    if not _RE_SEGURO.match(valor or ""):
        raise ServiceError(f"El valor de {nombre} tiene caracteres no admitidos para el instalador.")
    return valor


def env_completo(cfg, cli: dict) -> str:
    """El .env COMPLETO del appliance (sin huecos que rellenar a mano)."""
    glpi = _seguro("la URL de GLPI", cfg.public_glpi_url or "https://itier-itsm.ejemplo.com")
    if not tunel_configurado(cfg):
        raise ServiceError("Falta configurar el túnel a Zabbix en la consola (ITIER_ZBX_TUNNEL_HOSTNAME y service token).")
    return (
        f"# iTier — appliance del cliente «{cli['slug']}». Generado por la consola; no editar a mano.\n"
        f"ITIER_TENANT={_seguro('tenant', cli['slug'])}\n"
        f"ITIER_GLPI_URL={glpi}\n"
        f"ZBX_TUNNEL_HOSTNAME={cfg.zbx_tunnel_hostname}\n"
        f"CF_ACCESS_CLIENT_ID={cfg.cf_access_client_id}\n"
        f"CF_ACCESS_CLIENT_SECRET={cfg.cf_access_client_secret}\n"
        f"ZBX_TLSCONNECT=psk\n"
        f"ZBX_TLSPSKIDENTITY={_seguro('PSK identity', cli['psk_identity'])}\n"
        f"ZBX_TLSPSKFILE=/var/lib/zabbix/enc/proxy.psk\n"
    )


def compose_cliente() -> str:
    return (_PLANTILLAS / "docker-compose.yml").read_text(encoding="utf-8")


def instalador_linux(cfg, cli: dict, url_reporte: str = "") -> str:
    """Script bash autocontenido que deja el appliance funcionando como servicio."""
    env = env_completo(cfg, cli)
    psk = cli.get("psk_key") or ""
    if not re.fullmatch(r"[0-9a-f]{32,512}", psk):
        raise ServiceError("La PSK del cliente no es válida.")
    compose = compose_cliente()
    for nombre, cuerpo in (("compose", compose), ("env", env)):
        if "ITIER_EOF_" in cuerpo:
            raise ServiceError(f"Contenido inesperado en {nombre}.")
    plantilla = (_PLANTILLAS / "install.sh.tmpl").read_text(encoding="utf-8")
    return (plantilla
            .replace("@@TENANT@@", cli["slug"])
            .replace("@@URL_REPORTE@@", _seguro("URL de reporte", url_reporte))
            .replace("@@COMPOSE@@", compose.rstrip("\n"))
            .replace("@@ENV@@", env.rstrip("\n"))
            .replace("@@PSK@@", psk))


def _crlf(texto: str) -> str:
    return texto.replace("\r\n", "\n").replace("\n", "\r\n")


def clave_reporte(conn: sqlite3.Connection, cli: dict) -> str:
    """Clave (por cliente) con la que sus equipos reportan el resultado de la
    instalación. No es un secreto fuerte: solo permite informar estado."""
    if cli.get("report_key"):
        return cli["report_key"]
    clave = secrets.token_urlsafe(24)
    conn.execute("UPDATE clients SET report_key=? WHERE id=? AND report_key IS NULL", (clave, cli["id"]))
    conn.commit()
    r = conn.execute("SELECT report_key FROM clients WHERE id=?", (cli["id"],)).fetchone()
    cli["report_key"] = r["report_key"]
    return cli["report_key"]


def clave_central(conn: sqlite3.Connection, cli: dict) -> str:
    """Clave del instalador del equipo CENTRAL (el que lleva el túnel). Es distinta
    de la de los equipos comunes: solo con ella un reporte puede fijar la IP a la
    que apuntan los demás agentes de Zabbix."""
    if cli.get("central_key"):
        return cli["central_key"]
    clave = secrets.token_urlsafe(24)
    conn.execute("UPDATE clients SET central_key=? WHERE id=? AND central_key IS NULL", (clave, cli["id"]))
    conn.commit()
    r = conn.execute("SELECT central_key FROM clients WHERE id=?", (cli["id"],)).fetchone()
    cli["central_key"] = r["central_key"]
    return cli["central_key"]


_RE_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_MAX_EQUIPOS = 2000
_VALORES = {"activo", "detenido", "ausente", "ok", "sin_respuesta", "dns_no_resuelve", "omitido", "pendiente"}


def registrar_equipo(conn: sqlite3.Connection, clave: str, ip_origen: str, datos: dict) -> bool:
    """Guarda (upsert por equipo) lo que reporta un instalador de Windows."""
    if not clave or len(clave) > 64:
        return False
    r = conn.execute("SELECT id, nombre FROM clients WHERE report_key=? OR central_key=?", (clave, clave)).fetchone()
    if not r:
        return False
    es_central = bool(conn.execute("SELECT 1 FROM clients WHERE id=? AND central_key=?", (r["id"], clave)).fetchone())
    rol = "central" if es_central else "equipo"
    host = re.sub(r"[^A-Za-z0-9_.\-]", "", datos.get("host") or "")[:64].upper()
    if not host:
        return False
    existe = conn.execute("SELECT 1 FROM endpoint_reports WHERE client_id=? AND host=?", (r["id"], host)).fetchone()
    if not existe:
        n = conn.execute("SELECT COUNT(*) c FROM endpoint_reports WHERE client_id=?", (r["id"],)).fetchone()["c"]
        if n >= _MAX_EQUIPOS:
            return False

    def v(k: str) -> str:
        x = (datos.get(k) or "").strip().lower()
        return x if x in _VALORES else "desconocido"

    def txt(k: str, n: int) -> str:
        return re.sub(r"[^\x20-\x7EáéíóúñÁÉÍÓÚÑ]", " ", datos.get(k) or "")[:n].strip()

    estado = "ok" if datos.get("estado") == "ok" else "error"
    conn.execute(
        "INSERT INTO endpoint_reports(client_id, host, estado, glpi_servicio, glpi_conexion, zbx_servicio, "
        "zbx_conexion, os, detalle, rol, ip_origen, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(client_id, host) DO UPDATE SET estado=excluded.estado, glpi_servicio=excluded.glpi_servicio, "
        "glpi_conexion=excluded.glpi_conexion, zbx_servicio=excluded.zbx_servicio, zbx_conexion=excluded.zbx_conexion, "
        "os=excluded.os, detalle=excluded.detalle, rol=excluded.rol, ip_origen=excluded.ip_origen, ts=excluded.ts",
        (r["id"], host, estado, v("glpi_servicio"), v("glpi_conexion"), v("zbx_servicio"), v("zbx_conexion"),
         txt("os", 80), txt("detalle", 300), rol, ip_origen, _ahora().isoformat(timespec="seconds")))
    ip_lan = (datos.get("ip") or "").strip()
    if es_central and estado == "ok" and _RE_IPV4.match(ip_lan) and all(int(o) < 256 for o in ip_lan.split(".")):
        # El equipo central es el que reciben los demás agentes de Zabbix.
        conn.execute("UPDATE clients SET enrolado_at=?, enrolado_host=?, enrolado_ip=?, enrolado_estado='ok' WHERE id=?",
                     (_ahora().isoformat(timespec="seconds"), host, ip_lan, r["id"]))
    conn.commit()
    audit.registrar(conn, "enrolamiento", "enrolamiento.equipo.reporte",
                    {"cliente": r["nombre"], "host": host, "estado": estado, "rol": rol, "ip_origen": ip_origen})
    return True


def listar_equipos(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return [dict(x) for x in conn.execute(
        "SELECT * FROM endpoint_reports WHERE client_id=? ORDER BY ts DESC LIMIT 500", (cid,))]


def instalador_windows(cfg, cli: dict, formato: str = "cmd", url_reporte: str = "", rol: str = "equipo") -> str:
    """Instalador para equipos Windows del cliente.

    formato='cmd' → un .cmd de DOBLE CLIC: pide elevación (UAC) una vez y después
                    instala todo en silencio. Es el que se le manda al cliente.
    formato='ps1' → el script PowerShell solo, para GPO / RMM / Intune (corre
                    como SYSTEM: sin UAC ni interacción).
    """
    glpi = _seguro("la URL de GLPI", cfg.public_glpi_url or "https://itier-itsm.ejemplo.com")
    if rol not in ("equipo", "central"):
        raise ServiceError("Rol de instalador desconocido.")
    # El equipo CENTRAL lleva el túnel a Zabbix (con el service token de Cloudflare
    # Access) y hace el escaneo de red: ese instalador SÍ contiene secretos.
    if rol == "central":
        if not tunel_configurado(cfg):
            raise ServiceError("Falta configurar el túnel a Zabbix en la consola "
                               "(ITIER_ZBX_TUNNEL_HOSTNAME, ITIER_CF_ACCESS_CLIENT_ID/SECRET).")
        tunel = (cfg.zbx_tunnel_hostname, cfg.cf_access_client_id, cfg.cf_access_client_secret)
        glpi_servers = glpi + "/," + glpi + "/plugins/glpiinventory/"   # inventario + tareas de descubrimiento
    else:
        tunel = ("", "", "")
        glpi_servers = glpi + "/"
    plantilla = (_PLANTILLAS / "windows.ps1.tmpl").read_text(encoding="utf-8")
    if formato == "cmd":
        plantilla = (_PLANTILLAS / "windows.cmd.tmpl").read_text(encoding="utf-8") + plantilla
    plantilla.encode("ascii")  # los .cmd/.ps1 van en ASCII puro (PowerShell 5.1 lee ANSI)
    return _crlf(plantilla
            .replace("@@TENANT@@", _seguro("tenant", cli["slug"]))
            .replace("@@GLPI_SERVER@@", glpi_servers)  # raíz: inventario nativo de GLPI 10+
            .replace("@@ROL@@", rol)
            .replace("@@CLOUDFLARED_URL@@", _seguro("URL de cloudflared", cfg.cloudflared_win_url if rol == "central" else ""))
            .replace("@@ZBX_TUNNEL_HOST@@", _seguro("host del túnel", tunel[0]))
            .replace("@@CF_ID@@", _seguro("CF client id", tunel[1]))
            .replace("@@CF_SECRET@@", _seguro("CF client secret", tunel[2]))
            .replace("@@GLPI_MSI@@", _seguro("URL del MSI de GLPI Agent", cfg.glpi_agent_msi_url))
            .replace("@@ZBX_MSI@@", _seguro("URL del MSI de Zabbix Agent", cfg.zbx_agent_msi_url))
            .replace("@@URL_REPORTE@@", _seguro("URL de reporte", url_reporte))
            .replace("@@PROXY_IP@@", _seguro("IP del equipo central", cli.get("enrolado_ip") or "" if rol == "equipo" else "127.0.0.1")))
