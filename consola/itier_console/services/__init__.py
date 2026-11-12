"""Lógica de negocio de la consola: orquesta los adaptadores y el almacén propio.

Un servicio puede tocar varios sistemas (dar de alta un cliente crea una entidad
en GLPI y un grupo + proxy en Zabbix, y guarda el registro local). Las rutas HTTP
quedan finas: validan, llaman a un servicio y renderizan.
"""

from __future__ import annotations

import re
import unicodedata


class ServiceError(RuntimeError):
    """Error de negocio presentable al operador (se muestra en la UI)."""

    def __init__(self, mensaje: str, *, cliente_id: int | None = None):
        self.mensaje = mensaje
        self.cliente_id = cliente_id
        super().__init__(mensaje)


def slugify(texto: str) -> str:
    """Identificador URL/tenant: minúsculas ASCII con guiones."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "cliente"
