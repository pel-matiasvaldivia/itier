"""Ingesta de eventos del monitoreo — la entrada del bucle de Fase 3.

Un `Evento` es lo que dispara una interacción del agente: un problema que el
monitoreo (Zabbix) levanta en la LAN del cliente. Esta capa los normaliza y se
los entrega al `Runner`, que deduplica, arma el disparador y despacha al bucle
de razonamiento.

La fuente real se **inyecta** como adaptador, igual que `datos` en tools.py: acá
se define el contrato (`FuenteDeEventos`), una fuente de demo en memoria y una
fuente de Zabbix que mapea `problem.get` de un cliente inyectado (sin HTTP propio:
el cliente de Zabbix es el adaptador, como `DatosDemo` lo es para las tools).

Nada de esto llama al LLM ni importa el SDK: es determinista y testeable sin clave.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Evento:
    """Un problema del monitoreo, normalizado. Es DATO, nunca instrucción: el
    texto de `disparador` proviene del entorno del cliente y puede estar
    manipulado (OWASP ASI01); el system prompt del agente lo trata como tal."""

    origen: str                 # "zabbix"
    id: str                     # id del problema/evento en el origen (estable)
    ci_id: str                  # CI afectado, resuelto por el adaptador
    severidad: str              # "critica" | "alta" | "media" | "advertencia" | ...
    disparador: str             # texto del trigger (contenido no confiable)
    ticket_id: str | None = None
    ts: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def clave_correlacion(self) -> str:
        """Clave para deduplicar. Un mismo problema (origen+id) es el mismo
        evento; si el origen no da id estable, cae a ci+disparador."""
        if self.id:
            return f"{self.origen}:{self.id}"
        return f"{self.origen}:{self.ci_id}:{self.disparador}"


@runtime_checkable
class FuenteDeEventos(Protocol):
    """Contrato de una fuente de eventos. `nuevos()` devuelve los eventos no
    entregados todavía (la fuente lleva su propio cursor/estado)."""

    def nuevos(self) -> list[Evento]: ...


class FuenteDemo:
    """Fuente en memoria: una lista guionada de eventos. Para demo y tests."""

    def __init__(self, eventos: list[Evento] | None = None) -> None:
        self._eventos = list(eventos) if eventos is not None else eventos_demo()

    def nuevos(self) -> list[Evento]:
        out, self._eventos = self._eventos, []
        return out


class FuenteZabbix:
    """Fuente real: mapea los problemas de Zabbix a `Evento`.

    `cliente` es un adaptador con `problemas() -> list[dict]` (el contrato), que
    en producción consulta `problem.get` de la API de Zabbix. `resolver_ci`
    traduce el host/evento de Zabbix al id de CI de GLPI. Ambos se inyectan: acá
    no hay cliente HTTP (misma disciplina que `tools.py`).
    """

    # severity de Zabbix (0..5) → texto de iTier.
    _SEV = {
        "0": "no_clasificada", "1": "informacion", "2": "advertencia",
        "3": "media", "4": "alta", "5": "critica",
    }

    def __init__(self, cliente: Any, resolver_ci: Callable[[dict], str | None]) -> None:
        self._cli = cliente
        self._resolver_ci = resolver_ci

    def nuevos(self) -> list[Evento]:
        out: list[Evento] = []
        for p in self._cli.problemas():
            out.append(
                Evento(
                    origen="zabbix",
                    id=str(p.get("eventid") or p.get("id") or ""),
                    ci_id=self._resolver_ci(p) or "",
                    severidad=self._SEV.get(str(p.get("severity", "")), "media"),
                    disparador=p.get("name") or p.get("description") or "evento sin descripción",
                )
            )
        return out


def eventos_demo() -> list[Evento]:
    """Guion de demo: el disco lleno (con un duplicado inmediato) y una caída de
    un CI crítico, para ejercitar dedup y ruteo."""
    base = dt.datetime(2026, 8, 10, 14, 32, tzinfo=dt.timezone.utc)
    disco = Evento(
        "zabbix", "evt_88213", "ci_4471", "alta",
        "uso de disco en volumen D: superó el 90% (actual: 92%)",
        ticket_id="INC-3402", ts=base,
    )
    return [
        disco,
        disco,  # duplicado inmediato: el Runner debe omitirlo
        Evento(
            "zabbix", "evt_90011", "ci_0001", "critica",
            "servicio de directorio no responde en SRV-DC01",
            ts=base + dt.timedelta(minutes=1),
        ),
    ]
