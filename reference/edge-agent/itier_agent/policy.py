"""Motor de políticas de iTier.

Código determinista que decide qué puede hacer el agente. Corre FUERA del LLM:
aunque el modelo sea secuestrado por una inyección de prompt (OWASP ASI01), no puede
alterar su propio nivel de autonomía porque nunca tiene acceso a este módulo.

Ver docs/adr/ADR-0006-autonomia-graduada.md
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

import yaml


class Nivel(IntEnum):
    """Niveles de autonomía. El orden importa: el nivel efectivo es un mínimo."""

    L0 = 0  # observar          — solo lectura
    L1 = 1  # recomendar        — propone, el humano ejecuta
    L2 = 2  # aprobar y actuar  — ejecuta tras aprobación explícita
    L3 = 3  # actuar y notificar— ejecuta y avisa, con ventana de reversión
    L4 = 4  # autónomo          — ejecuta, visible en auditoría

    @classmethod
    def desde_texto(cls, s: str) -> "Nivel":
        return cls[s.strip().upper()]


Resultado = Literal[
    "ejecutar",
    "ejecutar_y_notificar",
    "requiere_aprobacion",
    "recomendar",
    "denegar",
]

_RESULTADO_POR_NIVEL: dict[Nivel, Resultado] = {
    Nivel.L4: "ejecutar",
    Nivel.L3: "ejecutar_y_notificar",
    Nivel.L2: "requiere_aprobacion",
    Nivel.L1: "recomendar",
    Nivel.L0: "denegar",
}


@dataclass(frozen=True)
class CI:
    """Elemento de configuración, en la forma mínima que la política necesita."""

    id: str
    nombre: str
    criticidad: Literal["critica", "alta", "media", "baja"]
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    """Resultado de una evaluación de política. Es lo que se persiste en auditoría."""

    accion: str
    playbook: str | None
    ci: str | None
    nivel_base: Nivel
    techo_tenant: Nivel
    techo_criticidad: Nivel
    techo_ventana: Nivel
    techos_duros: tuple[str, ...]
    nivel_efectivo: Nivel
    resultado: Resultado
    motivo: str

    def como_dict(self) -> dict:
        return {
            "accion": self.accion,
            "playbook": self.playbook,
            "ci": self.ci,
            "nivel_base": self.nivel_base.name,
            "techo_tenant": self.techo_tenant.name,
            "techo_criticidad": self.techo_criticidad.name,
            "techo_ventana": self.techo_ventana.name,
            "techos_duros": list(self.techos_duros),
            "nivel_efectivo": self.nivel_efectivo.name,
            "resultado": self.resultado,
            "motivo": self.motivo,
        }


class MotorDePoliticas:
    """Evalúa el nivel efectivo de una acción sobre un CI en un momento dado.

    nivel_efectivo = min(nivel_base, techo_tenant, techo_criticidad, techo_ventana)
    ...y después se aplican los techos duros, que ninguna evidencia levanta.
    """

    def __init__(self, catalogo: dict, tenant: dict) -> None:
        self._catalogo = catalogo
        self._tenant = tenant
        self._acciones = {a["id"]: a for a in catalogo.get("acciones", [])}
        self._playbooks = {p["id"]: p for p in catalogo.get("playbooks", [])}
        self._techos_duros = {t["id"]: t for t in catalogo.get("techos_duros", [])}

    # -- construcción --------------------------------------------------------

    @classmethod
    def desde_archivos(cls, catalogo_path: str, tenant_path: str) -> "MotorDePoliticas":
        with open(catalogo_path, encoding="utf-8") as f:
            catalogo = yaml.safe_load(f)
        with open(tenant_path, encoding="utf-8") as f:
            tenant = yaml.safe_load(f)
        return cls(catalogo, tenant)

    # -- evaluación ----------------------------------------------------------

    def evaluar(
        self,
        accion: str,
        *,
        ci: CI | None = None,
        playbook: str | None = None,
        ahora: dt.datetime | None = None,
    ) -> Decision:
        ahora = ahora or dt.datetime.now()
        motivos: list[str] = []

        spec = self._acciones.get(accion)
        if spec is None:
            return self._denegar(
                accion, playbook, ci, "Acción no existe en el catálogo firmado."
            )

        # --- CI intocable: L0 permanente, sin excepción ---------------------
        if ci and ci.id in set(self._tenant.get("cis_intocables", [])):
            return self._denegar(
                accion,
                playbook,
                ci,
                f"{ci.nombre} está en la lista de CIs intocables del cliente.",
            )

        # --- nivel base -----------------------------------------------------
        nivel_base, pb_spec = self._nivel_base(spec, playbook)
        if nivel_base is None:
            return self._denegar(
                accion, playbook, ci, f"Playbook '{playbook}' no está en el catálogo firmado."
            )

        # --- los cuatro techos ----------------------------------------------
        techo_tenant = self._techo_tenant(accion, playbook, motivos)
        techo_criticidad = self._techo_criticidad(ci, motivos)
        techo_ventana = self._techo_ventana(ahora, motivos)

        nivel = min(nivel_base, techo_tenant, techo_criticidad, techo_ventana)

        # --- techos duros: se aplican al final y no se negocian --------------
        duros_aplicados: list[str] = []
        for td_id in self._techos_duros_aplicables(spec, pb_spec, ci):
            td = self._techos_duros[td_id]
            tope = Nivel.desde_texto(td["max_nivel"])
            if tope < nivel:
                nivel = tope
                motivos.append(f"techo duro '{td_id}' → {tope.name}")
            duros_aplicados.append(td_id)

        return Decision(
            accion=accion,
            playbook=playbook,
            ci=ci.id if ci else None,
            nivel_base=nivel_base,
            techo_tenant=techo_tenant,
            techo_criticidad=techo_criticidad,
            techo_ventana=techo_ventana,
            techos_duros=tuple(duros_aplicados),
            nivel_efectivo=nivel,
            resultado=_RESULTADO_POR_NIVEL[nivel],
            motivo="; ".join(motivos) or "sin restricciones adicionales",
        )

    # -- internos ------------------------------------------------------------

    def _nivel_base(
        self, spec: dict, playbook: str | None
    ) -> tuple[Nivel | None, dict | None]:
        base = spec["nivel_base"]
        if base != "por_playbook":
            return Nivel.desde_texto(base), None
        pb = self._playbooks.get(playbook or "")
        if pb is None:
            return None, None
        return Nivel.desde_texto(pb["nivel_base"]), pb

    def _techo_tenant(
        self, accion: str, playbook: str | None, motivos: list[str]
    ) -> Nivel:
        techo = Nivel.desde_texto(self._tenant.get("techo_global", "L4"))
        for exc in self._tenant.get("excepciones", []):
            if exc["accion"] != accion:
                continue
            if "playbook" in exc and exc["playbook"] != playbook:
                continue
            candidato = Nivel.desde_texto(exc["max_nivel"])
            if candidato < techo:
                techo = candidato
                motivos.append(f"excepción del cliente → {techo.name}")
        return techo

    def _techo_criticidad(self, ci: CI | None, motivos: list[str]) -> Nivel:
        if ci is None:
            return Nivel.L4
        tabla = self._catalogo.get("techos_por_criticidad", {})
        techo = Nivel.desde_texto(tabla.get(ci.criticidad, "L4"))
        if techo < Nivel.L4:
            motivos.append(f"criticidad '{ci.criticidad}' de {ci.nombre} → {techo.name}")
        return techo

    def _techo_ventana(self, ahora: dt.datetime, motivos: list[str]) -> Nivel:
        dias = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
        hoy = dias[ahora.weekday()]
        hhmm = ahora.strftime("%H:%M")

        # Las ventanas de mantenimiento del cliente tienen prioridad.
        for v in self._tenant.get("ventanas_mantenimiento", []):
            if hoy in v["dias"] and v["desde"] <= hhmm < v["hasta"]:
                return Nivel.desde_texto(v["max_nivel"])

        default = Nivel.L4
        for v in self._catalogo.get("ventanas", []):
            if v.get("default"):
                default = Nivel.desde_texto(v["max_nivel"])
                continue
            if hoy in v.get("dias", []) and v["desde"] <= hhmm < v["hasta"]:
                techo = Nivel.desde_texto(v["max_nivel"])
                if techo < Nivel.L4:
                    motivos.append(f"ventana '{v['id']}' → {techo.name}")
                return techo
        return default

    def _techos_duros_aplicables(
        self, spec: dict, pb_spec: dict | None, ci: CI | None
    ) -> list[str]:
        ids: list[str] = list(spec.get("techos_duros", []))
        if pb_spec:
            ids += list(pb_spec.get("techos_duros", []))
        if ci:
            for td_id, td in self._techos_duros.items():
                roles = set(td.get("aplica_a_roles_de_ci", []))
                if roles & set(ci.roles):
                    ids.append(td_id)
        # Un playbook irreversible nunca puede actuar sin aprobación.
        if pb_spec and pb_spec.get("reversible") is False:
            ids.append("destruccion_de_datos")
        return list(dict.fromkeys(ids))  # dedup preservando orden

    def _denegar(
        self, accion: str, playbook: str | None, ci: CI | None, motivo: str
    ) -> Decision:
        return Decision(
            accion=accion,
            playbook=playbook,
            ci=ci.id if ci else None,
            nivel_base=Nivel.L0,
            techo_tenant=Nivel.L0,
            techo_criticidad=Nivel.L0,
            techo_ventana=Nivel.L0,
            techos_duros=(),
            nivel_efectivo=Nivel.L0,
            resultado="denegar",
            motivo=motivo,
        )


# ---------------------------------------------------------------------------
# Cortacircuitos — OWASP ASI08 (Cascading Failures)
# ---------------------------------------------------------------------------


@dataclass
class Cortacircuitos:
    """Límites de tasa y de radio de explosión, independientes del modelo."""

    max_por_hora_tenant: int = 20
    max_por_hora_ci: int = 3
    max_repeticiones_identicas: int = 3
    _ejecuciones: list[tuple[dt.datetime, str]] = field(default_factory=list)
    _llamadas: dict[str, int] = field(default_factory=dict)

    def permite(self, ci_id: str, ahora: dt.datetime | None = None) -> tuple[bool, str]:
        ahora = ahora or dt.datetime.now()
        corte = ahora - dt.timedelta(hours=1)
        self._ejecuciones = [(t, c) for t, c in self._ejecuciones if t > corte]

        if len(self._ejecuciones) >= self.max_por_hora_tenant:
            return False, "cortacircuito: límite de acciones por hora del tenant"
        if sum(1 for _, c in self._ejecuciones if c == ci_id) >= self.max_por_hora_ci:
            return False, f"cortacircuito: límite de acciones por hora sobre {ci_id}"
        return True, ""

    def registrar(self, ci_id: str, ahora: dt.datetime | None = None) -> None:
        self._ejecuciones.append((ahora or dt.datetime.now(), ci_id))

    def registrar_llamada(self, firma: str) -> tuple[bool, str]:
        """Detecta bucles improductivos: misma tool, mismos argumentos, N veces."""
        self._llamadas[firma] = self._llamadas.get(firma, 0) + 1
        if self._llamadas[firma] > self.max_repeticiones_identicas:
            return False, "cortacircuito: llamada idéntica repetida, posible bucle"
        return True, ""
