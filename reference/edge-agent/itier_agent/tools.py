"""Superficie de herramientas del agente.

Esta es la ÚNICA vía por la que el agente afecta al mundo. Se puede leer entera
en un minuto — esa es la propiedad de diseño (ver ADR-0003).

No existe `run_shell`. No existe `execute_sql`. No existe cliente HTTP genérico.
Agregar una capacidad es agregar una tool nueva, revisada y clasificada; nunca
ampliar el alcance de una existente.

En producción cada bloque de tools vive en un servidor MCP separado con su propio
límite de privilegio. Acá son funciones locales para que la demo sea legible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from anthropic import beta_tool

from .audit import Auditoria
from .policy import CI, Cortacircuitos, MotorDePoliticas

# ---------------------------------------------------------------------------
# Contexto de ejecución
#
# Las tools necesitan acceso al motor de políticas, a la auditoría y a los
# adaptadores de datos. Se inyecta al arrancar el agente, nunca desde el modelo.
# ---------------------------------------------------------------------------


@dataclass
class Contexto:
    politicas: MotorDePoliticas
    auditoria: Auditoria
    cortacircuitos: Cortacircuitos
    datos: Any  # adaptador GLPI/Zabbix; en la demo, fixtures en memoria
    cola_aprobaciones: list[dict]


_ctx: Contexto | None = None


def instalar_contexto(ctx: Contexto) -> None:
    global _ctx
    _ctx = ctx


def _c() -> Contexto:
    if _ctx is None:
        raise RuntimeError("Contexto no instalado. Llamar a instalar_contexto() primero.")
    return _ctx


def _evidencia(tool: str, args: dict, resultado: Any) -> Any:
    """Registra la consulta en auditoría y devuelve el resultado al modelo."""
    c = _c()
    ok, motivo = c.cortacircuitos.registrar_llamada(
        json.dumps({"t": tool, "a": args}, sort_keys=True)
    )
    if not ok:
        return {"error": motivo}
    c.auditoria.registrar(
        "evidencia", {"evidencia": c.auditoria.digest_evidencia(tool, args, resultado)}
    )
    return resultado


# ===========================================================================
# CLASE read — sin efectos secundarios. Siempre permitidas.
# ===========================================================================


@beta_tool
def cmdb_get_ci(ci_id: str) -> str:
    """Devuelve un elemento de configuración con sus atributos y relaciones.

    Args:
        ci_id: Identificador del CI, por ejemplo "ci_4471".
    """
    r = _c().datos.get_ci(ci_id)
    return json.dumps(_evidencia("cmdb.get_ci", {"ci_id": ci_id}, r), ensure_ascii=False)


@beta_tool
def cmdb_search_cis(
    tipo: str | None = None, criticidad: str | None = None, texto: str | None = None
) -> str:
    """Busca elementos de configuración por tipo, criticidad o texto libre.

    Args:
        tipo: Tipo de CI, por ejemplo "servidor", "switch", "impresora".
        criticidad: Una de "critica", "alta", "media", "baja".
        texto: Texto a buscar en el nombre o la descripción.
    """
    args = {"tipo": tipo, "criticidad": criticidad, "texto": texto}
    r = _c().datos.search_cis(**args)
    return json.dumps(_evidencia("cmdb.search_cis", args, r), ensure_ascii=False)


@beta_tool
def cmdb_get_impact_graph(ci_id: str) -> str:
    """Devuelve el grafo de dependencias de un CI: qué depende de él y de qué depende.

    Usar para entender el impacto real antes de proponer cualquier acción.

    Args:
        ci_id: Identificador del CI.
    """
    r = _c().datos.impact_graph(ci_id)
    return json.dumps(
        _evidencia("cmdb.get_impact_graph", {"ci_id": ci_id}, r), ensure_ascii=False
    )


@beta_tool
def tickets_get_similar(ci_id: str, sintoma: str, limite: int = 5) -> str:
    """Busca incidentes históricos parecidos y cómo se resolvieron.

    Es la herramienta de mayor valor diagnóstico: si el problema ya pasó, la
    resolución anterior es la mejor evidencia disponible.

    Args:
        ci_id: CI afectado.
        sintoma: Descripción breve del síntoma observado.
        limite: Cantidad máxima de incidentes a devolver.
    """
    args = {"ci_id": ci_id, "sintoma": sintoma, "limite": limite}
    r = _c().datos.tickets_similares(**args)
    return json.dumps(_evidencia("tickets.get_similar", args, r), ensure_ascii=False)


@beta_tool
def metrics_query(ci_id: str, metrica: str, ventana_horas: int = 24) -> str:
    """Consulta una serie temporal de una métrica para un CI.

    Args:
        ci_id: Identificador del CI.
        metrica: Nombre de la métrica, por ejemplo "disco.uso_pct", "cpu.uso_pct".
        ventana_horas: Ventana hacia atrás, en horas.
    """
    args = {"ci_id": ci_id, "metrica": metrica, "ventana_horas": ventana_horas}
    r = _c().datos.metrics(**args)
    return json.dumps(_evidencia("metrics.query", args, r), ensure_ascii=False)


@beta_tool
def logs_tail(ci_id: str, log: str, lineas: int = 100) -> str:
    """Devuelve las últimas líneas de un log declarado para el CI.

    Solo se pueden consultar logs de la allowlist declarada por CI. Cualquier
    otra ruta se rechaza.

    Args:
        ci_id: Identificador del CI.
        log: Nombre lógico del log, por ejemplo "sistema", "aplicacion", "backup".
        lineas: Cantidad de líneas a devolver.
    """
    args = {"ci_id": ci_id, "log": log, "lineas": lineas}
    r = _c().datos.logs(**args)
    return json.dumps(_evidencia("logs.tail", args, r), ensure_ascii=False)


@beta_tool
def assets_get_lifecycle(ci_id: str) -> str:
    """Devuelve garantía, contrato, fin de soporte y licencias del activo.

    Args:
        ci_id: Identificador del CI.
    """
    r = _c().datos.lifecycle(ci_id)
    return json.dumps(
        _evidencia("assets.get_lifecycle", {"ci_id": ci_id}, r), ensure_ascii=False
    )


@beta_tool
def kb_search(consulta: str, limite: int = 3) -> str:
    """Busca en la base de conocimiento y en los errores conocidos.

    Args:
        consulta: Términos de búsqueda.
        limite: Cantidad máxima de artículos a devolver.
    """
    args = {"consulta": consulta, "limite": limite}
    r = _c().datos.kb(**args)
    return json.dumps(_evidencia("kb.search", args, r), ensure_ascii=False)


# ===========================================================================
# CLASE act — toca la infraestructura. SIEMPRE pasa por el motor de políticas.
# ===========================================================================


@beta_tool
def remediate_dry_run(playbook: str, ci_id: str, parametros: str = "{}") -> str:
    """Simula un playbook sin aplicar cambios (Ansible --check).

    Siempre permitida: no tiene efectos por definición. Usarla para convertir
    una propuesta en evidencia antes de pedir aprobación.

    Args:
        playbook: Identificador del playbook del catálogo firmado.
        ci_id: CI sobre el que se simularía.
        parametros: Parámetros del playbook, en JSON.
    """
    c = _c()
    ci = c.datos.get_ci_obj(ci_id)
    d = c.politicas.evaluar("remediate.dry_run", ci=ci, playbook=playbook)
    c.auditoria.registrar("politica", {"decision": d.como_dict()})

    if d.resultado == "denegar":
        return json.dumps({"permitido": False, "motivo": d.motivo}, ensure_ascii=False)

    r = c.datos.dry_run(playbook, ci_id, json.loads(parametros))
    return json.dumps({"permitido": True, "simulacion": r}, ensure_ascii=False)


@beta_tool
def remediate_run_playbook(playbook: str, ci_id: str, parametros: str = "{}") -> str:
    """Ejecuta un playbook del catálogo firmado sobre un CI.

    El agente NO decide si se ejecuta: propone, y el motor de políticas resuelve.
    Según el nivel de autonomía efectivo, esta llamada puede ejecutar, encolar
    para aprobación humana, quedar solo como recomendación, o ser denegada.
    Cualquiera de esos resultados es una respuesta válida — no un error.

    Args:
        playbook: Identificador del playbook del catálogo firmado.
        ci_id: CI sobre el que actuar.
        parametros: Parámetros del playbook, en JSON.
    """
    c = _c()
    ci = c.datos.get_ci_obj(ci_id)
    d = c.politicas.evaluar("remediate.run_playbook", ci=ci, playbook=playbook)

    ok_cc, motivo_cc = c.cortacircuitos.permite(ci_id)
    if not ok_cc:
        c.auditoria.registrar(
            "cortacircuito", {"accion": playbook, "ci": ci_id, "motivo": motivo_cc}
        )
        return json.dumps({"resultado": "denegado", "motivo": motivo_cc}, ensure_ascii=False)

    entrada = {
        "decision": d.como_dict(),
        "accion_propuesta": {"playbook": playbook, "parametros": json.loads(parametros)},
    }

    if d.resultado in ("ejecutar", "ejecutar_y_notificar"):
        r = c.datos.ejecutar(playbook, ci_id, json.loads(parametros))
        c.cortacircuitos.registrar(ci_id)
        entrada["ejecucion"] = r
        c.auditoria.registrar("ejecucion", entrada)
        return json.dumps(
            {
                "resultado": d.resultado,
                "nivel_efectivo": d.nivel_efectivo.name,
                "ejecucion": r,
                "notificado": d.resultado == "ejecutar_y_notificar",
            },
            ensure_ascii=False,
        )

    if d.resultado == "requiere_aprobacion":
        item = {
            "playbook": playbook,
            "ci": ci_id,
            "parametros": json.loads(parametros),
            "decision": d.como_dict(),
        }
        c.cola_aprobaciones.append(item)
        c.auditoria.registrar("encolado_aprobacion", entrada)
        return json.dumps(
            {
                "resultado": "requiere_aprobacion",
                "nivel_efectivo": d.nivel_efectivo.name,
                "motivo": d.motivo,
                "nota": (
                    "La acción quedó encolada para aprobación humana. Continuá el "
                    "análisis y dejá el ticket con el diagnóstico y el plan; no "
                    "reintentes esta acción."
                ),
            },
            ensure_ascii=False,
        )

    c.auditoria.registrar("politica", entrada)
    return json.dumps(
        {
            "resultado": d.resultado,
            "nivel_efectivo": d.nivel_efectivo.name,
            "motivo": d.motivo,
            "nota": (
                "No está permitido actuar. Dejá la recomendación documentada en el "
                "ticket para que la resuelva un técnico."
            ),
        },
        ensure_ascii=False,
    )


# ===========================================================================
# CLASE write_itsm — escribe en el ITSM, nunca toca infraestructura.
# ===========================================================================


@beta_tool
def tickets_update(ticket_id: str, diagnostico: str, plan: str, categoria: str) -> str:
    """Actualiza un ticket con el diagnóstico y el plan propuesto.

    Args:
        ticket_id: Identificador del ticket.
        diagnostico: Diagnóstico en lenguaje claro, con la evidencia que lo sustenta.
        plan: Plan de remediación propuesto, paso a paso.
        categoria: Categoría del incidente.
    """
    c = _c()
    args = {
        "ticket_id": ticket_id,
        "diagnostico": diagnostico,
        "plan": plan,
        "categoria": categoria,
    }
    r = c.datos.actualizar_ticket(**args)
    c.auditoria.registrar("write_itsm", {"tool": "tickets.update", "args": args})
    return json.dumps(r, ensure_ascii=False)


# ===========================================================================
# La superficie completa. Si no está acá, el agente no puede hacerlo.
# ===========================================================================

TOOLS = [
    # read
    cmdb_get_ci,
    cmdb_search_cis,
    cmdb_get_impact_graph,
    tickets_get_similar,
    metrics_query,
    logs_tail,
    assets_get_lifecycle,
    kb_search,
    # act
    remediate_dry_run,
    remediate_run_playbook,
    # write_itsm
    tickets_update,
]
