"""Adaptadores hacia los sistemas externos (GLPI, Zabbix).

Cada adaptador encapsula una API: si mañana se cambia el núcleo ITSM o el motor
de monitoreo, se toca el adaptador y no el resto de la consola (mismo principio
que la capa MCP del agente — ADR-0003). Todos soportan un modo demo que devuelve
fixtures, para desarrollar sin backends reales.
"""

from __future__ import annotations


class AdapterError(RuntimeError):
    """Falla al hablar con un sistema externo (GLPI/Zabbix)."""

    def __init__(self, sistema: str, detalle: str):
        self.sistema = sistema
        self.detalle = detalle
        super().__init__(f"[{sistema}] {detalle}")
