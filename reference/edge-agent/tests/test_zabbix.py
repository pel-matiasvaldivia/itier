"""Test del cliente de eventos de Zabbix del edge (camino real, HTTP simulado).

Verifica que ZabbixEventos construye bien las requests JSON-RPC (Bearer, método,
filtro por grupo) y arma los problemas enriquecidos con el host (problem.get +
event.get), y que FuenteZabbix los mapea a Evento. HTTP simulado con
httpx.MockTransport — no valida la forma exacta de Zabbix 7.0, sino que el código
hace lo que pretende.

    pip install -e reference/edge-agent
    python reference/edge-agent/tests/test_zabbix.py
"""

from __future__ import annotations

import json
import sys

import httpx

from itier_agent.events import FuenteZabbix
from itier_agent.fixtures import DatosDemo
from itier_agent.zabbix import ZabbixEventos

fails: list[str] = []
captured: list[dict] = []


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def _rpc(body, result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body.get("id")})


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content) if request.content else {}
    captured.append({"headers": {k.lower(): v for k, v in request.headers.items()}, "body": body})
    m = body.get("method")
    if m == "hostgroup.get":
        return _rpc(body, [{"groupid": "5"}])
    if m == "problem.get":
        return _rpc(body, [
            {"eventid": "88213", "severity": "4", "name": "disco D: 92%", "objectid": "T1"},
            {"eventid": "90011", "severity": "5", "name": "DC no responde", "objectid": "T2"},
        ])
    if m == "event.get":
        return _rpc(body, [
            {"eventid": "88213", "hosts": [{"hostid": "4471", "host": "SRV-FILE01", "name": "File"}]},
            {"eventid": "90011", "hosts": [{"hostid": "1", "host": "SRV-DESCONOCIDO", "name": "DC"}]},
        ])
    return httpx.Response(404, json={"error": {"message": "no simulado", "data": m}})


_real_Client = httpx.Client


def _fake_Client(*a, **k):
    k["transport"] = httpx.MockTransport(handler)
    return _real_Client(*a, **k)


httpx.Client = _fake_Client  # type: ignore[assignment]

# ── problemas() camino real ─────────────────────────────────────────────────
captured.clear()
with ZabbixEventos(url="http://zbx", token="TKN", grupo="iTier/Cliente X") as z:
    check("no entra en modo demo", z.demo is False)
    probs = z.problemas()

check("problemas(): devuelve 2", len(probs) == 2, str(len(probs)))
check("problemas(): enriquece el host (problem.get + event.get)",
      probs[0]["host"] == "SRV-FILE01" and probs[0]["hostid"] == "4471", str(probs[0]))
check("problemas(): trae severidad y nombre",
      probs[0]["severity"] == "4" and probs[0]["name"] == "disco D: 92%")

pg = next((c for c in captured if c["body"].get("method") == "problem.get"), None)
check("problem.get usa Bearer", pg and pg["headers"].get("authorization") == "Bearer TKN")
check("problem.get filtra por el grupo del tenant (groupids=[5])",
      pg and pg["body"]["params"].get("groupids") == ["5"], str(pg["body"]["params"]) if pg else "—")
eg = next((c for c in captured if c["body"].get("method") == "event.get"), None)
check("event.get pide selectHosts por eventids",
      eg and eg["body"]["params"].get("selectHosts") and set(eg["body"]["params"]["eventids"]) == {"88213", "90011"})

# ── FuenteZabbix: mapeo a Evento usando el resolver de CMDB ──────────────────
captured.clear()
datos = DatosDemo()
with ZabbixEventos(url="http://zbx", token="TKN", grupo="iTier/Cliente X") as z:
    fuente = FuenteZabbix(z, resolver_ci=lambda p: datos.ci_por_host(p.get("host") or "") or (p.get("host") or ""))
    evs = fuente.nuevos()

check("FuenteZabbix: 2 eventos", len(evs) == 2, str(len(evs)))
check("FuenteZabbix: severity 4→'alta', 5→'critica'",
      evs[0].severidad == "alta" and evs[1].severidad == "critica", f"{evs[0].severidad},{evs[1].severidad}")
check("FuenteZabbix: resolver mapea SRV-FILE01→ci_4471", evs[0].ci_id == "ci_4471", evs[0].ci_id)
check("FuenteZabbix: host no mapeado deja el nombre como pista", evs[1].ci_id == "SRV-DESCONOCIDO", evs[1].ci_id)
check("FuenteZabbix: clave de correlación zabbix:eventid", evs[0].clave_correlacion() == "zabbix:88213")

# ── modo demo (sin url/token) ───────────────────────────────────────────────
zd = ZabbixEventos(url=None, token=None)
check("sin url/token → modo demo", zd.demo is True)
check("demo: problemas() devuelve fixtures", len(zd.problemas()) == 2)

# ── resolver de CMDB ────────────────────────────────────────────────────────
check("ci_por_host mapea un host conocido", datos.ci_por_host("SRV-FILE01") == "ci_4471")
check("ci_por_host devuelve None para desconocido", datos.ci_por_host("NO-EXISTE") is None)

httpx.Client = _real_Client  # type: ignore[assignment]
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
