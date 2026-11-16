"""Adaptador de datos REAL del agente: implementa el contrato que consumen las
tools (itier_agent.tools) y el motor de políticas, contra GLPI y Zabbix, en vez
de los fixtures de `DatosDemo`.

Honestidad de alcance (todo marcado ⚠, aislado acá):
  - REAL hoy: get_ci / get_ci_obj (criticidad + roles), actualizar_ticket,
    metrics (Zabbix), lifecycle (Infocom de GLPI).
  - VACÍO HONESTO (devuelve vacío + nota, NO fixtures de demo): search_cis,
    impact_graph, tickets_similares, kb, logs — requieren /search de GLPI o
    fuentes que todavía no se cablean. Mejor vacío que inventado.
  - NO DISPONIBLE: dry_run / ejecutar — el ejecutor de Ansible llega en Fase 4.

El id de CI sigue la convención "{itemtype}:{items_id}" de GLPI (p. ej.
"Computer:42"), que es lo que produce el resolver de FuenteZabbix con GLPI real.

⚠ CRITICIDAD/ROLES alimentan el techo de autonomía (seguridad). Acá se derivan de
  un campo de GLPI (business criticity) y de heurísticas por nombre, con un
  **default conservador**. En producción deberían venir de la curación de la CMDB
  (ver docs/07 §6.2); confirmá la fuente antes de subir la autonomía.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from .policy import CI


def _sin_acentos(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()

log = logging.getLogger("itier.datos")

_NO_EJECUTOR = {"estado": "no_disponible",
                "detalle": "El ejecutor de Ansible llega en Fase 4; esta acción no se ejecuta."}

# nombre de la business criticity de GLPI (minúsculas) → criticidad de iTier.
_CRIT = [("crit", "critica"), ("alta", "alta"), ("high", "alta"),
         ("media", "media"), ("medium", "media"), ("baja", "baja"), ("low", "baja")]

# heurísticas de rol por nombre de CI (subcadena en mayúsculas → rol).
_ROLES = [("DC", "domain_controller"), ("DOMAIN", "domain_controller"),
          ("FW", "firewall_perimetral"), ("FIREWALL", "firewall_perimetral"),
          ("BACKUP", "sistema_backup"), ("BKP", "sistema_backup"), ("NAS", "sistema_backup"),
          ("FILE", "file_server"), ("-FS", "file_server")]


class AdaptadorReal:
    def __init__(self, glpi: Any, zbx: Any, *, criticidad_default: str = "alta") -> None:
        self.glpi = glpi
        self.zbx = zbx
        self.criticidad_default = criticidad_default

    # -- utilidades ----------------------------------------------------------

    @staticmethod
    def _parse(ci_id: str) -> tuple[str, str]:
        if ci_id and ":" in ci_id:
            it, _, iid = ci_id.partition(":")
            return it or "Computer", iid
        return "Computer", ci_id or ""

    def _criticidad(self, item: dict) -> str:
        val = _sin_acentos(str(item.get("businesscriticities_id") or ""))
        for aguja, crit in _CRIT:
            if aguja in val:
                return crit
        return self.criticidad_default

    @staticmethod
    def _roles(nombre: str) -> tuple[str, ...]:
        up = (nombre or "").upper()
        roles = [rol for aguja, rol in _ROLES if aguja in up]
        return tuple(dict.fromkeys(roles))

    # -- CMDB ----------------------------------------------------------------

    def get_ci_obj(self, ci_id: str) -> CI | None:
        """CI mínimo para el motor de políticas (id, nombre, criticidad, roles)."""
        itemtype, iid = self._parse(ci_id)
        item = self.glpi.obtener_item(itemtype, iid)
        if not item:
            return None
        nombre = item.get("name") or ci_id
        return CI(id=ci_id, nombre=nombre, criticidad=self._criticidad(item), roles=self._roles(nombre))

    def get_ci(self, ci_id: str) -> dict:
        itemtype, iid = self._parse(ci_id)
        item = self.glpi.obtener_item(itemtype, iid)
        if not item:
            return {"error": f"CI {ci_id} no encontrado en GLPI"}
        nombre = item.get("name") or ci_id
        return {
            "id": ci_id, "itemtype": itemtype, "nombre": nombre,
            "criticidad": self._criticidad(item), "roles": list(self._roles(nombre)),
            "serial": item.get("serial"), "modelo": item.get("computermodels_id"),
            "ubicacion": item.get("locations_id"), "estado": item.get("states_id"),
            "comentario": item.get("comment"),
        }

    def search_cis(self, tipo=None, criticidad=None, texto=None) -> list[dict]:
        # ⚠ Requiere /search de GLPI (parseo por ids de campo). Pendiente: vacío honesto.
        log.info("search_cis real no implementado (requiere /search); devuelvo vacío.")
        return []

    def impact_graph(self, ci_id: str) -> dict:
        # ⚠ El grafo de impacto usa relaciones de GLPI; pendiente. Vacío honesto.
        return {"ci": ci_id, "depende_de": [], "dependen_de_el": [],
                "nota": "correlación de impacto pendiente (no cableada contra GLPI todavía)"}

    # -- Tickets -------------------------------------------------------------

    def tickets_similares(self, ci_id: str, sintoma: str, limite: int = 5) -> list[dict]:
        # ⚠ Requiere /search/Ticket. Pendiente: vacío honesto (no fixtures).
        return []

    def actualizar_ticket(self, ticket_id: str, diagnostico: str, plan: str, categoria: str) -> dict:
        texto = f"Diagnóstico (iTier):\n{diagnostico}\n\nPlan propuesto:\n{plan}"
        if categoria:
            texto += f"\n\nCategoría sugerida: {categoria}"
        try:
            self.glpi.agregar_seguimiento(str(ticket_id), texto)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "ticket": ticket_id, "error": str(e)[:200]}
        return {"ok": True, "ticket": ticket_id, "estado": "actualizado"}

    # -- Métricas y logs -----------------------------------------------------

    def metrics(self, ci_id: str, metrica: str, ventana_horas: int = 24) -> dict:
        ci = self.get_ci(ci_id)
        host = ci.get("nombre")
        if not host or ci.get("error"):
            return {"metrica": metrica, "serie": [], "nota": "sin host para el CI"}
        try:
            return self.zbx.historial(host, metrica, ventana_horas) or \
                {"metrica": metrica, "serie": [], "nota": "sin datos"}
        except Exception as e:  # noqa: BLE001
            return {"metrica": metrica, "serie": [], "nota": f"error consultando Zabbix: {str(e)[:120]}"}

    def logs(self, ci_id: str, log: str, lineas: int = 100) -> dict:
        # ⚠ Los logs vienen del GLPI Agent / endpoint; no cableado. Vacío honesto.
        return {"log": log, "lineas": [], "nota": "colector de logs no cableado todavía"}

    # -- Activos y conocimiento ----------------------------------------------

    def lifecycle(self, ci_id: str) -> dict:
        itemtype, iid = self._parse(ci_id)
        info = self.glpi.infocom(itemtype, iid)
        if not info:
            return {"ci": ci_id, "nota": "sin datos de ciclo de vida (Infocom) en GLPI"}
        return {
            "ci": ci_id,
            "compra": info.get("buy_date"),
            "garantia_hasta": info.get("warranty_date") or info.get("warranty_duration"),
            "contrato_soporte": info.get("suppliers_id"),
            "fin_soporte_fabricante": info.get("end_date"),
        }

    def kb(self, consulta: str, limite: int = 3) -> list[dict]:
        # ⚠ Requiere /search/KnowbaseItem. Pendiente: vacío honesto.
        return []

    # -- Ejecución (Fase 4) --------------------------------------------------

    def dry_run(self, playbook: str, ci_id: str, parametros: dict) -> dict:
        return dict(_NO_EJECUTOR, playbook=playbook)

    def ejecutar(self, playbook: str, ci_id: str, parametros: dict) -> dict:
        return dict(_NO_EJECUTOR, playbook=playbook)
