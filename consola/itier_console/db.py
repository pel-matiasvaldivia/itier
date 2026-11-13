"""Almacén propio de la consola, en SQLite (biblioteca estándar).

La consola guarda SOLO lo suyo: operadores (con su 2FA), el registro de clientes
dados de alta y la bitácora de auditoría. GLPI y Zabbix siguen siendo la fuente
de verdad de la CMDB y el monitoreo; acá no se duplica eso.

Se abre una conexión por request (sqlite es barato) para evitar problemas de
hilos con el threadpool de FastAPI.

⚠ El registro de clientes incluye la PSK del proxy de Zabbix (un secreto). El
  archivo SQLite debe vivir en un volumen con permisos restringidos (chmod 600,
  dueño del proceso). Ver consola/README.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    totp_secret   TEXT,
    totp_confirmed INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    must_change_pw INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    failed_count  INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    created_at    TEXT    NOT NULL,
    last_login    TEXT,
    -- NULL = usuario operador (consola). Con valor = usuario del PORTAL, atado a
    -- ese cliente (ve solo su empresa). Ver docs/08.
    client_id     INTEGER
);

CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT    NOT NULL UNIQUE,
    slug            TEXT    NOT NULL UNIQUE,
    glpi_entity_id  TEXT,
    zbx_hostgroup_id TEXT,
    zbx_proxy_id    TEXT,
    zbx_proxy_name  TEXT,
    psk_identity    TEXT,
    psk_key         TEXT,
    estado          TEXT    NOT NULL DEFAULT 'provisionando',
    notas           TEXT,
    created_at      TEXT    NOT NULL,
    created_by      TEXT    NOT NULL
);

-- Descubrimiento de red por cliente: rangos de IP + credencial SNMP que la
-- consola empuja a GLPI (SNMPCredential del core + IPRange del plugin
-- GlpiInventory). Guarda los ids que devolvió GLPI y el estado del push, para
-- reintentar si GLPI/el plugin fallan (igual que el alta de cliente).
-- ⚠ Incluye secretos SNMP (community / passphrases v3): mismo cuidado que la PSK.
CREATE TABLE IF NOT EXISTS discovery_ranges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    nombre          TEXT    NOT NULL,
    ip_start        TEXT    NOT NULL,
    ip_end          TEXT    NOT NULL,
    snmp_version    TEXT    NOT NULL,          -- '1' | '2c' | '3'
    snmp_community  TEXT,                      -- v1/v2c (secreto)
    snmp_username   TEXT,                      -- v3
    snmp_auth_proto TEXT,                      -- v3: MD5|SHA|SHA256|...
    snmp_auth_pass  TEXT,                      -- v3 (secreto)
    snmp_priv_proto TEXT,                      -- v3: DES|AES|...
    snmp_priv_pass  TEXT,                      -- v3 (secreto)
    glpi_cred_id    TEXT,                      -- id de SNMPCredential en GLPI
    glpi_range_id   TEXT,                      -- id de PluginGlpiinventoryIPRange en GLPI
    estado          TEXT    NOT NULL DEFAULT 'pendiente',  -- pendiente|aplicado|error
    detalle         TEXT,
    created_at      TEXT    NOT NULL,
    created_by      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    tipo        TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    prev_hash   TEXT    NOT NULL,
    hash        TEXT    NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    """Abre una conexión con claves foráneas y WAL activos."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migrar(conn: sqlite3.Connection) -> None:
    """Migraciones idempotentes para bases ya creadas (CREATE IF NOT EXISTS no
    altera tablas existentes)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "client_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN client_id INTEGER")
    conn.commit()


def init_db(path: str) -> None:
    """Crea el esquema si no existe y aplica migraciones. Idempotente."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrar(conn)
    finally:
        conn.close()
