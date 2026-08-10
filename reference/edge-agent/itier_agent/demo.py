"""Demo del bucle de iTier.

    python -m itier_agent.demo

Sin ANTHROPIC_API_KEY corre solo la parte determinista (motor de políticas y
auditoría), que es la que realmente hay que entender.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from .audit import Auditoria
from .fixtures import DatosDemo, _CIS
from .policy import MotorDePoliticas

RAIZ = Path(__file__).resolve().parents[3]
CATALOGO = RAIZ / "reference" / "policy" / "acciones.yaml"
TENANT = RAIZ / "reference" / "policy" / "tenant-ejemplo.yaml"

EVENTO = """\
Evento recibido de Zabbix a las 14:32.

  origen:     zabbix
  id:         evt_88213
  ci:         ci_4471 (SRV-FILE01)
  severidad:  alta
  disparador: uso de disco en volumen D: superó el 90% (actual: 92%)

Diagnosticá el incidente y proponé cómo resolverlo. El ticket abierto es INC-3402.
"""


def _sep(t: str) -> None:
    print(f"\n{'─' * 72}\n  {t}\n{'─' * 72}")


def demo_politicas() -> None:
    """La pieza central: la misma acción, distinto resultado según el contexto."""
    _sep("MOTOR DE POLÍTICAS — la misma acción cambia según CI, hora y cliente")

    pol = MotorDePoliticas.desde_archivos(str(CATALOGO), str(TENANT))

    sabado_madrugada = dt.datetime(2026, 8, 15, 3, 0)   # ventana de mantenimiento
    martes_mediodia = dt.datetime(2026, 8, 11, 12, 0)   # horario laboral

    casos = [
        ("rotar_logs", "ci_5502", sabado_madrugada,
         "playbook L4 (401 ejecuciones, 0 reversiones), CI de baja criticidad,\n"
         "    ventana de mantenimiento. Aun así NO llega a L4: el cliente está en\n"
         "    plan 'gestionado', cuyo techo global es L3"),
        ("limpiar_temp_windows", "ci_4471", sabado_madrugada,
         "playbook probado, CI de criticidad alta, ventana de mantenimiento"),
        ("limpiar_temp_windows", "ci_4471", martes_mediodia,
         "MISMO playbook y MISMO CI, pero en horario laboral"),
        ("rotar_logs", "ci_0003", sabado_madrugada,
         "playbook L4, pero sobre un controlador de dominio:\n"
         "    la criticidad y el techo duro de infraestructura crítica lo bajan"),
        ("extender_volumen", "ci_4471", sabado_madrugada,
         "playbook IRREVERSIBLE: nace en L2 y el techo duro se lo confirma"),
        ("limpiar_temp_windows", "ci_0119", sabado_madrugada,
         "NAS-BACKUP está en la lista de CIs intocables del cliente"),
    ]

    for playbook, ci_id, cuando, nota in casos:
        d = pol.evaluar(
            "remediate.run_playbook",
            ci=_CIS[ci_id],
            playbook=playbook,
            ahora=cuando,
        )
        print(
            f"\n  {playbook} → {_CIS[ci_id].nombre}  "
            f"({cuando.strftime('%a %d/%m %H:%M')})"
        )
        print(f"    {nota}")
        print(
            f"    base={d.nivel_base.name}  tenant={d.techo_tenant.name}  "
            f"criticidad={d.techo_criticidad.name}  ventana={d.techo_ventana.name}"
        )
        if d.techos_duros:
            print(f"    techos duros aplicados: {', '.join(d.techos_duros)}")
        print(f"    → efectivo={d.nivel_efectivo.name}   RESULTADO: {d.resultado.upper()}")
        if d.motivo != "sin restricciones adicionales":
            print(f"    motivo: {d.motivo}")


def demo_auditoria() -> None:
    _sep("AUDITORÍA — cadena de hash y detección de manipulación")

    aud = Auditoria(tenant="acme-sa")
    aud.registrar("evidencia", {"nota": "consulta de métricas"})
    aud.registrar("politica", {"nota": "evaluación de nivel"})
    aud.registrar("ejecucion", {"nota": "playbook ejecutado"})

    ok, motivo = aud.verificar()
    print(f"\n  Verificación inicial: {'✅' if ok else '❌'} {motivo}")

    # Alguien edita una entrada del pasado.
    aud._entradas[1]["nota"] = "evaluación de nivel (alterada)"
    ok, motivo = aud.verificar()
    print(f"  Tras alterar la entrada 1: {'✅' if ok else '❌'} {motivo}")


def demo_agente() -> None:
    _sep("BUCLE DEL AGENTE — diagnóstico sobre un evento real")

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ITIER_LLM_GATEWAY")):
        print(
            "\n  ⏭  Omitido: falta ANTHROPIC_API_KEY (o ITIER_LLM_GATEWAY).\n"
            "     En producción el appliance NUNCA tiene la clave: apunta al\n"
            "     gateway del plano de control, que la custodia y aplica el tope\n"
            "     de gasto por tenant."
        )
        return

    from .agent import AgenteEdge  # import diferido: no requerir el SDK sin clave

    agente = AgenteEdge(
        politicas=MotorDePoliticas.desde_archivos(str(CATALOGO), str(TENANT)),
        auditoria=Auditoria(tenant="acme-sa"),
        datos=DatosDemo(),
    )

    r = agente.atender(EVENTO)

    print(f"\n{r['texto']}\n")
    print(f"  turnos: {r['turnos']}   pendientes de aprobación: {r['aprobaciones_pendientes']}")
    print(
        f"  tokens: in={r['uso']['input']:,}  out={r['uso']['output']:,}  "
        f"cache_read={r['uso']['cache_read']:,}  cache_write={r['uso']['cache_write']:,}"
    )
    print(f"  costo de esta interacción: USD {r['costo_usd']}")

    _sep("AUDITORÍA DE LA INTERACCIÓN")
    for e in agente.auditoria:
        print(f"  {e['ts']}  {e['tipo']:<22} {e['hash'][:12]}…")
    ok, motivo = agente.auditoria.verificar()
    print(f"\n  {'✅' if ok else '❌'} {motivo}")


def main() -> None:
    print("\n╔══════════════════════════════════════════════════════════════════════╗")
    print("║  iTier — demo del esqueleto de referencia                            ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    demo_politicas()
    demo_auditoria()
    demo_agente()
    print()


if __name__ == "__main__":
    main()
