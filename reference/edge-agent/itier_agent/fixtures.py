"""Fuente de datos falsa para que la demo corra sin infraestructura.

En producción esto se reemplaza por adaptadores MCP contra GLPI (API v2 / OAuth2),
Zabbix y el ejecutor de Ansible. La interfaz pública de esta clase es el contrato
que esos adaptadores tienen que cumplir.
"""

from __future__ import annotations

from .policy import CI

_CIS = {
    "ci_0001": CI("ci_0001", "SRV-DC01", "critica", ("domain_controller",)),
    "ci_0002": CI("ci_0002", "FW-PERIM", "critica", ("firewall_perimetral",)),
    "ci_0003": CI("ci_0003", "SRV-DC02", "critica", ("domain_controller",)),
    "ci_4471": CI("ci_4471", "SRV-FILE01", "alta", ("file_server",)),
    "ci_0119": CI("ci_0119", "NAS-BACKUP", "critica", ("sistema_backup",)),
    "ci_5502": CI("ci_5502", "PRN-ADMIN", "baja", ("impresora",)),
}


class DatosDemo:
    """Implementa el contrato que consumen las tools de itier_agent.tools."""

    # -- CMDB ----------------------------------------------------------------

    def get_ci_obj(self, ci_id: str) -> CI | None:
        return _CIS.get(ci_id)

    def get_ci(self, ci_id: str) -> dict:
        ci = _CIS.get(ci_id)
        if not ci:
            return {"error": f"CI {ci_id} no encontrado"}
        return {
            "id": ci.id,
            "nombre": ci.nombre,
            "criticidad": ci.criticidad,
            "roles": list(ci.roles),
            "so": "Windows Server 2022",
            "volumenes": [
                {"unidad": "C:", "total_gb": 120, "uso_pct": 61},
                {"unidad": "D:", "total_gb": 900, "uso_pct": 92},
            ],
            "ubicacion": "Casa central — rack A",
        }

    def search_cis(self, tipo=None, criticidad=None, texto=None) -> list[dict]:
        out = []
        for ci in _CIS.values():
            if criticidad and ci.criticidad != criticidad:
                continue
            if texto and texto.lower() not in ci.nombre.lower():
                continue
            out.append({"id": ci.id, "nombre": ci.nombre, "criticidad": ci.criticidad})
        return out

    def impact_graph(self, ci_id: str) -> dict:
        if ci_id == "ci_4471":
            return {
                "ci": "SRV-FILE01",
                "depende_de": ["SRV-DC01 (autenticación)", "SW-CORE01 (red)"],
                "dependen_de_el": [
                    {"servicio": "Carpetas compartidas", "usuarios": 38},
                    {"servicio": "ERP — repositorio de documentos", "usuarios": 12},
                    {"servicio": "Respaldo diario", "criticidad": "alta"},
                ],
                "usuarios_afectados_si_cae": 38,
            }
        return {"ci": ci_id, "depende_de": [], "dependen_de_el": []}

    # -- Tickets -------------------------------------------------------------

    def tickets_similares(self, ci_id: str, sintoma: str, limite: int = 5) -> list[dict]:
        return [
            {
                "id": "INC-2291",
                "fecha": "2026-05-14",
                "sintoma": "Volumen D: al 94% en SRV-FILE01",
                "causa_raiz": "Acumulación de temporales del ERP en D:\\Temp",
                "resolucion": "playbook limpiar_temp_windows — liberó 71 GB",
                "tiempo_resolucion_min": 6,
            },
            {
                "id": "INC-1877",
                "fecha": "2026-03-02",
                "sintoma": "Volumen D: al 91% en SRV-FILE01",
                "causa_raiz": "Ídem — el ERP no purga temporales tras cierre mensual",
                "resolucion": "playbook limpiar_temp_windows — liberó 64 GB",
                "tiempo_resolucion_min": 5,
            },
            {
                "id": "INC-1502",
                "fecha": "2026-01-08",
                "sintoma": "Volumen D: al 96% en SRV-FILE01",
                "causa_raiz": "Ídem",
                "resolucion": "Limpieza manual",
                "tiempo_resolucion_min": 47,
            },
        ][:limite]

    def actualizar_ticket(self, ticket_id, diagnostico, plan, categoria) -> dict:
        return {"ok": True, "ticket": ticket_id, "estado": "en_progreso"}

    # -- Métricas y logs -----------------------------------------------------

    def metrics(self, ci_id: str, metrica: str, ventana_horas: int = 24) -> dict:
        if metrica.startswith("disco"):
            return {
                "metrica": metrica,
                "unidad": "D:",
                "serie": [
                    {"h": -24, "v": 78},
                    {"h": -18, "v": 81},
                    {"h": -12, "v": 85},
                    {"h": -6, "v": 89},
                    {"h": -1, "v": 92},
                ],
                "tendencia": "creciente, ~0.6 pp/hora",
                "linea_base_30d": 74,
            }
        return {"metrica": metrica, "serie": [], "nota": "sin datos en la ventana"}

    def logs(self, ci_id: str, log: str, lineas: int = 100) -> dict:
        if log == "aplicacion":
            return {
                "log": "aplicacion",
                "lineas": [
                    "2026-08-10 11:02 ERP.Export  Cierre mensual iniciado",
                    "2026-08-10 11:47 ERP.Export  Generados 14.212 archivos en D:\\Temp",
                    "2026-08-10 12:03 ERP.Export  Cierre mensual finalizado",
                    "2026-08-10 12:03 ERP.Export  ADVERTENCIA: purga de temporales omitida",
                ],
            }
        return {"log": log, "lineas": [], "nota": "sin eventos relevantes"}

    def lifecycle(self, ci_id: str) -> dict:
        return {
            "ci": ci_id,
            "garantia_hasta": "2027-04-30",
            "contrato_soporte": "activo",
            "fin_soporte_fabricante": "2029-10-14",
        }

    def kb(self, consulta: str, limite: int = 3) -> list[dict]:
        return [
            {
                "id": "KB-0042",
                "titulo": "ERP no purga temporales tras el cierre mensual",
                "tipo": "error_conocido",
                "solucion_temporal": "Ejecutar limpiar_temp_windows sobre D:\\Temp",
                "solucion_definitiva": (
                    "Pendiente: solicitar al proveedor del ERP la corrección de la "
                    "rutina de purga. RFC-0031 abierto."
                ),
            }
        ][:limite]

    # -- Ejecución -----------------------------------------------------------

    def dry_run(self, playbook: str, ci_id: str, parametros: dict) -> dict:
        if playbook == "limpiar_temp_windows":
            return {
                "modo": "check",
                "cambios_previstos": 1,
                "detalle": "Eliminaría 14.212 archivos en D:\\Temp (~68 GB)",
                "uso_disco_posterior_estimado_pct": 84,
                "reversible": True,
            }
        return {"modo": "check", "cambios_previstos": 0, "detalle": "sin cambios"}

    def ejecutar(self, playbook: str, ci_id: str, parametros: dict) -> dict:
        return {
            "estado": "ok",
            "playbook": playbook,
            "duracion_s": 34,
            "cambios": 1,
            "verificacion": {"uso_disco_pct": 84, "condicion_cumplida": True},
            "reversion_disponible": True,
        }
