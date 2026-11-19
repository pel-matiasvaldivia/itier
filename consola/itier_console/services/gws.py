"""Gestión de usuarios de Google Workspace de un cliente (reglas de negocio sobre el adaptador de la Directory API)."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from ..adapters import AdapterError, gws as gws_mod
from . import ServiceError, correo
from .m365 import sugerir_usuario  # noqa: F401  (misma regla: «María Gómez» → maria.gomez)
from .usuarios import generar_clave

_RE_UID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_RE_LOCAL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def uid_valido(uid: str) -> str:
    if not _RE_UID.match(uid or ""):
        raise ServiceError("Identificador de usuario inválido.")
    return uid


def admin_valido(email: str) -> str:
    return correo.validar_email((email or "").strip().lower())


def conectado(cli: dict) -> bool:
    return bool(cli.get("gws_admin")) and cli.get("gws_estado") == "conectado"


def marcar(conn: sqlite3.Connection, cli_id: int, **campos) -> None:
    permitidos = {"gws_admin", "gws_estado", "gws_detalle", "gws_dominio", "gws_conectado_at"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    conn.execute(f"UPDATE clients SET {', '.join(k + ' = ?' for k in campos)} WHERE id = ?", (*campos.values(), cli_id))
    conn.commit()


def verificar_conexion(conn: sqlite3.Connection, cfg, cli: dict, admin: str | None = None) -> dict:
    """Prueba la delegación leyendo los dominios; deja el estado del cliente en «conectado» o «error»."""
    admin = admin_valido(admin or cli.get("gws_admin") or "")
    try:
        d = gws_mod.cliente(cfg, admin).dominios()
    except AdapterError as e:
        marcar(conn, cli["id"], gws_admin=admin, gws_estado="error", gws_detalle=e.detalle[:400])
        raise
    if admin.rsplit("@", 1)[-1] not in d["dominios"]:
        msg = "El administrador tiene que ser de un dominio verificado de ese Workspace."
        marcar(conn, cli["id"], gws_admin=admin, gws_estado="error", gws_detalle=msg)
        raise ServiceError(msg)
    marcar(conn, cli["id"], gws_admin=admin, gws_estado="conectado", gws_detalle=None, gws_dominio=d["principal"],
           gws_conectado_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return d


def proteger_admin(u: dict, accion: str) -> None:
    """No se reinicia la clave, suspende ni elimina a un administrador desde la consola: es la vía clásica de toma de cuenta."""
    if u.get("admin"):
        raise ServiceError(f"{u['email']} es administrador del dominio: {accion} hacelo desde la Admin console de Google "
                           "(la consola no opera sobre administradores).")


def crear(cfg, cli: dict, doms: dict, *, nombre: str, apellido: str, usuario: str, dominio: str, cargo: str,
          departamento: str, enviar_a: str) -> dict:
    nombre, apellido, usuario = (nombre or "").strip(), (apellido or "").strip(), (usuario or "").strip().lower()
    if not nombre or not apellido:
        raise ServiceError("Nombre y apellido son obligatorios.")
    if not _RE_LOCAL.match(usuario):
        raise ServiceError("El usuario (antes de la @) solo puede llevar letras sin tildes, números, punto, guion y guion bajo.")
    if dominio not in doms.get("dominios", []):
        raise ServiceError("Elegí un dominio verificado de Google Workspace.")
    email = f"{usuario}@{dominio}"
    if enviar_a.strip():
        enviar_a = correo.validar_email(enviar_a)
    clave = generar_clave()
    creado = gws_mod.cliente(cfg, cli["gws_admin"]).crear_usuario(
        email=email, nombre=nombre, apellido=apellido, clave=clave, cargo=(cargo or "").strip(), departamento=(departamento or "").strip())
    estado = "no solicitado"
    if enviar_a:
        try:
            correo.enviar(cfg, enviar_a, f"Tu cuenta de Google Workspace — {cli['nombre']}",
                          f"Hola {nombre},\n\nTe creamos una cuenta de Google Workspace:\n\n  Usuario: {email}\n  Clave temporal: {clave}\n\n"
                          "Ingresá en https://accounts.google.com con ese usuario; en el primer ingreso te va a pedir cambiar la clave.\n\n"
                          "Si no esperabas este correo, avisanos.\n")
            estado = f"ok (enviado a {enviar_a})"
        except Exception as e:  # noqa: BLE001
            estado = f"error: {getattr(e, 'mensaje', None) or e}"
    return {"id": creado["id"], "usuario": email, "email": email, "clave": clave, "correo": estado}
