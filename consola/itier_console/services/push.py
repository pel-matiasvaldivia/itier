"""Avisos push al celular (Web Push con claves VAPID): llegan aunque la app esté cerrada.

Cada persona activa los avisos desde Guardia, en su teléfono (en iPhone, con la app instalada). El servidor guarda la suscripción
del navegador (no hay cuentas ni servicios de terceros propios: el aviso pasa por el servicio push de Apple/Google/Mozilla, cifrado
de punta a punta con la clave de cada suscripción). Se envían en: caída/recuperación de red y tickets nuevos."""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

from . import ajustes, rbac

log = logging.getLogger("itier.push")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def asegurar_claves(conn: sqlite3.Connection, cfg):
    """Config efectiva con las claves VAPID; si todavía no hay, las genera y las guarda (la privada, cifrada)."""
    ef = ajustes.efectiva(conn, cfg)
    if ef.vapid_priv and ef.vapid_pub:
        return ef
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    pem = v.private_pem().decode()
    pub = base64.urlsafe_b64encode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
    ajustes.guardar(conn, cfg, "sistema", {"vapid_priv": pem, "vapid_pub": pub})
    return ajustes.efectiva(conn, cfg)


def suscribir(conn: sqlite3.Connection, uid: int, sub: dict, ua: str = "") -> None:
    ep, k = (sub or {}).get("endpoint", ""), (sub or {}).get("keys") or {}
    if not (ep.startswith("https://") and len(ep) < 1000 and k.get("p256dh") and k.get("auth")):
        raise ValueError("suscripción inválida")
    conn.execute("INSERT INTO push_subs(user_id, endpoint, p256dh, auth, ua, created_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth, ua=excluded.ua",
                 (uid, ep, k["p256dh"], k["auth"], (ua or "")[:200], _ahora()))
    conn.commit()


def baja(conn: sqlite3.Connection, uid: int, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?", (endpoint, uid))
    conn.commit()


def _destinatarios(conn: sqlite3.Connection, cliente_id: int | None) -> list[dict]:
    """Suscripciones de quienes pueden ver ese cliente: el personal de iTiers y los técnicos/PM/admin de cliente de esa empresa."""
    sql = ("SELECT s.*, u.rol, u.scope_client_id FROM push_subs s JOIN users u ON u.id = s.user_id "
           "WHERE u.disabled = 0 AND u.client_id IS NULL")
    sel = []
    for f in conn.execute(sql):
        if not rbac.puede(f["rol"], "tickets"):
            continue
        if f["scope_client_id"] and cliente_id and f["scope_client_id"] != cliente_id:
            continue
        if f["scope_client_id"] and not cliente_id:
            continue
        sel.append(dict(f))
    return sel


def _enviar_uno(cfg, sub: dict, payload: dict) -> int:
    """Envía una notificación. → código HTTP del servicio push (201 ok · 404/410 suscripción muerta)."""
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush
    v = Vapid.from_pem(cfg.vapid_priv.encode())
    contacto = cfg.console_public_url if (cfg.console_public_url or "").startswith("https://") else f"mailto:{cfg.soporte_email or 'soporte@localhost.invalid'}"
    try:
        r = webpush({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}, data=json.dumps(payload),
                    vapid_private_key=v, vapid_claims={"sub": contacto}, ttl=3600 if not payload.get("urgente") else 14400, timeout=10)
        return getattr(r, "status_code", 201)
    except WebPushException as e:
        return getattr(getattr(e, "response", None), "status_code", 0) or 0


def avisar(conn: sqlite3.Connection, cfg, titulo: str, cuerpo: str, url: str = "/guardia", cliente_id: int | None = None,
           urgente: bool = False, tag: str | None = None, en_segundo_plano: bool = True) -> int:
    """Manda el aviso a todos los teléfonos suscriptos que corresponden. → cantidad de destinatarios. Nunca lanza: un aviso fallido no rompe nada."""
    try:
        ef = asegurar_claves(conn, cfg)
        subs = _destinatarios(conn, cliente_id)
        if not subs:
            return 0
        payload = {"title": titulo[:80], "body": cuerpo[:160], "url": url, "urgente": urgente, **({"tag": tag} if tag else {})}
        db_path = cfg.db_path

        def _hacer() -> None:
            from .. import db as db_mod
            muertas = []
            for s in subs:
                try:
                    if _enviar_uno(ef, s, payload) in (404, 410):
                        muertas.append(s["endpoint"])
                except Exception as e:  # noqa: BLE001
                    log.info("Push: no se pudo enviar a %s…: %s", s["endpoint"][:40], e)
            if muertas:
                c = db_mod.connect(db_path)
                try:
                    c.executemany("DELETE FROM push_subs WHERE endpoint = ?", [(m,) for m in muertas])
                    c.commit()
                finally:
                    c.close()
        if en_segundo_plano:
            threading.Thread(target=_hacer, name="itier-push", daemon=True).start()
        else:
            _hacer()
        return len(subs)
    except Exception as e:  # noqa: BLE001
        log.warning("Push: %s", e)
        return 0
