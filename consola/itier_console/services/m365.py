"""Gestión de usuarios de Microsoft 365 de un cliente (reglas de negocio sobre el adaptador de Graph)."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

from ..adapters import AdapterError, m365 as m365_mod
from . import ServiceError, correo
from .usuarios import generar_clave

_RE_UID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_RE_LOCAL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RE_TENANT = re.compile(r"^([0-9a-fA-F-]{36}|[A-Za-z0-9.-]{3,253})$")


def uid_valido(uid: str) -> str:
    if not _RE_UID.match(uid or ""):
        raise ServiceError("Identificador de usuario inválido.")
    return uid


def tenant_valido(t: str) -> str:
    t = (t or "").strip().lower()
    if t and not _RE_TENANT.match(t):
        raise ServiceError("El tenant no es válido (dominio como empresa.onmicrosoft.com o el ID del directorio).")
    return t


def sugerir_usuario(nombre: str, apellido: str) -> str:
    """«María» + «Gómez» → «maria.gomez»."""
    def limpio(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]", "", s)
    n, a = limpio(nombre), limpio(apellido)
    return ".".join(x for x in (n, a) if x)


def conectado(cli: dict) -> bool:
    return bool(cli.get("m365_tenant_id")) and cli.get("m365_estado") == "conectado"


def cliente_m365(cfg, cli: dict) -> m365_mod.M365Client:
    if not cli.get("m365_tenant_id"):
        raise ServiceError("Este cliente no tiene un tenant de Microsoft 365 conectado.")
    return m365_mod.cliente(cfg, cli["m365_tenant_id"])


def marcar(conn: sqlite3.Connection, cli_id: int, **campos) -> None:
    permitidos = {"m365_tenant_id", "m365_estado", "m365_detalle", "m365_dominio", "m365_nombre", "m365_conectado_at"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    conn.execute(f"UPDATE clients SET {', '.join(k + ' = ?' for k in campos)} WHERE id = ?", (*campos.values(), cli_id))
    conn.commit()


def verificar_conexion(conn: sqlite3.Connection, cfg, cli: dict, tenant: str | None = None) -> dict:
    """Prueba el token y lee la organización; deja el estado del cliente en «conectado» o «error»."""
    tenant = tenant or cli.get("m365_tenant_id")
    if not tenant:
        raise ServiceError("Falta el tenant.")
    try:
        org = m365_mod.cliente(cfg, tenant).organizacion()
    except AdapterError as e:
        marcar(conn, cli["id"], m365_tenant_id=tenant, m365_estado="error", m365_detalle=e.detalle[:300])
        raise
    marcar(conn, cli["id"], m365_tenant_id=tenant, m365_estado="conectado", m365_detalle=None,
           m365_dominio=org.get("dominio_principal"), m365_nombre=org.get("nombre"),
           m365_conectado_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return org


def validar_alta(org: dict, nombre: str, apellido: str, usuario: str, dominio: str) -> str:
    nombre, apellido, usuario = (nombre or "").strip(), (apellido or "").strip(), (usuario or "").strip().lower()
    if not nombre or not apellido:
        raise ServiceError("Nombre y apellido son obligatorios.")
    if not _RE_LOCAL.match(usuario):
        raise ServiceError("El usuario (antes de la @) solo puede llevar letras sin tildes, números, punto, guion y guion bajo.")
    if dominio not in org.get("dominios", []):
        raise ServiceError("Elegí un dominio verificado del tenant.")
    return f"{usuario}@{dominio}"


def crear(cfg, cli: dict, org: dict, *, nombre: str, apellido: str, usuario: str, dominio: str, cargo: str,
          departamento: str, sku_ids: list[str], enviar_a: str) -> dict:
    """Crea el usuario con clave temporal. Devuelve {usuario, clave, upn, correo: estado}."""
    upn = validar_alta(org, nombre, apellido, usuario, dominio)
    if enviar_a.strip():
        enviar_a = correo.validar_email(enviar_a)
    clave = generar_clave()
    g = m365_mod.cliente(cfg, cli["m365_tenant_id"])
    creado = g.crear_usuario(upn=upn, nombre=nombre.strip(), apellido=apellido.strip(), clave=clave,
                             cargo=(cargo or "").strip(), departamento=(departamento or "").strip(), sku_ids=sku_ids)
    estado = "no solicitado"
    if enviar_a:
        try:
            asunto = f"Tu cuenta de Microsoft 365 — {org.get('nombre') or cli['nombre']}"
            cuerpo = (f"Hola {nombre.strip()},\n\nTe creamos una cuenta de Microsoft 365:\n\n  Usuario: {upn}\n  Clave temporal: {clave}\n\n"
                      "Ingresá en https://www.office.com con ese usuario; en el primer ingreso te va a pedir cambiar la clave.\n\n"
                      "Si no esperabas este correo, avisanos.\n")
            correo.enviar(cfg, enviar_a, asunto, cuerpo)
            estado = f"ok (enviado a {enviar_a})"
        except Exception as e:  # noqa: BLE001
            estado = f"error: {getattr(e, 'mensaje', None) or e}"
    return {"id": creado["id"], "upn": upn, "usuario": upn, "clave": clave, "correo": estado}
