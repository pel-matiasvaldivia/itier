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
    created_by      TEXT    NOT NULL,
    -- Enrolamiento automatizado: token del nodo mesh de Cloudflare (secreto, por
    -- cliente) y lo que el appliance reportó al instalarse ("llamó a casa").
    mesh_token      TEXT,
    enrolado_at     TEXT,
    enrolado_host   TEXT,
    enrolado_ip     TEXT,
    enrolado_estado TEXT,
    -- Autoasignación: regla de GLPI (tag → entidad) y acción de autorregistro
    -- de Zabbix (metadata → grupo) creadas en el alta.
    glpi_rule_id    TEXT,
    zbx_action_id   TEXT,
    autoasig_detalle TEXT,
    -- Clave con la que los instaladores de equipos reportan su resultado.
    report_key      TEXT
);

-- Resultado que reporta cada equipo Windows al terminar su instalación
-- (servicios corriendo + conexión). Una fila por equipo (último reporte).
CREATE TABLE IF NOT EXISTS endpoint_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL,
    host          TEXT    NOT NULL,
    estado        TEXT    NOT NULL,          -- ok | error
    glpi_servicio TEXT, glpi_conexion TEXT,
    zbx_servicio  TEXT, zbx_conexion  TEXT,
    os            TEXT,
    detalle       TEXT,
    ip_origen     TEXT,
    ts            TEXT    NOT NULL,
    UNIQUE(client_id, host)
);

-- Enlaces de instalación del appliance: token de un solo propósito, con
-- vencimiento y usos acotados. Se guarda SOLO el hash (el token se muestra una vez).
CREATE TABLE IF NOT EXISTS enroll_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    token_hash  TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL,
    created_by  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    max_usos    INTEGER NOT NULL DEFAULT 3,
    usos        INTEGER NOT NULL DEFAULT 0,
    revocado    INTEGER NOT NULL DEFAULT 0,
    tipo        TEXT    NOT NULL DEFAULT 'appliance'   -- appliance | equipos
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
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
    for col in ("mesh_token", "enrolado_at", "enrolado_host", "enrolado_ip", "enrolado_estado",
                "glpi_rule_id", "zbx_action_id", "autoasig_detalle", "report_key"):
        if col not in ccols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(enroll_tokens)").fetchall()}
    if tcols and "tipo" not in tcols:
        conn.execute("ALTER TABLE enroll_tokens ADD COLUMN tipo TEXT NOT NULL DEFAULT 'appliance'")
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
