"""Tests de la Fase 3: motor de políticas + bucle de eventos (offline, sin LLM).

No requiere clave de LLM ni el SDK en uso: ejercita el código determinista, que
es el que decide qué puede hacer el agente y cómo se ingieren los eventos.

    pip install -e reference/edge-agent
    python reference/edge-agent/tests/test_fase3.py

Sale con código != 0 si algún check falla (apto para CI).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from itier_agent.audit import Auditoria
from itier_agent.events import Evento, FuenteDemo, FuenteZabbix
from itier_agent.fixtures import _CIS, DatosDemo
from itier_agent.policy import MotorDePoliticas
from itier_agent.runner import Runner, formatear_disparador

POLICY = Path(__file__).resolve().parents[2] / "policy"
CATALOGO = POLICY / "acciones.yaml"
TENANT = POLICY / "tenant-ejemplo.yaml"

fails: list[str] = []


def check(nombre: str, cond: bool, extra: str = "") -> None:
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


# Momentos de referencia.
SAB_MADRUGADA = dt.datetime(2026, 8, 15, 3, 0)   # sábado, ventana de mantenimiento
MAR_MEDIODIA = dt.datetime(2026, 8, 11, 12, 0)   # martes, horario laboral

# ── 1) Motor de políticas: la misma acción cambia según contexto ────────────
pol = MotorDePoliticas.desde_archivos(str(CATALOGO), str(TENANT))


def ev_pb(playbook, ci_id, cuando):
    return pol.evaluar("remediate.run_playbook", ci=_CIS[ci_id], playbook=playbook, ahora=cuando)


# baja criticidad + ventana, pero el plan 'gestionado' topea en L3
d = ev_pb("rotar_logs", "ci_5502", SAB_MADRUGADA)
check("L4 playbook en CI baja + ventana → L3 por techo del tenant",
      d.resultado == "ejecutar_y_notificar" and d.nivel_efectivo.name == "L3", d.resultado)

# mismo playbook+CI: ventana vs horario laboral
d = ev_pb("limpiar_temp_windows", "ci_4471", SAB_MADRUGADA)
check("limpiar_temp en CI alta, ventana → L3", d.resultado == "ejecutar_y_notificar", d.resultado)
d = ev_pb("limpiar_temp_windows", "ci_4471", MAR_MEDIODIA)
check("MISMO caso en horario laboral → L2 (requiere aprobación)",
      d.resultado == "requiere_aprobacion" and d.nivel_efectivo.name == "L2", d.resultado)

# playbook irreversible nace en L2 y ahí se queda
d = ev_pb("extender_volumen", "ci_4471", SAB_MADRUGADA)
check("playbook irreversible → L2 (techo duro destruccion_de_datos)",
      d.resultado == "requiere_aprobacion" and "destruccion_de_datos" in d.techos_duros, str(d.techos_duros))

# CI crítico + rol domain_controller: criticidad + techo duro lo bajan
d = ev_pb("rotar_logs", "ci_0003", SAB_MADRUGADA)
check("L4 playbook sobre domain controller → L2",
      d.nivel_efectivo.name == "L2" and "infraestructura_critica" in d.techos_duros, str(d.como_dict()))

# CI intocable: L0 permanente, sin importar nada
d = ev_pb("limpiar_temp_windows", "ci_0119", SAB_MADRUGADA)
check("CI intocable → denegar", d.resultado == "denegar" and d.nivel_efectivo.name == "L0", d.resultado)

# excepción del tenant: reiniciar_servidor forzado a L1 aunque sea ventana
d = ev_pb("reiniciar_servidor", "ci_4471", SAB_MADRUGADA)
check("excepción del tenant (reiniciar_servidor) → L1 (recomendar)",
      d.resultado == "recomendar" and d.nivel_efectivo.name == "L1", d.resultado)

# acción inexistente en el catálogo firmado
d = pol.evaluar("foo.bar", ci=_CIS["ci_4471"], ahora=SAB_MADRUGADA)
check("acción fuera del catálogo → denegar", d.resultado == "denegar", d.resultado)

# dry_run (sin efectos): permitido salvo CI intocable — así lo usa tools.py
d = pol.evaluar("remediate.dry_run", ci=_CIS["ci_4471"], playbook="extender_volumen", ahora=SAB_MADRUGADA)
check("dry_run sobre CI normal → permitido (no denegar)", d.resultado != "denegar", d.resultado)
d = pol.evaluar("remediate.dry_run", ci=_CIS["ci_0119"], playbook="limpiar_temp_windows", ahora=SAB_MADRUGADA)
check("dry_run sobre CI intocable → denegar", d.resultado == "denegar", d.resultado)

# ── 2) Bucle de eventos (runner) en modo OFFLINE ────────────────────────────
aud = Auditoria(tenant="acme-sa")
runner = Runner(auditoria=aud, datos=DatosDemo(), agente=None)
salidas = runner.correr(FuenteDemo())

check("3 eventos de demo procesados", len(salidas) == 3, str(len(salidas)))
check("1º evento (disco) → triage_offline", salidas[0]["accion"] == "triage_offline", salidas[0]["accion"])
check("2º evento (idéntico) → omitido por dedup", salidas[1]["accion"] == "omitido", salidas[1]["accion"])
check("3º evento (DC) → triage_offline", salidas[2]["accion"] == "triage_offline", salidas[2]["accion"])
ok_aud, motivo = aud.verificar()
check("auditoría del bucle íntegra", ok_aud, motivo)
tipos = [e["tipo"] for e in aud]
check("auditoría registró el duplicado", "evento_duplicado" in tipos, str(tipos))
check("auditoría registró el triage", tipos.count("triage_offline") == 2, str(tipos))

# formateo del disparador
disp = formatear_disparador(
    Evento("zabbix", "evt_1", "ci_4471", "alta", "disco D: al 92%", ticket_id="INC-9")
)
check("disparador incluye CI, síntoma y ticket",
      "ci_4471" in disp and "disco D: al 92%" in disp and "INC-9" in disp)

# dedup explícito sobre el mismo objeto
r2 = Runner(auditoria=Auditoria(tenant="t"), datos=DatosDemo(), agente=None)
e = Evento("zabbix", "evt_x", "ci_4471", "alta", "x")
T = dt.datetime(2026, 8, 10, 10, 0, tzinfo=dt.timezone.utc)
check("primer evento se tría", r2.procesar(e, ahora=T)["accion"] == "triage_offline")
check("mismo evento 1 min después → omitido",
      r2.procesar(e, ahora=T + dt.timedelta(minutes=1))["accion"] == "omitido")

# guard de tormenta por CI (máximo 6/hora)
r3 = Runner(auditoria=Auditoria(tenant="t"), datos=DatosDemo(), agente=None)
res = [r3.procesar(Evento("zabbix", f"e{i}", "ci_4471", "media", "m"), ahora=T) for i in range(7)]
check("6 eventos del mismo CI pasan", all(x["accion"] == "triage_offline" for x in res[:6]))
check("el 7º evento del mismo CI → suprimido (tormenta)", res[6]["accion"] == "suprimido", res[6]["accion"])

# ── 3) Despacho al agente (online), con un agente falso ─────────────────────
class AgenteFalso:
    def __init__(self):
        self.visto = []

    def atender(self, disparador):
        self.visto.append(disparador)
        return {"texto": "ok", "turnos": 2, "aprobaciones_pendientes": 1, "costo_usd": 0.01}


af = AgenteFalso()
r4 = Runner(auditoria=Auditoria(tenant="t"), datos=DatosDemo(), agente=af)
out = r4.procesar(Evento("zabbix", "evt_on", "ci_4471", "alta", "disco", ticket_id="INC-1"), ahora=T)
check("con agente: el evento se atiende", out["accion"] == "atendido", out["accion"])
check("con agente: se pasa el disparador al bucle", len(af.visto) == 1 and "ci_4471" in af.visto[0])
check("con agente: se propagan turnos/aprobaciones", out["turnos"] == 2 and out["aprobaciones_pendientes"] == 1)
check("runner.offline refleja el modo", r4.offline is False and r2.offline is True)

# ── 4) FuenteZabbix: mapeo de problem.get → Evento ──────────────────────────
class ZbxFalso:
    def problemas(self):
        return [
            {"eventid": "7001", "severity": "5", "name": "SRV-DC01 no responde", "host": "SRV-DC01"},
            {"eventid": "7002", "severity": "3", "name": "latencia alta", "host": "SW-CORE01"},
        ]


fz = FuenteZabbix(ZbxFalso(), resolver_ci=lambda p: {"SRV-DC01": "ci_0001"}.get(p["host"]))
evs = fz.nuevos()
check("FuenteZabbix mapea 2 problemas", len(evs) == 2, str(len(evs)))
check("severity 5 → 'critica'", evs[0].severidad == "critica", evs[0].severidad)
check("severity 3 → 'media'", evs[1].severidad == "media", evs[1].severidad)
check("resolver_ci traduce el host al CI", evs[0].ci_id == "ci_0001", evs[0].ci_id)
check("name → disparador, eventid → id", evs[0].disparador == "SRV-DC01 no responde" and evs[0].id == "7001")
check("clave de correlación usa origen:id", evs[0].clave_correlacion() == "zabbix:7001", evs[0].clave_correlacion())

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
