"""Administración de operadores desde la línea de comandos.

    itier-console-admin add --user ana --admin
    itier-console-admin passwd --user ana
    itier-console-admin reset-2fa --user ana      # fuerza re-enrolar el 2FA
    itier-console-admin disable --user ana
    itier-console-admin list

Opera directamente sobre la base SQLite (ITIER_CONSOLE_DB). No necesita el
secreto de sesión. Las contraseñas se piden de forma interactiva si no se pasan.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import security
from .db import connect, init_db


def _db_path() -> str:
    return os.environ.get("ITIER_CONSOLE_DB", "/data/consola.sqlite3")


def _pedir_password(valor: str | None) -> str:
    if valor:
        return valor
    p1 = getpass.getpass("Contraseña: ")
    p2 = getpass.getpass("Repetir: ")
    if p1 != p2:
        sys.exit("Las contraseñas no coinciden.")
    if len(p1) < 10:
        sys.exit("La contraseña debe tener al menos 10 caracteres.")
    return p1


def _cmd_add(conn, args) -> None:
    if security.obtener_usuario(conn, args.user):
        sys.exit(f"El usuario «{args.user}» ya existe.")
    pw = _pedir_password(args.password)
    security.crear_usuario(conn, args.user, pw, is_admin=args.admin, must_change_pw=args.force_change)
    print(f"Usuario «{args.user}» creado (admin={args.admin}). Enrolará el 2FA en el primer login.")


def _cmd_passwd(conn, args) -> None:
    u = security.obtener_usuario(conn, args.user)
    if not u:
        sys.exit(f"No existe «{args.user}».")
    pw = _pedir_password(args.password)
    security.cambiar_password(conn, int(u["id"]), pw)
    conn.execute("UPDATE users SET must_change_pw = 1, failed_count = 0, locked_until = NULL WHERE id = ?", (u["id"],))
    conn.commit()
    print(f"Contraseña de «{args.user}» actualizada (deberá cambiarla en el próximo login).")


def _cmd_reset_2fa(conn, args) -> None:
    u = security.obtener_usuario(conn, args.user)
    if not u:
        sys.exit(f"No existe «{args.user}».")
    conn.execute("UPDATE users SET totp_secret = NULL, totp_confirmed = 0 WHERE id = ?", (u["id"],))
    conn.commit()
    print(f"2FA de «{args.user}» reiniciado: re-enrolará en el próximo login.")


def _cmd_estado(conn, args, disabled: int) -> None:
    u = security.obtener_usuario(conn, args.user)
    if not u:
        sys.exit(f"No existe «{args.user}».")
    conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (disabled, u["id"]))
    conn.commit()
    print(f"Usuario «{args.user}» {'deshabilitado' if disabled else 'habilitado'}.")


def _cmd_list(conn, _args) -> None:
    filas = conn.execute(
        "SELECT username, is_admin, totp_confirmed, disabled, last_login FROM users ORDER BY username"
    ).fetchall()
    if not filas:
        print("(sin usuarios)")
        return
    print(f"{'usuario':20} {'admin':6} {'2fa':4} {'estado':10} último_login")
    for f in filas:
        print(f"{f['username']:20} {'sí' if f['is_admin'] else 'no':6} "
              f"{'ok' if f['totp_confirmed'] else '—':4} "
              f"{'baja' if f['disabled'] else 'activo':10} {f['last_login'] or '—'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="itier-console-admin", description="Administración de operadores de la consola de iTier.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="crear un operador")
    p.add_argument("--user", required=True)
    p.add_argument("--password", help="si se omite, se pide de forma interactiva")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--force-change", action="store_true", help="exigir cambio de clave en el primer login")

    p = sub.add_parser("passwd", help="restablecer la contraseña")
    p.add_argument("--user", required=True)
    p.add_argument("--password")

    p = sub.add_parser("reset-2fa", help="forzar re-enrolamiento del 2FA")
    p.add_argument("--user", required=True)

    p = sub.add_parser("disable", help="dar de baja un operador")
    p.add_argument("--user", required=True)

    p = sub.add_parser("enable", help="reactivar un operador")
    p.add_argument("--user", required=True)

    sub.add_parser("list", help="listar operadores")

    args = parser.parse_args()

    path = _db_path()
    init_db(path)
    conn = connect(path)
    try:
        if args.cmd == "add":
            _cmd_add(conn, args)
        elif args.cmd == "passwd":
            _cmd_passwd(conn, args)
        elif args.cmd == "reset-2fa":
            _cmd_reset_2fa(conn, args)
        elif args.cmd == "disable":
            _cmd_estado(conn, args, 1)
        elif args.cmd == "enable":
            _cmd_estado(conn, args, 0)
        elif args.cmd == "list":
            _cmd_list(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
