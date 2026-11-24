"""MeshCentral propio de un cliente: su servidor (con su dominio, p. ej. https://remoto.empresa.com/cliente), su usuario de
servicio y su clave (cifrada). Sirve para clientes que ya tienen sus equipos conectados a otro servidor Mesh y se
quieren operar desde iTiers sin tocar los agentes instalados."""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from urllib.parse import urlparse

from . import ServiceError, ajustes

_RE_HOST = re.compile(r"^[A-Za-z0-9.-]{3,253}$")


def externo(cli: dict) -> bool:
    return bool(cli.get("mesh_url") and cli.get("mesh_user") and cli.get("mesh_pass"))


def url_valida(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    p = urlparse(u)
    if p.scheme not in ("https", "http") or not p.hostname or not _RE_HOST.match(p.hostname) or p.query or p.fragment:
        raise ServiceError("La dirección de MeshCentral tiene que ser como https://remoto.empresa.com o https://remoto.empresa.com/cliente.")
    if not re.match(r"^(/[A-Za-z0-9._-]+)?$", p.path):
        raise ServiceError("La ruta del dominio de Mesh solo admite letras, números, punto, guion y guion bajo (ej.: /thevines).")
    return u


def cfg_de(cfg, cli: dict):
    """Config con el Mesh propio del cliente; si no tiene, la general del servidor de iTiers."""
    if not externo(cli):
        return cfg
    try:
        clave = ajustes._fernet(cfg).decrypt(cli["mesh_pass"].encode()).decode()
    except Exception:  # noqa: BLE001
        raise ServiceError("No se pudo leer la clave guardada del Mesh de este cliente; cargala de nuevo.")
    return dataclasses.replace(cfg, mesh_url=cli["mesh_url"], mesh_user=cli["mesh_user"], mesh_password=clave)


def guardar(conn: sqlite3.Connection, cfg, cli_id: int, url: str, usuario: str, clave: str) -> None:
    url = url_valida(url)
    usuario = (usuario or "").strip()
    if not re.match(r"^[A-Za-z0-9._@+-]{2,64}$", usuario):
        raise ServiceError("Usuario de Mesh inválido.")
    if clave.strip():
        conn.execute("UPDATE clients SET mesh_url=?, mesh_user=?, mesh_pass=? WHERE id=?",
                     (url, usuario, ajustes._fernet(cfg).encrypt(clave.strip().encode()).decode(), cli_id))
    else:
        r = conn.execute("UPDATE clients SET mesh_url=?, mesh_user=? WHERE id=? AND mesh_pass IS NOT NULL", (url, usuario, cli_id))
        if r.rowcount == 0:
            raise ServiceError("Falta la clave del usuario de servicio de Mesh.")
    conn.commit()


def quitar(conn: sqlite3.Connection, cli_id: int) -> None:
    conn.execute("UPDATE clients SET mesh_url=NULL, mesh_user=NULL, mesh_pass=NULL WHERE id=?", (cli_id,))
    conn.commit()
