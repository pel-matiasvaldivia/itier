"""Equipo de TI de una empresa cliente: quién puede invitar, cambiar, dar de baja y eliminar a quién.

Reglas (rbac.PUEDE_INVITAR): el administrador de iTiers y el administrador de cliente gestionan todos los roles de cliente;
el PM de cliente, solo técnicos. Nadie se gestiona a sí mismo, y siempre dentro de la misma empresa."""

from __future__ import annotations

import sqlite3

from . import ServiceError, rbac, usuarios as usuarios_svc


def listar(conn: sqlite3.Connection, cid: int) -> list[dict]:
    filas = conn.execute("SELECT * FROM users WHERE client_id IS NULL AND scope_client_id = ? ORDER BY username", (cid,)).fetchall()
    return [dict(f, rol=rbac.rol_de_fila(bool(f["is_admin"]), None, f["rol"])) for f in filas]


def roles_que_puede_dar(actor_rol: str | None) -> list[tuple[str, str]]:
    permitidos = rbac.PUEDE_INVITAR.get(actor_rol or "", set())
    return [(k, v) for k, v in rbac.ROLES.items() if k in permitidos]


def _objetivo(conn: sqlite3.Connection, actor: dict, cid: int, uid: int) -> dict:
    fila = conn.execute("SELECT * FROM users WHERE id = ? AND client_id IS NULL AND scope_client_id = ?", (uid, cid)).fetchone()
    if not fila:
        raise ServiceError("Ese usuario no pertenece al equipo de esta empresa.")
    u = dict(fila, rol=rbac.rol_de_fila(bool(fila["is_admin"]), None, fila["rol"]))
    if u["id"] == actor["uid"]:
        raise ServiceError("No podés hacer esto sobre tu propio usuario.")
    if u["rol"] not in rbac.PUEDE_INVITAR.get(actor.get("rol") or "", set()):
        raise ServiceError("Tu rol no alcanza para gestionar a ese usuario.")
    return u


def invitar(conn, cfg, actor: dict, cid: int, username: str, email: str, nombre: str, rol: str, enviar: bool):
    if rol not in rbac.PUEDE_INVITAR.get(actor.get("rol") or "", set()):
        raise ServiceError("Tu rol no puede invitar a ese tipo de usuario.")
    return usuarios_svc.crear(conn, cfg, actor["user"], username, email, nombre, rol, enviar=enviar, cliente_id=cid)


def cambiar_rol(conn, cfg, actor: dict, cid: int, uid: int, rol: str) -> dict:
    u = _objetivo(conn, actor, cid, uid)
    if rol not in rbac.PUEDE_INVITAR.get(actor.get("rol") or "", set()):
        raise ServiceError("Tu rol no puede asignar ese rol.")
    return usuarios_svc.cambiar_rol(conn, cfg, actor["user"], u["id"], rol)


def reiniciar_clave(conn, cfg, actor: dict, cid: int, uid: int) -> tuple[dict, str, dict]:
    u = _objetivo(conn, actor, cid, uid)
    clave, estados = usuarios_svc.reiniciar_clave(conn, cfg, actor["user"], u["id"])
    return u, clave, estados


def activar(conn, cfg, actor: dict, cid: int, uid: int, activo: bool) -> dict:
    u = _objetivo(conn, actor, cid, uid)
    return usuarios_svc.activar(conn, cfg, actor["user"], u["id"], activo)


def eliminar(conn, cfg, actor: dict, cid: int, uid: int) -> dict:
    u = _objetivo(conn, actor, cid, uid)
    return usuarios_svc.eliminar(conn, cfg, actor["user"], actor["uid"], u["id"])
