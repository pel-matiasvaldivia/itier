"""Punto de entrada de servicio del agente iTiers.

Valida la configuración, expone un health check y, de forma **opt-in**
(ITIER_AGENT_LOOP=1), arranca el bucle de eventos de la Fase 3
(itier_agent.runner) en un hilo. Sin esa variable queda en modo esqueleto (solo
health), igual que en Fase 1/2. El bucle usa por ahora una **fuente demo**; la
fuente real de Zabbix (events.FuenteZabbix) se cablea con el adaptador
correspondiente del edge (ver reference/README). Sin clave de LLM ni gateway, el
bucle corre en modo OFFLINE: tria eventos y los deja en auditoría, sin diagnóstico.

    python -m itier_agent.serve

Salud:
    GET /healthz  → 200 siempre que el proceso viva (liveness)
    GET /readyz   → 200 si la configuración mínima está presente, 503 si falta algo
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("itier.serve")

PUERTO = int(os.environ.get("ITIER_HEALTH_PORT", "8080"))

# Modo del servicio, para /readyz: "esqueleto" | "bucle_offline" | "bucle_llm".
_MODO = "esqueleto"

# Configuración mínima que el agente necesita para operar contra el VPS.
# ITIER_LLM_GATEWAY y ANTHROPIC_API_KEY son alternativas: alcanza con una.
_REQUERIDAS = ["ITIER_TENANT", "ITIER_GLPI_URL"]
_LLM = ["ITIER_LLM_GATEWAY", "ANTHROPIC_API_KEY"]


def faltantes() -> list[str]:
    """Devuelve la lista de variables de entorno ausentes."""
    faltan = [k for k in _REQUERIDAS if not os.environ.get(k)]
    if not any(os.environ.get(k) for k in _LLM):
        faltan.append("ITIER_LLM_GATEWAY|ANTHROPIC_API_KEY")
    return faltan


def _bool_env(nombre: str) -> bool:
    return os.environ.get(nombre, "").strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _rutas_policy() -> tuple[str, str] | None:
    """Ubica el catálogo de acciones y la política del tenant: por entorno
    (ITIER_POLICY_CATALOG / ITIER_POLICY_TENANT) o, en desarrollo, relativo al
    repo. Devuelve None si no están. En el appliance el catálogo firmado lo
    entrega el plano de control (ver reference/README → «Lo que falta»)."""
    from pathlib import Path

    cat = os.environ.get("ITIER_POLICY_CATALOG")
    ten = os.environ.get("ITIER_POLICY_TENANT")
    if not (cat and ten):
        raiz = Path(__file__).resolve().parents[3] / "reference" / "policy"
        cat = cat or str(raiz / "acciones.yaml")
        ten = ten or str(raiz / "tenant-ejemplo.yaml")
    return (cat, ten) if Path(cat).is_file() and Path(ten).is_file() else None


def _arrancar_bucle(parar: "threading.Event") -> str:
    """Arranca el bucle de eventos (Fase 3) en un hilo daemon, si ITIER_AGENT_LOOP
    está activo. Devuelve el modo para /readyz. Opt-in: sin la variable, el
    servicio queda en modo esqueleto, igual que antes."""
    if not _bool_env("ITIER_AGENT_LOOP"):
        return "esqueleto"

    from .audit import Auditoria
    from .events import FuenteDemo, FuenteZabbix
    from .fixtures import DatosDemo
    from .runner import Runner

    tenant = os.environ.get("ITIER_TENANT", "demo")
    auditoria = Auditoria(tenant=tenant)

    # ── Clientes reales (se comparten entre fuente, tickets y adaptador) ──────
    zbx_cli = None
    zbx_url = os.environ.get("ITIER_ZBX_URL")
    zbx_token = os.environ.get("ITIER_ZBX_TOKEN")
    if zbx_url and zbx_token:
        from .zabbix import ZabbixEventos

        zbx_cli = ZabbixEventos(url=zbx_url, token=zbx_token, grupo=os.environ.get("ITIER_ZBX_GROUP"))

    glpi_cli = None
    glpi_url = os.environ.get("ITIER_GLPI_URL")
    glpi_app = os.environ.get("ITIER_GLPI_APP_TOKEN")
    glpi_user = os.environ.get("ITIER_GLPI_USER_TOKEN")
    if glpi_url and glpi_app and glpi_user:
        from .glpi import GLPITickets

        glpi_cli = GLPITickets(
            url=glpi_url, app_token=glpi_app, user_token=glpi_user,
            entity_id=int(os.environ.get("ITIER_GLPI_ENTITY", "0") or 0),
        )

    # ── Datos del agente: REAL si hay GLPI + Zabbix; si no, demo ──────────────
    if glpi_cli is not None and zbx_cli is not None:
        from .datos import AdaptadorReal

        datos = AdaptadorReal(
            glpi_cli, zbx_cli,
            criticidad_default=os.environ.get("ITIER_CRITICIDAD_DEFAULT", "alta"),
        )
        datos_desc = "GLPI+Zabbix reales"
    else:
        datos = DatosDemo()
        datos_desc = "DEMO"

    # ── Fuente de eventos ─────────────────────────────────────────────────────
    if zbx_cli is not None:
        if glpi_cli is not None:
            # correlación host→CI real: ci_id = "{itemtype}:{items_id}" de GLPI.
            def _resolver(p, _g=glpi_cli):
                ci = _g.ci_por_host(p.get("host") or "")
                return f"{ci['itemtype']}:{ci['items_id']}" if ci and ci.get("items_id") else (p.get("host") or "")
        else:
            def _resolver(p, _d=datos):
                f = getattr(_d, "ci_por_host", None)
                return (f(p.get("host") or "") if f else None) or (p.get("host") or "")
        fuente = FuenteZabbix(zbx_cli, resolver_ci=_resolver)
        fuente_desc = f"Zabbix real ({zbx_url})"
    else:
        fuente = FuenteDemo()
        fuente_desc = "DEMO (definí ITIER_ZBX_URL + ITIER_ZBX_TOKEN para el Zabbix real)"

    # ── Puerto de tickets: GLPI real (mismo cliente) o tickets en memoria ─────
    if glpi_cli is not None:
        tickets = glpi_cli
        tickets_desc = f"GLPI real ({glpi_url})"
    else:
        from .glpi import TicketsDemo

        tickets = TicketsDemo()
        tickets_desc = "DEMO (definí ITIER_GLPI_URL + APP/USER_TOKEN para GLPI real)"

    agente = None
    tiene_llm = any(os.environ.get(k) for k in _LLM)
    rutas = _rutas_policy()
    if tiene_llm and rutas:
        try:
            from .agent import AgenteEdge
            from .policy import MotorDePoliticas

            agente = AgenteEdge(
                politicas=MotorDePoliticas.desde_archivos(*rutas),
                auditoria=auditoria,
                datos=datos,
            )
            modo = "bucle_llm"
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo construir el agente LLM (%s); sigo en OFFLINE.", e)
            modo = "bucle_offline"
    else:
        modo = "bucle_offline"
        if not tiene_llm:
            log.info("Bucle en OFFLINE: sin ITIER_LLM_GATEWAY/ANTHROPIC_API_KEY (solo triage).")
        elif not rutas:
            log.info("Bucle en OFFLINE: no encontré el catálogo de políticas (solo triage).")

    runner = Runner(auditoria=auditoria, datos=datos, agente=agente, tickets=tickets)
    intervalo = float(os.environ.get("ITIER_AGENT_INTERVALO_S", "60"))

    log.warning(
        "Bucle de eventos ACTIVO (modo=%s, fuente=%s, tickets=%s, datos=%s). Ver reference/README.",
        modo, fuente_desc, tickets_desc, datos_desc,
    )
    threading.Thread(
        target=runner.bucle, args=(fuente, intervalo, parar),
        daemon=True, name="itier-bucle",
    ).start()
    return modo


class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo: int, cuerpo: dict) -> None:
        payload = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802  (nombre impuesto por BaseHTTPRequestHandler)
        if self.path.rstrip("/") == "/healthz":
            self._responder(200, {"status": "ok"})
        elif self.path.rstrip("/") == "/readyz":
            faltan = faltantes()
            if faltan:
                self._responder(503, {"status": "config_incompleta", "faltan": faltan})
            else:
                self._responder(
                    200,
                    {
                        "status": "listo",
                        "tenant": os.environ.get("ITIER_TENANT"),
                        "modo": _MODO,
                    },
                )
        else:
            self._responder(404, {"status": "not_found"})

    def log_message(self, *_args) -> None:
        # Silencia el log por request del http.server; usamos logging propio.
        return


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("ITIER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tenant = os.environ.get("ITIER_TENANT", "(sin tenant)")
    faltan = faltantes()
    if faltan:
        log.warning("Configuración incompleta, faltan: %s", ", ".join(faltan))
        log.warning("El contenedor arranca igual; /readyz devolverá 503 hasta completarla.")
    else:
        log.info("Configuración mínima presente para el tenant %s", tenant)

    server = ThreadingHTTPServer(("0.0.0.0", PUERTO), Handler)

    parar = threading.Event()

    global _MODO
    _MODO = _arrancar_bucle(parar)
    if _MODO == "esqueleto":
        log.info(
            "Agente iTiers en modo esqueleto. Health en :%d/healthz y :%d/readyz. "
            "No se procesan eventos (activá el bucle con ITIER_AGENT_LOOP=1).",
            PUERTO, PUERTO,
        )
    else:
        log.info("Agente iTiers con bucle de eventos (modo=%s). Health en :%d.", _MODO, PUERTO)

    def _apagar(*_a) -> None:
        log.info("Señal de apagado recibida, cerrando.")
        parar.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _apagar)
    signal.signal(signal.SIGINT, _apagar)

    try:
        server.serve_forever(poll_interval=1.0)
    finally:
        server.server_close()
        log.info("Servicio detenido.")


if __name__ == "__main__":
    main()
