"""Enrolamiento automatizado: instaladores preconfigurados por cliente.

Al dar de alta un cliente, la consola ya conoce TODOS los valores del appliance
(tenant, URL de GLPI, server Zabbix en la malla, PSK, token del nodo mesh). En vez
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

⚠ El instalador del appliance CONTIENE SECRETOS (PSK, token mesh). Por eso el
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
_RE_MESH = re.compile(r"^[A-Za-z0-9_\-=.+/]{16,4096}$")
_RE_SEGURO = re.compile(r"^[A-Za-z0-9_\-.:/@]*$")   # valores que van a un .env / script


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Token del nodo mesh ──────────────────────────────────────────────────────

def guardar_mesh_token(conn: sqlite3.Connection, actor: str, cli: dict, token: str) -> None:
    token = (token or "").strip()
    if token and not _RE_MESH.match(token):
        raise ServiceError("El token del nodo mesh tiene un formato inválido (pegá solo el token, sin espacios).")
    conn.execute("UPDATE clients SET mesh_token=? WHERE id=?", (token or None, cli["id"]))
    conn.commit()
    # Nunca el token en la auditoría: solo el hecho.
    audit.registrar(conn, actor, "enrolamiento.mesh_token", {"cliente": cli["nombre"], "cargado": bool(token)})


# ── Enlaces de instalación ───────────────────────────────────────────────────

def faltantes(cfg, cli: dict) -> list[str]:
    """Qué le falta al cliente para poder generar un instalador completo."""
    f = []
    if cli.get("estado") != "activo":
        f.append("el alta en GLPI/Zabbix no está completa (reintentá el aprovisionamiento)")
    if not cli.get("psk_key"):
        f.append("no hay PSK del proxy")
    if not cli.get("mesh_token"):
        f.append("falta el token del nodo mesh de Cloudflare")
    if not cfg.public_glpi_url and not cfg.demo:
        f.append("falta ITIER_PUBLIC_GLPI_URL en la consola")
    return f


def crear_enlace(conn: sqlite3.Connection, cfg, actor: str, cli: dict) -> str:
    """Crea un enlace de instalación y devuelve el token EN CLARO (única vez)."""
    falta = faltantes(cfg, cli)
    if falta:
        raise ServiceError("No se puede generar el instalador: " + "; ".join(falta) + ".")
    token = secrets.token_urlsafe(32)
    ahora = _ahora()
    conn.execute(
        "INSERT INTO enroll_tokens(client_id, token_hash, created_at, created_by, expires_at, max_usos) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cli["id"], _hash(token), ahora.isoformat(timespec="seconds"), actor,
         (ahora + timedelta(hours=cfg.enroll_ttl_h)).isoformat(timespec="seconds"), cfg.enroll_max_usos),
    )
    conn.commit()
    audit.registrar(conn, actor, "enrolamiento.enlace.creado", {
        "cliente": cli["nombre"], "vence_h": cfg.enroll_ttl_h, "max_usos": cfg.enroll_max_usos})
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


def _buscar(conn: sqlite3.Connection, token: str) -> dict | None:
    if not token or len(token) > 128:
        return None
    r = conn.execute("SELECT * FROM enroll_tokens WHERE token_hash=?", (_hash(token),)).fetchone()
    if not r:
        return None
    d = dict(r)
    if d["revocado"] or d["expires_at"] <= _ahora().isoformat(timespec="seconds"):
        return None
    return d


def canjear(conn: sqlite3.Connection, token: str, ip: str) -> dict | None:
    """Valida el token y consume un uso. Devuelve el cliente o None."""
    t = _buscar(conn, token)
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
        "cliente": cli["nombre"], "enlace": t["id"], "ip": ip, "uso": t["usos"] + 1})
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
    server = _seguro("ITIER_ZBX_SERVER_HOST", cfg.zbx_server_host or "172.16.0.4")
    mesh = cli.get("mesh_token") or ""
    if not _RE_MESH.match(mesh):
        raise ServiceError("Falta (o es inválido) el token del nodo mesh de este cliente.")
    return (
        f"# iTier — appliance del cliente «{cli['slug']}». Generado por la consola; no editar a mano.\n"
        f"ITIER_TENANT={_seguro('tenant', cli['slug'])}\n"
        f"ITIER_GLPI_URL={glpi}\n"
        f"MESH_NODE_TOKEN={mesh}\n"
        f"ZBX_SERVER_HOST={server}\n"
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


def instalador_windows(cfg, cli: dict) -> str:
    """Script PowerShell para puestos/servidores Windows del cliente."""
    glpi = _seguro("la URL de GLPI", cfg.public_glpi_url or "https://itier-itsm.ejemplo.com")
    plantilla = (_PLANTILLAS / "windows.ps1.tmpl").read_text(encoding="utf-8")
    return (plantilla
            .replace("@@TENANT@@", _seguro("tenant", cli["slug"]))
            .replace("@@GLPI_SERVER@@", glpi + "/plugins/glpiinventory/")
            .replace("@@GLPI_MSI@@", _seguro("URL del MSI de GLPI Agent", cfg.glpi_agent_msi_url))
            .replace("@@ZBX_MSI@@", _seguro("URL del MSI de Zabbix Agent", cfg.zbx_agent_msi_url))
            .replace("@@PROXY_IP@@", _seguro("IP del appliance", cli.get("enrolado_ip") or "")))
