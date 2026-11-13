"""Descubrimiento de red (SNMP) por cliente, operado desde la consola.

El operador define, por cliente, los **rangos de IP** a barrer y una **credencial
SNMP**. La consola los empuja a GLPI (SNMPCredential del core + IPRange del plugin
GlpiInventory, acotado a la entidad del cliente, y el vínculo entre ambos). A
partir de ahí el `glpi-agent` del appliance toma ese trabajo al contactar al
server. Así el operador no entra al dashboard de GLPI.

Se guarda el registro local (con los ids que devolvió GLPI y el estado del push)
para poder **reintentar** si GLPI o el plugin fallan, igual que el alta de cliente.

⚠ El acoplamiento con GLPI vive en adapters/glpi.py (itemtypes/campos del plugin).
"""

from __future__ import annotations

import ipaddress
import sqlite3
from datetime import datetime, timezone

from ..adapters import AdapterError, glpi as glpi_mod
from . import ServiceError

_VERSIONES = {"1", "2c", "3"}
_AUTH_PROTOS = {"MD5", "SHA", "SHA224", "SHA256", "SHA384", "SHA512"}
_PRIV_PROTOS = {"DES", "AES", "3DES", "AES192", "AES256"}


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def listar(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    filas = conn.execute(
        "SELECT * FROM discovery_ranges WHERE client_id = ? ORDER BY id DESC", (client_id,)
    ).fetchall()
    return [dict(f) for f in filas]


def obtener(conn: sqlite3.Connection, client_id: int, rid: int) -> dict | None:
    f = conn.execute(
        "SELECT * FROM discovery_ranges WHERE id = ? AND client_id = ?", (rid, client_id)
    ).fetchone()
    return dict(f) if f else None


def _validar(nombre, ip_start, ip_end, version, community, username, auth_proto, priv_proto) -> None:
    if not nombre:
        raise ServiceError("Falta el nombre del rango.")
    try:
        a = ipaddress.IPv4Address(ip_start)
        b = ipaddress.IPv4Address(ip_end)
    except ipaddress.AddressValueError:
        raise ServiceError("IP de inicio/fin inválida (se espera IPv4, p. ej. 192.168.1.1).")
    if int(a) > int(b):
        raise ServiceError("La IP de inicio es mayor que la de fin.")
    if version not in _VERSIONES:
        raise ServiceError("Versión SNMP inválida (1, 2c o 3).")
    if version in ("1", "2c") and not community:
        raise ServiceError("SNMP v1/v2c necesita el community string.")
    if version == "3":
        if not username:
            raise ServiceError("SNMP v3 necesita usuario.")
        if auth_proto and auth_proto.upper() not in _AUTH_PROTOS:
            raise ServiceError(f"Protocolo de autenticación SNMPv3 inválido: {auth_proto}.")
        if priv_proto and priv_proto.upper() not in _PRIV_PROTOS:
            raise ServiceError(f"Protocolo de privacidad SNMPv3 inválido: {priv_proto}.")


def agregar(
    conn: sqlite3.Connection, cfg, actor: str, cli: dict, *,
    nombre: str, ip_start: str, ip_end: str, version: str,
    community: str = "", username: str = "", auth_proto: str = "", auth_pass: str = "",
    priv_proto: str = "", priv_pass: str = "",
) -> dict:
    nombre = (nombre or "").strip()
    ip_start, ip_end = (ip_start or "").strip(), (ip_end or "").strip()
    version = (version or "").strip()
    _validar(nombre, ip_start, ip_end, version, community, username, auth_proto, priv_proto)
    if not cli.get("glpi_entity_id"):
        raise ServiceError("El cliente no tiene entidad GLPI (alta incompleta). Completá el alta antes.")
    cur = conn.execute(
        """INSERT INTO discovery_ranges
           (client_id, nombre, ip_start, ip_end, snmp_version, snmp_community,
            snmp_username, snmp_auth_proto, snmp_auth_pass, snmp_priv_proto, snmp_priv_pass,
            estado, created_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pendiente', ?, ?)""",
        (cli["id"], nombre, ip_start, ip_end, version, community or None,
         username or None, (auth_proto or "").upper() or None, auth_pass or None,
         (priv_proto or "").upper() or None, priv_pass or None, _ahora(), actor),
    )
    conn.commit()
    rid = int(cur.lastrowid)
    return aplicar(conn, cfg, cli, rid)


def aplicar(conn: sqlite3.Connection, cfg, cli: dict, rid: int) -> dict:
    """Empuja (o reintenta) el rango a GLPI. Reusa los ids ya creados si los hay."""
    row = obtener(conn, int(cli["id"]), rid)
    if not row:
        raise ServiceError("Rango de descubrimiento no encontrado.")
    entity = cli.get("glpi_entity_id")
    if not entity:
        _marcar(conn, rid, "error", "El cliente no tiene entidad GLPI.")
        return obtener(conn, int(cli["id"]), rid)

    slug = cli["slug"]
    etiqueta = f"itier-{slug}-{row['nombre']}"
    try:
        with glpi_mod.cliente(cfg) as g:
            cred_id = row["glpi_cred_id"] or g.crear_credencial_snmp(
                etiqueta, row["snmp_version"],
                community=row["snmp_community"], username=row["snmp_username"],
                auth_proto=row["snmp_auth_proto"], auth_pass=row["snmp_auth_pass"],
                priv_proto=row["snmp_priv_proto"], priv_pass=row["snmp_priv_pass"],
            )
            range_id = row["glpi_range_id"] or g.crear_rango_ip(
                int(entity), etiqueta, row["ip_start"], row["ip_end"]
            )
            g.vincular_rango_credencial(int(range_id), int(cred_id))
    except AdapterError as e:
        _marcar(conn, rid, "error", e.detalle, cred_id=locals().get("cred_id"), range_id=locals().get("range_id"))
        return obtener(conn, int(cli["id"]), rid)

    conn.execute(
        "UPDATE discovery_ranges SET estado='aplicado', detalle=NULL, glpi_cred_id=?, glpi_range_id=? WHERE id=?",
        (str(cred_id), str(range_id), rid),
    )
    conn.commit()
    return obtener(conn, int(cli["id"]), rid)


def _marcar(conn, rid, estado, detalle, *, cred_id=None, range_id=None) -> None:
    # Guarda los ids que sí se crearon, para que el reintento no los duplique.
    conn.execute(
        """UPDATE discovery_ranges SET estado=?, detalle=?,
           glpi_cred_id=COALESCE(?, glpi_cred_id), glpi_range_id=COALESCE(?, glpi_range_id)
           WHERE id=?""",
        (estado, (detalle or "")[:300],
         str(cred_id) if cred_id else None, str(range_id) if range_id else None, rid),
    )
    conn.commit()


def eliminar(conn: sqlite3.Connection, client_id: int, rid: int) -> dict | None:
    """Borra el registro local. No toca GLPI (el rango/credencial quedan allá)."""
    row = obtener(conn, client_id, rid)
    if row:
        conn.execute("DELETE FROM discovery_ranges WHERE id = ? AND client_id = ?", (rid, client_id))
        conn.commit()
    return row
