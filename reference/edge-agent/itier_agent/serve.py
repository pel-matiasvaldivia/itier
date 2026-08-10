"""Punto de entrada de servicio del agente iTier.

Valida la configuración, expone un health check y queda a la espera. El bucle de
procesamiento de eventos (itier_agent.agent) llega en la Fase 3 del roadmap;
esta imagen existe para poder desplegar el contenedor en el cliente, cablear la
red contra el VPS y validar la conectividad end-to-end desde ya.

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
                        "modo": "esqueleto_fase3",
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

    log.info(
        "Agente iTier en modo esqueleto (Fase 3 pendiente). "
        "Health en :%d/healthz y :%d/readyz. Aún no se procesan eventos.",
        PUERTO,
        PUERTO,
    )

    server = ThreadingHTTPServer(("0.0.0.0", PUERTO), Handler)

    parar = threading.Event()

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
