"""Alta de cliente: el flujo central de la consola.

Un solo formulario encadena tres pasos en dos sistemas y deja todo listo para que
el appliance del cliente se enrole:

  1. GLPI   — crea la Entidad del cliente (aísla su CMDB y sus tickets).
  2. Zabbix — crea el grupo de hosts `iTier/<cliente>`.
  3. Zabbix — crea el proxy ACTIVO `itier-<tenant>` con PSK, que es el que el
              zabbix-proxy del cliente usará para empujar métricas al VPS.

El resultado se guarda en el registro local y se genera el BUNDLE DE ENROLAMIENTO:
el fragmento de `.env` + la PSK que se pegan en `deploy/cliente/` del appliance.
Los nombres calzan exactamente con deploy/cliente/docker-compose.yml (ZBX_HOSTNAME
= itier-<tenant>, ZBX_TLSPSKIDENTITY = itier-<tenant>).

Si un paso externo falla, se guarda el registro con estado='error' y lo ya creado,
para reintentar o limpiar sin perder rastro (y queda en auditoría).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone

from .. import audit
from ..adapters import AdapterError, glpi as glpi_mod, zabbix as zbx_mod
from . import ServiceError, slugify


def _existe(conn: sqlite3.Connection, nombre: str, slug: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM clients WHERE nombre = ? OR slug = ? LIMIT 1", (nombre, slug)
    ).fetchone()
    return row is not None


def listar_clientes(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY nombre ASC").fetchall()]


def obtener_cliente(conn: sqlite3.Connection, cid: int) -> dict | None:
    r = conn.execute("SELECT * FROM clients WHERE id = ?", (cid,)).fetchone()
    return dict(r) if r else None


def obtener_cliente_por_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    r = conn.execute("SELECT * FROM clients WHERE slug = ?", (slug,)).fetchone()
    return dict(r) if r else None


def alta_cliente(conn: sqlite3.Connection, cfg, actor: str, nombre: str, notas: str = "") -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ServiceError("El nombre del cliente es obligatorio.")
    slug = slugify(nombre)
    if _existe(conn, nombre, slug):
        raise ServiceError(f"Ya existe un cliente «{nombre}» (o su identificador «{slug}»).")

    psk_identity = f"itier-{slug}"          # == ZBX_TLSPSKIDENTITY del cliente
    psk_key = secrets.token_hex(32)          # 256 bits en hex (formato PSK de Zabbix)
    proxy_name = f"itier-{slug}"             # == ZBX_HOSTNAME del zabbix-proxy del cliente
    grupo_nombre = f"iTier/{nombre}"

    entity_id = groupid = proxyid = None
    error = None
    try:
        with glpi_mod.cliente(cfg) as g:
            entity_id = g.crear_entidad(nombre)
        with zbx_mod.cliente(cfg) as z:
            groupid = z.crear_grupo(grupo_nombre)
            proxyid = z.crear_proxy(proxy_name, psk_identity, psk_key)
        estado = "activo"
    except AdapterError as e:
        estado = "error"
        error = str(e)

    cur = conn.execute(
        "INSERT INTO clients(nombre, slug, glpi_entity_id, zbx_hostgroup_id, zbx_proxy_id, "
        "zbx_proxy_name, psk_identity, psk_key, estado, notas, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            nombre, slug,
            str(entity_id) if entity_id is not None else None,
            str(groupid) if groupid is not None else None,
            str(proxyid) if proxyid is not None else None,
            proxy_name, psk_identity, psk_key, estado, notas,
            datetime.now(timezone.utc).isoformat(timespec="seconds"), actor,
        ),
    )
    conn.commit()
    cid = int(cur.lastrowid)

    audit.registrar(conn, actor, "cliente.alta", {
        "cliente": nombre, "slug": slug, "glpi_entity_id": entity_id,
        "zbx_hostgroup_id": groupid, "zbx_proxy_id": proxyid,
        "proxy_name": proxy_name, "estado": estado, "error": error,
    })

    if error:
        raise ServiceError(
            f"Alta parcial de «{nombre}»: {error}. Se guardó el registro con estado=error "
            "para reintentar o limpiar; revisá GLPI/Zabbix.",
            cliente_id=cid,
        )
    return obtener_cliente(conn, cid)


def bundle_env(cfg, cli: dict) -> str:
    """Fragmento de .env drop-in para deploy/cliente/ del appliance del cliente."""
    glpi = cfg.public_glpi_url or "https://itsm.tu-dominio.com"
    server = cfg.zbx_server_host or "tu-vps.tu-dominio.com"
    return (
        f"# ── Bundle de enrolamiento iTier — cliente: {cli['nombre']} ──\n"
        f"# Generado por la consola. Pegá estas líneas en deploy/cliente/.env del appliance,\n"
        f"# y guardá la PSK (abajo) en el archivo ZBX_TLSPSKFILE.\n"
        f"ITIER_TENANT={cli['slug']}\n"
        f"ITIER_GLPI_URL={glpi}\n"
        f"ZBX_SERVER_HOST={server}\n"
        f"ZBX_TLSCONNECT=psk\n"
        f"ZBX_TLSPSKIDENTITY={cli['psk_identity']}\n"
        f"ZBX_TLSPSKFILE=/var/lib/zabbix/enc/proxy.psk\n"
    )
