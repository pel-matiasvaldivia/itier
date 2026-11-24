"""Alta de cliente: el flujo central de la consola.

Un solo formulario encadena tres pasos en dos sistemas y deja todo listo para que
el appliance del cliente se enrole:

  1. GLPI   — crea la Entidad del cliente (aísla su CMDB y sus tickets).
  2. Zabbix — crea el grupo de hosts `iTier/<cliente>`.
  3. Zabbix — crea el proxy ACTIVO `itier-<tenant>` con PSK, que es el que el
              zabbix-proxy del cliente usará para empujar métricas al VPS.

El resultado se guarda en el registro local y se genera el BUNDLE DE ENROLAMIENTO:
el fragmento de `.env` + la PSK que se pegan en `deploy/cliente/` del appliance.
Los nombres calzan exactamente con deploy/cliente/docker-compose.yml (ZBX_HOSTNAME
= itier-<tenant>, ZBX_TLSPSKIDENTITY = itier-<tenant>).

Si un paso externo falla, se guarda el registro con estado='error' y lo ya creado,
para reintentar o limpiar sin perder rastro (y queda en auditoría).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone

from .. import audit, security
from ..adapters import AdapterError, glpi as glpi_mod, mesh as mesh_mod, zabbix as zbx_mod
from . import mesh_cliente as mesh_propio
from . import ServiceError, slugify


def _existe(conn: sqlite3.Connection, nombre: str, slug: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM clients WHERE nombre = ? OR slug = ? LIMIT 1", (nombre, slug)
    ).fetchone()
    return row is not None


def listar_clientes(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY nombre ASC").fetchall()]


def obtener_cliente(conn: sqlite3.Connection, cid: int) -> dict | None:
    r = conn.execute("SELECT * FROM clients WHERE id = ?", (cid,)).fetchone()
    return dict(r) if r else None


def obtener_cliente_por_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    r = conn.execute("SELECT * FROM clients WHERE slug = ?", (slug,)).fetchone()
    return dict(r) if r else None


def alta_cliente(conn: sqlite3.Connection, cfg, actor: str, nombre: str, notas: str = "") -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ServiceError("El nombre del cliente es obligatorio.")
    slug = slugify(nombre)
    if _existe(conn, nombre, slug):
        raise ServiceError(f"Ya existe un cliente «{nombre}» (o su identificador «{slug}»).")

    psk_identity = f"itier-{slug}"          # == ZBX_TLSPSKIDENTITY del cliente
    psk_key = secrets.token_hex(32)          # 256 bits en hex (formato PSK de Zabbix)
    proxy_name = f"itier-{slug}"             # == ZBX_HOSTNAME del zabbix-proxy del cliente
    grupo_nombre = f"iTier/{nombre}"

    entity_id = groupid = proxyid = None
    error = None
    try:
        with glpi_mod.cliente(cfg) as g:
            entity_id = g.crear_entidad(nombre)
        with zbx_mod.cliente(cfg) as z:
            groupid = z.crear_grupo(grupo_nombre)
            proxyid = z.crear_proxy(proxy_name, psk_identity, psk_key)
        estado = "activo"
    except AdapterError as e:
        estado = "error"
        error = str(e)

    cur = conn.execute(
        "INSERT INTO clients(nombre, slug, glpi_entity_id, zbx_hostgroup_id, zbx_proxy_id, "
        "zbx_proxy_name, psk_identity, psk_key, estado, notas, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            nombre, slug,
            str(entity_id) if entity_id is not None else None,
            str(groupid) if groupid is not None else None,
            str(proxyid) if proxyid is not None else None,
            proxy_name, psk_identity, psk_key, estado, notas,
            datetime.now(timezone.utc).isoformat(timespec="seconds"), actor,
        ),
    )
    conn.commit()
    cid = int(cur.lastrowid)

    audit.registrar(conn, actor, "cliente.alta", {
        "cliente": nombre, "slug": slug, "glpi_entity_id": entity_id,
        "zbx_hostgroup_id": groupid, "zbx_proxy_id": proxyid,
        "proxy_name": proxy_name, "estado": estado, "error": error,
    })

    if error:
        raise ServiceError(
            f"Alta parcial de «{nombre}»: {error}. Se guardó el registro con estado=error "
            "para reintentar o limpiar; revisá GLPI/Zabbix.",
            cliente_id=cid,
        )
    autoasignar(conn, cfg, actor, cid)
    return obtener_cliente(conn, cid)


def autoasignar(conn: sqlite3.Connection, cfg, actor: str, cid: int) -> None:
    """Deja el cliente "enchufar y listo": regla de GLPI (tag itier-<slug> →
    su entidad) y acción de autorregistro de Zabbix (metadata itier-<slug> → su
    grupo). Best-effort e idempotente: si falla NO invalida el alta; queda el
    detalle para reintentar."""
    cli = obtener_cliente(conn, cid)
    if not cli or cli.get("estado") != "activo":
        return
    marca = f"itier-{cli['slug']}"
    rule_id, action_id = cli.get("glpi_rule_id"), cli.get("zbx_action_id")
    errores = []
    if cfg.mesh_enabled and not mesh_propio.externo(cli):   # idempotente: también re-comparte el grupo con el grupo de usuarios SOPORTE
        try:
            with mesh_mod.cliente(cfg) as m:
                mesh_id = m.asegurar_grupo(marca)
                # El grupo ya existe: se guarda ANTES de compartirlo, así un fallo al compartir
                # no impide que el instalador lleve el Mesh Agent.
                conn.execute("UPDATE clients SET mesh_id=? WHERE id=?", (mesh_id, cid))
                try:
                    m.compartir_con_soporte(mesh_id)
                except AdapterError as e:
                    errores.append(f"MeshCentral (compartir con {cfg.mesh_support_group}): {e.detalle}")
        except AdapterError as e:
            errores.append(f"MeshCentral (grupo): {e.detalle}")
    if not rule_id:
        try:
            with glpi_mod.cliente(cfg) as g:
                rule_id = g.asegurar_regla_tag(f"iTier · {cli['nombre']}", marca, int(cli["glpi_entity_id"]))
        except (AdapterError, TypeError, ValueError) as e:
            errores.append(f"GLPI (regla por tag): {e}")
    if not action_id:
        try:
            with zbx_mod.cliente(cfg) as z:
                action_id = z.asegurar_autoregistro(f"iTier · {cli['nombre']}", marca, cli["zbx_hostgroup_id"])
        except (AdapterError, KeyError, IndexError) as e:
            errores.append(f"Zabbix (autorregistro): {e}")
    if cfg.zbx_configurado and cli.get("zbx_hostgroup_id"):
        try:   # el grupo de usuarios de soporte de Zabbix ve los hosts de este cliente
            with zbx_mod.cliente(cfg) as z:
                z.dar_acceso_grupo(cfg.zbx_soporte_grupo, cli["zbx_hostgroup_id"])
        except (AdapterError, KeyError, IndexError) as e:
            errores.append(f"Zabbix (acceso de soporte): {getattr(e, 'detalle', e)}")
    detalle = "; ".join(errores)[:500] or None
    conn.execute("UPDATE clients SET glpi_rule_id=?, zbx_action_id=?, autoasig_detalle=? WHERE id=?",
                 (str(rule_id) if rule_id else None, str(action_id) if action_id else None, detalle, cid))
    conn.commit()
    audit.registrar(conn, actor, "cliente.autoasignacion", {
        "cliente": cli["nombre"], "glpi_rule_id": rule_id, "zbx_action_id": action_id, "error": detalle})


def bundle_env(cfg, cli: dict) -> str:
    """Fragmento de .env para configurar el appliance a mano (avanzado). Sin los
    secretos del túnel: esos van solo en el instalador."""
    glpi = cfg.public_glpi_url or "https://itier-itsm.pymesenlinea.com.ar"
    return (
        f"# ── iTiers — cliente: {cli['nombre']} (configuración manual) ──\n"
        f"ITIER_TENANT={cli['slug']}\n"
        f"ITIER_GLPI_URL={glpi}\n"
        f"ZBX_TUNNEL_HOSTNAME={cfg.zbx_tunnel_hostname or 'itier-zbx.tu-dominio.com'}\n"
        f"# Service token de Cloudflare Access (está en el .env de la consola):\n"
        f"CF_ACCESS_CLIENT_ID=\n"
        f"CF_ACCESS_CLIENT_SECRET=\n"
        f"ZBX_TLSCONNECT=psk\n"
        f"ZBX_TLSPSKIDENTITY={cli['psk_identity']}\n"
        f"ZBX_TLSPSKFILE=/var/lib/zabbix/enc/proxy.psk\n"
    )


def reprovisionar(conn: sqlite3.Connection, cfg, actor: str, cid: int) -> dict:
    """Reintenta el alta de un cliente que quedó incompleto (estado=error).

    Crea SOLO las piezas que falten (entidad GLPI / grupo / proxy), reusando la
    PSK ya generada, y actualiza el registro. Idempotente: si GLPI/Zabbix ya
    tenían algo, los adaptadores lo reutilizan.
    """
    cli = obtener_cliente(conn, cid)
    if not cli:
        raise ServiceError("Cliente no encontrado.")

    nombre, slug = cli["nombre"], cli["slug"]
    entity_id = cli.get("glpi_entity_id") or None
    groupid = cli.get("zbx_hostgroup_id") or None
    proxyid = cli.get("zbx_proxy_id") or None
    proxy_name = cli.get("zbx_proxy_name") or f"itier-{slug}"
    psk_identity = cli.get("psk_identity") or f"itier-{slug}"
    psk_key = cli.get("psk_key") or secrets.token_hex(32)
    grupo_nombre = f"iTier/{nombre}"

    error = None
    try:
        if not entity_id:
            with glpi_mod.cliente(cfg) as g:
                entity_id = g.crear_entidad(nombre)
        with zbx_mod.cliente(cfg) as z:
            if not groupid:
                groupid = z.crear_grupo(grupo_nombre)
            if not proxyid:
                proxyid = z.crear_proxy(proxy_name, psk_identity, psk_key)
        estado = "activo"
    except AdapterError as e:
        estado = "error"
        error = str(e)

    conn.execute(
        "UPDATE clients SET glpi_entity_id=?, zbx_hostgroup_id=?, zbx_proxy_id=?, "
        "zbx_proxy_name=?, psk_identity=?, psk_key=?, estado=? WHERE id=?",
        (
            str(entity_id) if entity_id is not None else None,
            str(groupid) if groupid is not None else None,
            str(proxyid) if proxyid is not None else None,
            proxy_name, psk_identity, psk_key, estado, cid,
        ),
    )
    conn.commit()
    audit.registrar(conn, actor, "cliente.reintento", {
        "cliente": nombre, "slug": slug, "glpi_entity_id": entity_id,
        "zbx_hostgroup_id": groupid, "zbx_proxy_id": proxyid,
        "estado": estado, "error": error,
    })

    if error:
        raise ServiceError(
            f"Reintento parcial de «{nombre}»: {error}. Revisá permisos/tokens y volvé a intentar.",
            cliente_id=cid,
        )
    autoasignar(conn, cfg, actor, cid)
    return obtener_cliente(conn, cid)


def eliminar_cliente(conn: sqlite3.Connection, actor: str, cid: int) -> dict:
    """Borra SOLO el registro local del cliente (no toca GLPI ni Zabbix).

    Pensado para limpiar altas fallidas y volver a crearlas. Las entidades/grupos/
    proxies que hayan quedado en GLPI/Zabbix hay que borrarlos allí a mano.
    """
    cli = obtener_cliente(conn, cid)
    if not cli:
        raise ServiceError("Cliente no encontrado.")
    # Borra también los usuarios de portal de ese cliente (quedarían huérfanos).
    n_usuarios = security.eliminar_usuarios_de_cliente(conn, cid)
    conn.execute("DELETE FROM enroll_tokens WHERE client_id=?", (cid,))  # enlaces de instalación
    conn.execute("DELETE FROM endpoint_reports WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM clients WHERE id=?", (cid,))
    conn.commit()
    audit.registrar(conn, actor, "cliente.eliminado", {
        "cliente": cli["nombre"], "slug": cli["slug"],
        "glpi_entity_id": cli.get("glpi_entity_id"),
        "zbx_proxy_id": cli.get("zbx_proxy_id"),
        "usuarios_portal_borrados": n_usuarios,
        "nota": "solo registro local; GLPI/Zabbix no se tocaron",
    })
    return cli
