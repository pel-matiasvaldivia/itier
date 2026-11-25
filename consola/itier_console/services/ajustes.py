"""Ajustes que se editan desde la consola (hoy: canales de comunicación).

Pisan a las variables de entorno: lo cargado en pantalla manda; si no hay nada, vale el .env.
Los secretos (clave del buzón, API key) se guardan cifrados con una clave derivada del
secreto de la consola (ITIER_CONSOLE_SECRET): si ese secreto cambia, hay que volver a cargarlos.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import sqlite3
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

# clave → (atributo de Config, es secreto, tipo)
CAMPOS = {
    "soporte_email": ("soporte_email", False, str),
    "imap_host": ("imap_host", False, str),
    "imap_port": ("imap_port", False, int),
    "imap_user": ("imap_user", False, str),
    "imap_password": ("imap_password", True, str),
    "imap_folder": ("imap_folder", False, str),
    "imap_tls": ("imap_tls", False, str),
    "imap_poll_s": ("imap_poll_s", False, int),
    "imap_auth": ("imap_auth", False, str),
    "imap_ms_tenant": ("imap_ms_tenant", False, str),
    "imap_ms_client_id": ("imap_ms_client_id", False, str),
    "imap_ms_secret": ("imap_ms_secret", True, str),
    "wa_api_url": ("wa_api_url", False, str),
    "wa_api_key": ("wa_api_key", True, str),
    "wa_numero": ("wa_numero", False, str),
    "m365_client_id": ("m365_client_id", False, str),
    "m365_client_secret": ("m365_client_secret", True, str),
    "gws_sa_json": ("gws_sa_json", True, str),
    "vapid_priv": ("vapid_priv", True, str),
    "vapid_pub": ("vapid_pub", False, str),
    "fx_ars_usd": ("fx_ars_usd", False, float),
    "fx_fecha": ("fx_fecha", False, str),
    "costo_plataforma_usd": ("costo_plataforma_usd", False, float),
}


def _fernet(cfg) -> Fernet:
    k = hashlib.sha256(("itier-ajustes|" + cfg.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(k))


def leer(conn: sqlite3.Connection, cfg) -> dict:
    out = {}
    for r in conn.execute("SELECT clave, valor, secreto FROM ajustes"):
        if r["clave"] not in CAMPOS or r["valor"] is None:
            continue
        v = r["valor"]
        if r["secreto"]:
            try:
                v = _fernet(cfg).decrypt(v.encode()).decode()
            except InvalidToken:
                continue                      # secreto de la consola cambió: se ignora
        out[r["clave"]] = v
    return out


def guardar(conn: sqlite3.Connection, cfg, actor: str, valores: dict) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for clave, v in valores.items():
        if clave not in CAMPOS:
            continue
        secreto = CAMPOS[clave][1]
        if v is None or str(v).strip() == "":
            conn.execute("DELETE FROM ajustes WHERE clave = ?", (clave,))
            continue
        v = str(v).strip()
        if secreto:
            v = _fernet(cfg).encrypt(v.encode()).decode()
        conn.execute(
            "INSERT INTO ajustes(clave, valor, secreto, updated_at, updated_by) VALUES (?,?,?,?,?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, secreto=excluded.secreto, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (clave, v, 1 if secreto else 0, ts, actor))
    conn.commit()


def efectiva(conn: sqlite3.Connection, cfg):
    """Config con los ajustes guardados encima de lo que vino del entorno."""
    cambios = {}
    for clave, v in leer(conn, cfg).items():
        attr, _s, tipo = CAMPOS[clave]
        try:
            cambios[attr] = tipo(v)
        except ValueError:
            pass
    if "wa_api_url" in cambios:
        cambios["wa_api_url"] = cambios["wa_api_url"].rstrip("/")
    if "imap_poll_s" in cambios:
        cambios["imap_poll_s"] = max(15, cambios["imap_poll_s"])
    return dataclasses.replace(cfg, **cambios) if cambios else cfg
