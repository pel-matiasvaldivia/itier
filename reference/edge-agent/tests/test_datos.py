"""Test del AdaptadorReal (contrato del agente contra GLPI + Zabbix, HTTP simulado).

Verifica los caminos REALES (get_ci/get_ci_obj con criticidad+roles, actualizar_ticket,
metrics, lifecycle) y que lo no implementado devuelve vacío honesto / no_disponible,
con httpx.MockTransport. No valida la forma exacta de GLPI 11 / Zabbix 7.0.

    pip install -e reference/edge-agent
    python reference/edge-agent/tests/test_datos.py
"""

from __future__ import annotations

import json
import sys

import httpx

from itier_agent.datos import AdaptadorReal
from itier_agent.glpi import GLPITickets
from itier_agent.zabbix import ZabbixEventos

fails: list[str] = []


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def _rpc(body, result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body.get("id")})


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    body = json.loads(request.content) if request.content else {}
    # ── GLPI ──
    if p.endswith("/initSession"):
        return httpx.Response(200, json={"session_token": "SESS"})
    if p.endswith("/killSession"):
        return httpx.Response(200, json={})
    if "/Computer/42/Infocom" in p:
        return httpx.Response(200, json=[{"warranty_date": "2027-04-30", "buy_date": "2024-01-10",
                                          "end_date": "2029-10-14", "suppliers_id": "Proveedor X"}])
    if "/Computer/42" in p and request.method == "GET":
        return httpx.Response(200, json={"id": 42, "name": "SRV-DC01",
                                         "businesscriticities_id": "Crítica", "serial": "ABC",
                                         "locations_id": "Rack A"})
    if "/Computer/99" in p and request.method == "GET":
        return httpx.Response(200, json={"id": 99, "name": "PRN-OFFICE"})
    if p.endswith("/ITILFollowup"):
        return httpx.Response(201, json={"id": 7})
    # ── Zabbix ──
    if p.endswith("/api_jsonrpc.php"):
        m = body.get("method")
        if m == "host.get":
            return _rpc(body, [{"hostid": "1", "items": [
                {"itemid": "555", "key_": "system.cpu.util", "value_type": "0", "name": "CPU %"}]}])
        if m == "history.get":
            return _rpc(body, [{"clock": "1000", "value": "42"}, {"clock": "1060", "value": "47"}])
    return httpx.Response(404, json={"error": "no simulado"})


_real_Client = httpx.Client


def _fake_Client(*a, **k):
    k["transport"] = httpx.MockTransport(handler)
    return _real_Client(*a, **k)


httpx.Client = _fake_Client  # type: ignore[assignment]

glpi = GLPITickets(url="http://glpi", app_token="APP", user_token="USR")
zbx = ZabbixEventos(url="http://zbx", token="TKN")
d = AdaptadorReal(glpi, zbx, criticidad_default="alta")

# ── get_ci_obj: criticidad (del campo GLPI) + roles (heurística por nombre) ──
ci = d.get_ci_obj("Computer:42")
check("get_ci_obj trae el CI", ci is not None and ci.nombre == "SRV-DC01", str(ci))
check("criticidad desde business criticity de GLPI (Crítica→critica)", ci and ci.criticidad == "critica", ci.criticidad if ci else "—")
check("rol domain_controller por heurística de nombre (SRV-DC01)", ci and "domain_controller" in ci.roles, str(ci.roles) if ci else "—")
check("el id del CI se preserva (itemtype:id)", ci and ci.id == "Computer:42")

ci99 = d.get_ci_obj("Computer:99")
check("sin business criticity → default conservador (alta)", ci99 and ci99.criticidad == "alta", ci99.criticidad if ci99 else "—")

# ── get_ci (detalle) ────────────────────────────────────────────────────────
det = d.get_ci("Computer:42")
check("get_ci devuelve nombre+criticidad+roles", det["nombre"] == "SRV-DC01" and det["criticidad"] == "critica"
      and "domain_controller" in det["roles"], str(det))

# ── actualizar_ticket → seguimiento en GLPI ─────────────────────────────────
r = d.actualizar_ticket("77", "disco lleno en D:", "ejecutar limpiar_temp", "Almacenamiento")
check("actualizar_ticket ok", r.get("ok") is True and r.get("ticket") == "77", str(r))

# ── metrics → historial de Zabbix (host = nombre del CI) ────────────────────
met = d.metrics("Computer:42", "system.cpu.util", 6)
check("metrics trae la serie de Zabbix", len(met.get("serie", [])) == 2 and met["serie"][0]["value"] == "42", str(met))

# ── lifecycle → Infocom de GLPI ─────────────────────────────────────────────
lc = d.lifecycle("Computer:42")
check("lifecycle trae garantía desde Infocom", lc.get("garantia_hasta") == "2027-04-30", str(lc))

# ── vacío honesto (no fixtures de demo) ─────────────────────────────────────
check("tickets_similares vacío honesto", d.tickets_similares("Computer:42", "disco") == [])
check("kb vacío honesto", d.kb("disco lleno") == [])
check("search_cis vacío honesto", d.search_cis(texto="srv") == [])
check("impact_graph minimal con nota", "nota" in d.impact_graph("Computer:42"))
check("logs vacío honesto con nota", d.logs("Computer:42", "sistema")["lineas"] == [])

# ── ejecución: no disponible (Fase 4) ───────────────────────────────────────
check("dry_run → no_disponible", d.dry_run("limpiar_temp", "Computer:42", {})["estado"] == "no_disponible")
check("ejecutar → no_disponible", d.ejecutar("limpiar_temp", "Computer:42", {})["estado"] == "no_disponible")

httpx.Client = _real_Client  # type: ignore[assignment]
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
