"""Seguridad de la consola: contraseñas, 2FA (TOTP), CSRF y anti fuerza bruta.

Decisión (ADR-0008): la consola gestiona sus propios usuarios. Las contraseñas se
guardan con Argon2id; el segundo factor es TOTP (compatible con Google/Microsoft
Authenticator, Aegis, etc.). Sin proveedores externos: identidad y auditoría 100%
dentro de iTier.
"""

from __future__ import annotations

import io
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()
# Hash de descarte: se "verifica" contra él cuando el usuario no existe, para que
# el tiempo de respuesta no delate si el usuario es válido o no.
_DUMMY_HASH = _ph.hash("itier-timing-equalizer")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Contraseñas ────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(stored: str, pw: str) -> bool:
    try:
        _ph.verify(stored, pw)
        return True
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _ph.check_needs_rehash(stored)
    except InvalidHashError:
        return True


# ── TOTP (segundo factor) ──────────────────────────────────────────────────

def nuevo_secreto_totp() -> str:
    return pyotp.random_base32()


def uri_totp(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verificar_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    # valid_window=1 tolera ±30 s de desfasaje de reloj.
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def qr_svg(uri: str) -> str:
    """Devuelve el QR como SVG embebible (sin declaración XML)."""
    qr = segno.make(uri, error="m")
    buff = io.BytesIO()
    qr.save(buff, kind="svg", xmldecl=False, svgns=True, scale=4, border=2, dark="#0f172a")
    return buff.getvalue().decode("utf-8")


# ── CSRF (doble envío con token en sesión) ─────────────────────────────────

def asegurar_csrf(session: dict) -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


def verificar_csrf(session: dict, token: str | None) -> bool:
    real = session.get("csrf")
    return bool(real) and bool(token) and secrets.compare_digest(real, token)


# ── Usuarios (operan sobre la conexión SQLite) ─────────────────────────────

def obtener_usuario(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def obtener_usuario_id(conn: sqlite3.Connection, uid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def contar_usuarios(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def crear_usuario(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
    must_change_pw: bool = False,
    client_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, is_admin, must_change_pw, created_at, client_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, hash_password(password), int(is_admin), int(must_change_pw), _now_iso(), client_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def listar_usuarios_cliente(conn: sqlite3.Connection, client_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE client_id = ? ORDER BY username", (client_id,)
    ).fetchall()


def eliminar_usuario(conn: sqlite3.Connection, uid: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()


def eliminar_usuarios_de_cliente(conn: sqlite3.Connection, client_id: int) -> int:
    cur = conn.execute("DELETE FROM users WHERE client_id = ?", (client_id,))
    conn.commit()
    return cur.rowcount


def set_totp_secret(conn: sqlite3.Connection, uid: int, secret: str) -> None:
    conn.execute(
        "UPDATE users SET totp_secret = ?, totp_confirmed = 0 WHERE id = ?", (secret, uid)
    )
    conn.commit()


def confirmar_totp(conn: sqlite3.Connection, uid: int) -> None:
    conn.execute("UPDATE users SET totp_confirmed = 1 WHERE id = ?", (uid,))
    conn.commit()


def cambiar_password(conn: sqlite3.Connection, uid: int, nueva: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_pw = 0 WHERE id = ?",
        (hash_password(nueva), uid),
    )
    conn.commit()


# ── Anti fuerza bruta ──────────────────────────────────────────────────────

def esta_bloqueado(user_row: sqlite3.Row) -> tuple[bool, int]:
    lu = user_row["locked_until"]
    if not lu:
        return False, 0
    try:
        until = datetime.fromisoformat(lu)
    except ValueError:
        return False, 0
    ahora = datetime.now(timezone.utc)
    if until > ahora:
        return True, int((until - ahora).total_seconds())
    return False, 0


def registrar_fallo(conn: sqlite3.Connection, user_row: sqlite3.Row, lockout_threshold: int, lockout_window_s: int) -> None:
    n = user_row["failed_count"] + 1
    locked_until = None
    if n >= lockout_threshold:
        locked_until = (
            datetime.now(timezone.utc) + timedelta(seconds=lockout_window_s)
        ).isoformat(timespec="seconds")
        n = 0
    conn.execute(
        "UPDATE users SET failed_count = ?, locked_until = ? WHERE id = ?",
        (n, locked_until, user_row["id"]),
    )
    conn.commit()


def limpiar_fallos(conn: sqlite3.Connection, uid: int) -> None:
    conn.execute(
        "UPDATE users SET failed_count = 0, locked_until = NULL, last_login = ? WHERE id = ?",
        (_now_iso(), uid),
    )
    conn.commit()


# ── Autenticación (primer factor) ──────────────────────────────────────────

@dataclass
class ResultadoAuth:
    ok: bool
    motivo: str
    user: sqlite3.Row | None = None
    bloqueado: bool = False
    restante_s: int = 0


def autenticar(conn: sqlite3.Connection, lockout_threshold: int, lockout_window_s: int, username: str, password: str) -> ResultadoAuth:
    row = obtener_usuario(conn, username)
    if row is None:
        # Igualar tiempos: verificamos contra un hash de descarte.
        verify_password(_DUMMY_HASH, password)
        return ResultadoAuth(False, "credenciales inválidas")
    if row["disabled"]:
        return ResultadoAuth(False, "usuario deshabilitado")
    bloqueado, restante = esta_bloqueado(row)
    if bloqueado:
        return ResultadoAuth(False, "cuenta bloqueada temporalmente", bloqueado=True, restante_s=restante)
    if verify_password(row["password_hash"], password):
        if needs_rehash(row["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), row["id"]),
            )
            conn.commit()
        return ResultadoAuth(True, "ok", user=row)
    registrar_fallo(conn, row, lockout_threshold, lockout_window_s)
    return ResultadoAuth(False, "credenciales inválidas")
