"""Orquestador del bucle de eventos de iTier — el "encendido" de la Fase 3.

Toma eventos de una `FuenteDeEventos`, los **deduplica** dentro de una ventana,
frena **tormentas** de eventos por CI, arma el disparador y lo **despacha** al
bucle de razonamiento (`AgenteEdge`). Sin agente (sin clave de LLM ni gateway)
corre en modo **OFFLINE**: deja constancia del triage en la auditoría y **no
inventa diagnóstico**.

Qué NO hace este runner: decidir acciones sobre infraestructura. Eso lo resuelve
el motor de políticas, dentro de las tools de clase `act`, cuando el agente las
llama. El runner solo orquesta ingesta → dedup → despacho → auditoría.

No importa el SDK de Anthropic: el `AgenteEdge` se construye afuera (solo cuando
hay clave) y se inyecta. Así el bucle es testeable sin clave ni infraestructura.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

from .audit import Auditoria
from .events import Evento, FuenteDeEventos


def formatear_disparador(ev: Evento) -> str:
    """Arma el turno de usuario a partir del evento. El texto del evento es DATO
    no confiable (el system prompt del agente lo trata como tal)."""
    cuando = ev.ts.strftime("%Y-%m-%d %H:%M")
    lineas = [
        f"Evento recibido de {ev.origen} a las {cuando} (UTC).",
        "",
        f"  origen:     {ev.origen}",
        f"  id:         {ev.id}",
        f"  ci:         {ev.ci_id}",
        f"  severidad:  {ev.severidad}",
        f"  disparador: {ev.disparador}",
        "",
    ]
    if ev.ticket_id:
        lineas.append(
            f"Diagnosticá el incidente y proponé cómo resolverlo. "
            f"El ticket abierto es {ev.ticket_id}."
        )
    else:
        lineas.append(
            "Diagnosticá el incidente y proponé cómo resolverlo, y dejá el "
            "diagnóstico y el plan documentados en un ticket."
        )
    return "\n".join(lineas)


class Runner:
    """Orquesta el bucle de eventos. Determinista salvo por el despacho al agente."""

    def __init__(
        self,
        *,
        auditoria: Auditoria,
        datos: Any,
        agente: Any | None = None,
        ventana_dedup_min: int = 30,
        max_eventos_por_ci_hora: int = 6,
    ) -> None:
        self.auditoria = auditoria
        self.datos = datos
        self.agente = agente           # AgenteEdge o None (modo OFFLINE)
        self.ventana = dt.timedelta(minutes=ventana_dedup_min)
        self.max_eventos_por_ci_hora = max_eventos_por_ci_hora
        self._vistos: dict[str, dt.datetime] = {}
        self._por_ci: list[tuple[dt.datetime, str]] = []

    @property
    def offline(self) -> bool:
        return self.agente is None

    # -- un evento -----------------------------------------------------------

    def procesar(self, ev: Evento, ahora: dt.datetime | None = None) -> dict:
        ahora = ahora or dt.datetime.now(dt.timezone.utc)
        clave = ev.clave_correlacion()

        # 1) dedup por ventana: el mismo problema no dispara dos interacciones.
        visto = self._vistos.get(clave)
        if visto is not None and (ahora - visto) < self.ventana:
            self.auditoria.registrar("evento_duplicado", {"clave": clave, "ci": ev.ci_id})
            return {"accion": "omitido", "motivo": "duplicado dentro de la ventana", "evento": clave}
        self._vistos[clave] = ahora

        # 2) guard de tormenta por CI (radio de explosión, OWASP ASI08).
        corte = ahora - dt.timedelta(hours=1)
        self._por_ci = [(t, c) for (t, c) in self._por_ci if t > corte]
        if sum(1 for _, c in self._por_ci if c == ev.ci_id) >= self.max_eventos_por_ci_hora:
            self.auditoria.registrar("evento_suprimido", {"ci": ev.ci_id, "motivo": "tormenta de eventos"})
            return {"accion": "suprimido", "motivo": "tormenta de eventos para el CI", "ci": ev.ci_id}
        self._por_ci.append((ahora, ev.ci_id))

        # 3) registro del evento y armado del disparador.
        self.auditoria.registrar(
            "evento_recibido",
            {"origen": ev.origen, "id": ev.id, "ci": ev.ci_id,
             "severidad": ev.severidad, "ticket": ev.ticket_id},
        )
        disparador = formatear_disparador(ev)

        # 4) despacho.
        if self.offline:
            # Sin LLM: triage, no diagnóstico inventado.
            self.auditoria.registrar(
                "triage_offline",
                {"ci": ev.ci_id, "ticket": ev.ticket_id,
                 "nota": "evento triado; el razonamiento del LLM no está disponible "
                         "(sin clave ni gateway). Un técnico lo retoma desde el ticket."},
            )
            return {"accion": "triage_offline", "evento": clave, "ci": ev.ci_id,
                    "ticket": ev.ticket_id, "disparador": disparador}

        r = self.agente.atender(disparador)
        return {
            "accion": "atendido",
            "evento": clave,
            "ci": ev.ci_id,
            "turnos": r.get("turnos"),
            "aprobaciones_pendientes": r.get("aprobaciones_pendientes"),
            "costo_usd": r.get("costo_usd"),
        }

    # -- una pasada / el daemon ---------------------------------------------

    def correr(self, fuente: FuenteDeEventos, limite: int | None = None,
               ahora: dt.datetime | None = None) -> list[dict]:
        """Procesa los eventos nuevos de la fuente. Devuelve el resultado de cada uno."""
        salidas: list[dict] = []
        for i, ev in enumerate(fuente.nuevos()):
            if limite is not None and i >= limite:
                break
            salidas.append(self.procesar(ev, ahora=ahora))
        return salidas

    def bucle(self, fuente: FuenteDeEventos, intervalo_s: float,
              parar: threading.Event) -> None:
        """Poll periódico hasta que se pida parar. Un evento que falla no mata el
        bucle: se registra el error y se sigue."""
        while not parar.is_set():
            try:
                self.correr(fuente)
            except Exception as e:  # noqa: BLE001 — el bucle debe sobrevivir
                self.auditoria.registrar("error_bucle", {"error": str(e)[:200]})
            parar.wait(intervalo_s)
