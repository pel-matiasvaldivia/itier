"""Roles de la plataforma y qué puede hacer cada uno en la consola.

  admin    Administrador (full): todo, incluido el ABM de usuarios y el alta/baja de clientes.
  pm       PM: gestiona clientes, instaladores, descubrimiento y curación; ve auditoría. No administra usuarios.
  tecnico  Técnico: tickets, equipos (incl. conexión remota) y lectura de clientes.

El mismo rol define el perfil que se crea en GLPI, Zabbix y MeshCentral (services/usuarios.py).
Los usuarios del portal (clientes) no tienen rol: solo ven su empresa.
"""

from __future__ import annotations

ROLES = {
    "admin": "Administrador (full)",
    "pm": "PM",
    "tecnico": "Técnico",
    "tecnico_cliente": "Técnico de cliente",
    "pm_cliente": "PM de cliente",
    "admin_cliente": "Administrador de cliente",
}

# Roles que trabajan SOLO sobre un cliente (users.scope_client_id): no tienen cuentas en GLPI/Zabbix/Mesh de iTiers.
DE_UN_CLIENTE = {"tecnico_cliente", "pm_cliente", "admin_cliente"}

# Quién puede dar de alta (invitar) a quién dentro de su empresa. El administrador de iTiers puede con todos.
PUEDE_INVITAR = {
    "admin": set(DE_UN_CLIENTE),
    "admin_cliente": set(DE_UN_CLIENTE),
    "pm_cliente": {"tecnico_cliente"},
}

# «reportes» (horas y rentabilidad) no está en ningún rol salvo admin (= todo): maneja plata.
_TODOS = {"tablero", "tickets", "equipos", "equipos.remoto", "clientes.ver", "m365", "gws", "activos"}
_PM = _TODOS | {"clientes.editar", "instaladores", "descubrimiento", "curacion", "agentes", "auditoria", "bandeja", "m365.eliminar", "gws.eliminar", "activos.eliminar"}

_CLIENTE = {"tickets", "equipos", "equipos.remoto", "clientes.ver", "m365", "gws", "activos"}

# PM y administrador de cliente: además configuran su propia empresa (Microsoft 365, Google Workspace, Mesh, canales)
# y gestionan a su equipo de TI. El administrador puede invitar a todos los roles de cliente; el PM, solo técnicos.
_CLIENTE_PM = _CLIENTE | {"clientes.editar", "m365.eliminar", "gws.eliminar", "activos.eliminar", "equipo.cliente"}

PERMISOS = {
    "tecnico_cliente": _CLIENTE,
    "pm_cliente": _CLIENTE_PM,
    "admin_cliente": _CLIENTE_PM,
    "admin": None,                      # None = todo
    "pm": _PM,
    "tecnico": _TODOS,
}


def es_de_un_cliente(rol: str | None) -> bool:
    return rol in DE_UN_CLIENTE


def rol_valido(rol: str | None) -> bool:
    return rol in ROLES


def puede(rol: str | None, permiso: str) -> bool:
    if rol not in PERMISOS:
        return False
    p = PERMISOS[rol]
    return p is None or permiso in p


def rol_de_fila(is_admin: bool, client_id, rol: str | None) -> str | None:
    """Rol efectivo de un usuario de la tabla (compatibilidad con usuarios anteriores a los roles)."""
    if client_id:
        return None
    if rol in ROLES:
        return rol
    return "admin" if is_admin else "tecnico"
