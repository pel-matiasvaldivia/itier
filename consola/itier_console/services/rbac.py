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
}

# «reportes» (horas y rentabilidad) no está en ningún rol salvo admin (= todo): maneja plata.
_TODOS = {"tablero", "tickets", "equipos", "equipos.remoto", "clientes.ver", "m365"}
_PM = _TODOS | {"clientes.editar", "instaladores", "descubrimiento", "curacion", "agentes", "auditoria", "bandeja", "m365.eliminar"}

PERMISOS = {
    "admin": None,                      # None = todo
    "pm": _PM,
    "tecnico": _TODOS,
}


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
