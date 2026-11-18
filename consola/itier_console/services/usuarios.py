"""ABM de usuarios de la plataforma (técnicos, PM y administradores).

Un alta crea la MISMA cuenta —mismo usuario y clave inicial— en la consola, GLPI, Zabbix y MeshCentral,
con el perfil equivalente al rol (services/rbac.py). La clave se genera acá, se muestra UNA vez al
administrador y no se guarda (solo su hash en la consola). Cada sistema es best-effort e independiente:
si uno falla, el alta de los otros queda y el estado por sistema se muestra en la lista.

⚠ La clave solo es la misma al principio: después cada sistema la maneja por su cuenta (la consola
y Mesh fuerzan el cambio en el primer login). «Reiniciar clave» vuelve a sincronizar las tres.
"""

from __future__ import annotations

import re
import secrets
import sqlite3

from .. import audit, security
from ..adapters import AdapterError, glpi as glpi_mod, mesh as mesh_mod, zabbix as zbx_mod
from . import ServiceError, correo, rbac

_RE_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_MINUS, _MAYUS, _DIG, _SIM = "abcdefghjkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "-_!+"


def generar_clave(largo: int = 16) -> str:
    """Clave aleatoria que cumple políticas típicas (minúscula, mayúscula, dígito y símbolo)."""
    pool = _MINUS + _MAYUS + _DIG
    chars = [secrets.choice(_MINUS), secrets.choice(_MAYUS), secrets.choice(_DIG), secrets.choice(_SIM)]
    chars += [secrets.choice(pool) for _ in range(largo - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _validar(username: str, email: str, rol: str) -> None:
    if not _RE_USUARIO.match(username or ""):
        raise ServiceError("Usuario inválido: 3 a 32 caracteres en minúscula, números, punto, guion o guion bajo.")
    correo.validar_email(email or "")
    if not rbac.rol_valido(rol):
        raise ServiceError("Rol desconocido.")


def listar(conn: sqlite3.Connection) -> list[dict]:
    filas = conn.execute("SELECT * FROM users WHERE client_id IS NULL ORDER BY username").fetchall()
    return [dict(f, rol=rbac.rol_de_fila(bool(f["is_admin"]), None, f["rol"])) for f in filas]


def _fila(conn: sqlite3.Connection, uid: int) -> dict:
    f = conn.execute("SELECT * FROM users WHERE id=? AND client_id IS NULL", (uid,)).fetchone()
    if not f:
        raise ServiceError("Usuario no encontrado.")
    return dict(f, rol=rbac.rol_de_fila(bool(f["is_admin"]), None, f["rol"]))


def _admins_activos(conn: sqlite3.Connection, excepto: int | None = None) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM users WHERE client_id IS NULL AND disabled=0 AND (rol='admin' OR (rol IS NULL AND is_admin=1)) AND id<>?",
        (excepto or 0,)).fetchone()["c"]


def _estado(ok: str, ya: str = "") -> str:
    return f"ok{' (' + ya + ')' if ya else ''}"


def _aprovisionar(conn, cfg, u: dict, clave: str) -> dict[str, str]:
    """Crea/actualiza la cuenta en GLPI, Zabbix y Mesh con `clave`. Devuelve el estado por sistema."""
    estados: dict[str, str] = {}
    nombre = u.get("nombre") or u["username"]
    if cfg.glpi_configurado:
        try:
            with glpi_mod.cliente(cfg) as g:
                gid, que = g.asegurar_usuario(u["username"], clave, u["email"], nombre, u["rol"])
            conn.execute("UPDATE users SET glpi_user_id=? WHERE id=?", (gid, u["id"]))
            estados["glpi"] = "ok" if que == "creado" else "ok (ya existía: clave y perfil actualizados)"
        except (AdapterError, KeyError, TypeError) as e:
            estados["glpi"] = f"error: {getattr(e, 'detalle', e)}"[:300]
    else:
        estados["glpi"] = "no configurado"
    if cfg.zbx_configurado:
        try:
            with zbx_mod.cliente(cfg) as z:
                zid, que = z.asegurar_usuario(u["username"], clave, nombre, u["rol"], cfg.zbx_soporte_grupo)
            conn.execute("UPDATE users SET zbx_user_id=? WHERE id=?", (zid, u["id"]))
            estados["zbx"] = "ok" if que == "creado" else "ok (ya existía: clave y rol actualizados)"
        except (AdapterError, KeyError, TypeError, IndexError) as e:
            estados["zbx"] = f"error: {getattr(e, 'detalle', e)}"[:300]
    else:
        estados["zbx"] = "no configurado"
    if cfg.mesh_enabled:
        try:
            with mesh_mod.cliente(cfg) as m:
                que = m.asegurar_usuario(u["username"], clave, u["email"], u["rol"], cfg.mesh_support_group)
            estados["mesh"] = "ok" if que == "creado" else "existente: la clave de Mesh no se pudo sincronizar (cambiala en Mesh)"
        except AdapterError as e:
            estados["mesh"] = f"error: {e.detalle}"[:300]
    else:
        estados["mesh"] = "no configurado"
    conn.execute("UPDATE users SET sync_glpi=?, sync_zbx=?, sync_mesh=? WHERE id=?",
                 (estados["glpi"], estados["zbx"], estados["mesh"], u["id"]))
    conn.commit()
    return estados


def crear(conn: sqlite3.Connection, cfg, actor: str, username: str, email: str, nombre: str, rol: str) -> tuple[int, str, dict]:
    """Alta en consola + GLPI + Zabbix + Mesh. → (uid, clave en claro —mostrar UNA vez—, estados)."""
    username = (username or "").strip().lower()
    email = (email or "").strip().lower()
    nombre = (nombre or "").strip()[:80]
    _validar(username, email, rol)
    if security.obtener_usuario(conn, username):
        raise ServiceError("Ya existe un usuario con ese nombre en la consola.")
    clave = generar_clave()
    uid = security.crear_usuario(conn, username, clave, rol=rol, must_change_pw=True, email=email, nombre=nombre)
    estados = _aprovisionar(conn, cfg, _fila(conn, uid), clave)
    audit.registrar(conn, actor, "usuario.creado", {"usuario": username, "rol": rol, "email": email, "sync": estados})
    return uid, clave, estados


def reiniciar_clave(conn: sqlite3.Connection, cfg, actor: str, uid: int) -> tuple[str, dict]:
    """Clave nueva en los tres sistemas (en Mesh se recrea la cuenta: la API no permite fijar la clave de otro)."""
    u = _fila(conn, uid)
    clave = generar_clave()
    conn.execute("UPDATE users SET password_hash=?, must_change_pw=1, failed_count=0, locked_until=NULL WHERE id=?",
                 (security.hash_password(clave), uid))
    conn.commit()
    if cfg.mesh_enabled:
        try:
            with mesh_mod.cliente(cfg) as m:
                m.eliminar_usuario(u["username"])
        except AdapterError:
            pass   # si no existía, el alta lo crea
    estados = _aprovisionar(conn, cfg, u, clave)
    audit.registrar(conn, actor, "usuario.clave_reiniciada", {"usuario": u["username"], "sync": estados})
    return clave, estados


def cambiar_rol(conn: sqlite3.Connection, cfg, actor: str, uid: int, rol: str) -> dict[str, str]:
    u = _fila(conn, uid)
    if not rbac.rol_valido(rol):
        raise ServiceError("Rol desconocido.")
    if u["rol"] == "admin" and rol != "admin" and _admins_activos(conn, excepto=uid) == 0:
        raise ServiceError("Tiene que quedar al menos un administrador activo.")
    conn.execute("UPDATE users SET rol=?, is_admin=? WHERE id=?", (rol, int(rol == "admin"), uid))
    resultado: dict[str, str] = {}
    if cfg.glpi_configurado and u.get("glpi_user_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                g.cambiar_rol_usuario(u["glpi_user_id"], rol)
            resultado["glpi"] = "ok"
        except AdapterError as e:
            resultado["glpi"] = f"error: {e.detalle}"[:300]
    if cfg.zbx_configurado and u.get("zbx_user_id"):
        try:
            with zbx_mod.cliente(cfg) as z:
                z.cambiar_rol_usuario(u["zbx_user_id"], rol)
            resultado["zbx"] = "ok"
        except AdapterError as e:
            resultado["zbx"] = f"error: {e.detalle}"[:300]
    if cfg.mesh_enabled:
        try:
            with mesh_mod.cliente(cfg) as m:
                m.cambiar_rol_usuario(u["username"], rol)
            resultado["mesh"] = "ok"
        except AdapterError as e:
            resultado["mesh"] = f"error: {e.detalle}"[:300]
    for k, col in (("glpi", "sync_glpi"), ("zbx", "sync_zbx"), ("mesh", "sync_mesh")):
        if k in resultado:
            conn.execute(f"UPDATE users SET {col}=? WHERE id=?", (resultado[k], uid))
    conn.commit()
    audit.registrar(conn, actor, "usuario.rol_cambiado", {"usuario": u["username"], "de": u["rol"], "a": rol, "sync": resultado})
    return resultado


def activar(conn: sqlite3.Connection, cfg, actor: str, uid: int, activo: bool) -> dict[str, str]:
    """Baja/alta lógica en los cuatro sistemas (nada se borra: se conserva el historial)."""
    u = _fila(conn, uid)
    if not activo and u["rol"] == "admin" and _admins_activos(conn, excepto=uid) == 0:
        raise ServiceError("Tiene que quedar al menos un administrador activo.")
    conn.execute("UPDATE users SET disabled=? WHERE id=?", (0 if activo else 1, uid))
    resultado: dict[str, str] = {}
    if cfg.glpi_configurado and u.get("glpi_user_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                g.activar_usuario(u["glpi_user_id"], activo)
            resultado["glpi"] = "ok"
        except AdapterError as e:
            resultado["glpi"] = f"error: {e.detalle}"[:300]
    if cfg.zbx_configurado and u.get("zbx_user_id"):
        try:
            with zbx_mod.cliente(cfg) as z:
                z.activar_usuario(u["zbx_user_id"], activo, cfg.zbx_soporte_grupo)
            resultado["zbx"] = "ok"
        except AdapterError as e:
            resultado["zbx"] = f"error: {e.detalle}"[:300]
    if cfg.mesh_enabled:
        try:
            with mesh_mod.cliente(cfg) as m:
                m.bloquear_usuario(u["username"], not activo, u["rol"], cfg.mesh_support_group)
            resultado["mesh"] = "ok"
        except AdapterError as e:
            resultado["mesh"] = f"error: {e.detalle}"[:300]
    conn.commit()
    audit.registrar(conn, actor, "usuario.activado" if activo else "usuario.dado_de_baja",
                    {"usuario": u["username"], "sync": resultado})
    return resultado


def eliminar(conn: sqlite3.Connection, cfg, actor: str, actor_uid: int, uid: int) -> dict[str, str]:
    u = _fila(conn, uid)
    if uid == actor_uid:
        raise ServiceError("No podés eliminar tu propio usuario.")
    if u["rol"] == "admin" and _admins_activos(conn, excepto=uid) == 0:
        raise ServiceError("Tiene que quedar al menos un administrador activo.")
    resultado: dict[str, str] = {}
    if cfg.glpi_configurado and u.get("glpi_user_id"):
        try:
            with glpi_mod.cliente(cfg) as g:
                g.eliminar_usuario(u["glpi_user_id"])
            resultado["glpi"] = "ok"
        except AdapterError as e:
            resultado["glpi"] = f"error: {e.detalle}"[:300]
    if cfg.zbx_configurado and u.get("zbx_user_id"):
        try:
            with zbx_mod.cliente(cfg) as z:
                z.eliminar_usuario(u["zbx_user_id"])
            resultado["zbx"] = "ok"
        except AdapterError as e:
            resultado["zbx"] = f"error: {e.detalle}"[:300]
    if cfg.mesh_enabled:
        try:
            with mesh_mod.cliente(cfg) as m:
                m.eliminar_usuario(u["username"])
            resultado["mesh"] = "ok"
        except AdapterError as e:
            resultado["mesh"] = f"error: {e.detalle}"[:300]
    security.eliminar_usuario(conn, uid)
    audit.registrar(conn, actor, "usuario.eliminado", {"usuario": u["username"], "sync": resultado})
    return resultado
