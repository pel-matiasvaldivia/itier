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
    client_id     INTEGER,
    -- Rol de plataforma de un operador: admin | pm | tecnico (NULL en usuarios del portal).
    rol           TEXT,
    email         TEXT,
    nombre        TEXT,
    -- Cuentas equivalentes en los otros sistemas y su estado de sincronización.
    glpi_user_id  TEXT, zbx_user_id TEXT,
    sync_glpi     TEXT, sync_zbx TEXT, sync_mesh TEXT
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
    -- Enrolamiento automatizado: (mesh_token: en desuso desde ADR-0010; se conserva por compatibilidad) (por
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
    report_key      TEXT,
    central_key     TEXT,
    mesh_id         TEXT      -- id corto del grupo de dispositivos del cliente en MeshCentral
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
    mesh_servicio TEXT, mesh_conexion TEXT,
    os            TEXT,
    detalle       TEXT,
    rol           TEXT    NOT NULL DEFAULT 'equipo',   -- equipo | central
    ip_origen     TEXT,
    ts            TEXT    NOT NULL,
    UNIQUE(client_id, host)
);

-- ── Canales de entrada (correo y WhatsApp) ───────────────────────────────────
-- Dominios de correo de un cliente: un mensaje de *@dominio se asocia a ese cliente.
CREATE TABLE IF NOT EXISTS client_dominios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL,
    dominio    TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL,
    created_by TEXT
);
-- Personas autorizadas a pedir soporte: por correo y/o WhatsApp (número normalizado, solo dígitos).
CREATE TABLE IF NOT EXISTS client_contactos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL,
    nombre     TEXT,
    email      TEXT,
    whatsapp   TEXT,
    created_at TEXT    NOT NULL,
    created_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacto_email ON client_contactos(email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacto_wa ON client_contactos(whatsapp) WHERE whatsapp IS NOT NULL;
-- Todo lo que entra por un canal (bandeja): con su resultado, para auditar y para triage.
CREATE TABLE IF NOT EXISTS entrantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canal       TEXT    NOT NULL,            -- email | whatsapp
    remitente   TEXT    NOT NULL,            -- correo o número
    nombre      TEXT,
    asunto      TEXT,
    cuerpo      TEXT,
    recibido_at TEXT    NOT NULL,
    client_id   INTEGER,                     -- NULL = no se pudo asociar a un cliente
    estado      TEXT    NOT NULL,            -- ticket | seguimiento | sin_cliente | revisar | descartado | ignorado | error
    ticket_id   INTEGER,
    msg_id      TEXT,
    detalle     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entrante_msg ON entrantes(canal, msg_id) WHERE msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_entrante_rem ON entrantes(canal, remitente, recibido_at);
-- De qué canal vino cada ticket (los de la consola no figuran) y a qué cliente pertenece.
CREATE TABLE IF NOT EXISTS ticket_origen (
    ticket_id  INTEGER PRIMARY KEY,
    client_id  INTEGER,
    canal      TEXT NOT NULL,
    remitente  TEXT,
    created_at TEXT NOT NULL
);
-- Conversación abierta de un remitente (WhatsApp): sus mensajes siguientes van al mismo ticket.
CREATE TABLE IF NOT EXISTS conversaciones (
    canal     TEXT NOT NULL,
    remitente TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    last_at   TEXT NOT NULL,
    PRIMARY KEY (canal, remitente)
);

-- Ajustes editables desde la consola (canales de comunicación). Los secretos van cifrados.
CREATE TABLE IF NOT EXISTS ajustes (
    clave      TEXT PRIMARY KEY,
    valor      TEXT,
    secreto    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

-- Costo por hora (USD) de cada persona que carga horas: para el reporte de rentabilidad.
CREATE TABLE IF NOT EXISTS tarifas (
    username TEXT PRIMARY KEY,
    usd_hora REAL NOT NULL
);

-- Activos (notebooks, desktops, monitores, celulares…) de cada cliente y a quién están asignados.
CREATE TABLE IF NOT EXISTS activos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id      INTEGER NOT NULL,
    tipo           TEXT    NOT NULL,         -- notebook | desktop | monitor | celular | otro
    etiqueta       TEXT,                     -- código interno (único por cliente)
    marca          TEXT,
    modelo         TEXT,
    serie          TEXT,
    imei           TEXT,
    linea          TEXT,
    fecha_compra   TEXT,
    garantia_hasta TEXT,
    estado         TEXT    NOT NULL DEFAULT 'en_stock',   -- en_uso | en_stock | en_reparacion | de_baja
    persona_id     INTEGER,                  -- client_contactos.id
    notas          TEXT,
    created_at     TEXT    NOT NULL,
    created_by     TEXT,
    updated_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_activo_etiqueta ON activos(client_id, etiqueta) WHERE etiqueta IS NOT NULL AND etiqueta != '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_activo_serie ON activos(client_id, tipo, serie) WHERE serie IS NOT NULL AND serie != '';
CREATE INDEX IF NOT EXISTS ix_activo_cliente ON activos(client_id, tipo);
CREATE TABLE IF NOT EXISTS activo_historial (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    activo_id  INTEGER NOT NULL,
    accion     TEXT    NOT NULL,             -- alta | asignado | devuelto | estado | editado
    persona    TEXT,                         -- nombre de la persona (copia: sobrevive si se desactiva)
    detalle    TEXT,
    por        TEXT,
    at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_historial_activo ON activo_historial(activo_id, id);

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
                "glpi_rule_id", "zbx_action_id", "autoasig_detalle", "report_key", "central_key", "mesh_id"):
        if col not in ccols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
    for col in ("m365_tenant_id", "m365_estado", "m365_detalle", "m365_dominio", "m365_nombre", "m365_conectado_at"):
        if col not in ccols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
    for col in ("gws_admin", "gws_estado", "gws_detalle", "gws_dominio", "gws_conectado_at"):
        if col not in ccols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
    kcols = {r["name"] for r in conn.execute("PRAGMA table_info(client_contactos)").fetchall()}
    if kcols:
        for col, ddl in (("cargo", "TEXT"), ("area", "TEXT"), ("activo", "INTEGER NOT NULL DEFAULT 1")):
            if col not in kcols:
                conn.execute(f"ALTER TABLE client_contactos ADD COLUMN {col} {ddl}")
    if "facturacion_ars" not in ccols:
        conn.execute("ALTER TABLE clients ADD COLUMN facturacion_ars REAL")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for col in ("rol", "email", "nombre", "glpi_user_id", "zbx_user_id", "sync_glpi", "sync_zbx", "sync_mesh"):
        if col not in ucols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    conn.execute("UPDATE users SET rol='admin' WHERE rol IS NULL AND client_id IS NULL AND is_admin=1")
    conn.execute("UPDATE users SET rol='tecnico' WHERE rol IS NULL AND client_id IS NULL AND is_admin=0")
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(endpoint_reports)").fetchall()}
    if ecols and "rol" not in ecols:
        conn.execute("ALTER TABLE endpoint_reports ADD COLUMN rol TEXT NOT NULL DEFAULT 'equipo'")
    for col in ("mesh_servicio", "mesh_conexion"):
        if ecols and col not in ecols:
            conn.execute(f"ALTER TABLE endpoint_reports ADD COLUMN {col} TEXT")
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
