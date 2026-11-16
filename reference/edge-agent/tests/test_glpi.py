"""Test del adaptador de tickets de GLPI del edge y del lazo evento→ticket.

Camino real con HTTP simulado (httpx.MockTransport): verifica que GLPITickets
abre el ticket, lo vincula al CI (Item_Ticket) y lo anota (ITILFollowup), y que el
Runner abre un ticket por cada evento nuevo. No valida la forma exacta de GLPI 11.

    pip install -e reference/edge-agent
    python reference/edge-agent/tests/test_glpi.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys

import httpx

from itier_agent.audit import Auditoria
from itier_agent.events import Evento
from itier_agent.fixtures import DatosDemo
from itier_agent.glpi import GLPITickets, TicketsDemo
from itier_agent.runner import Runner

fails: list[str] = []
captured: list[dict] = []


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    body = json.loads(request.content) if request.content else {}
    captured.append({"method": request.method, "path": p,
                     "headers": {k.lower(): v for k, v in request.headers.items()}, "body": body})
    if p.endswith("/initSession"):
        return httpx.Response(200, json={"session_token": "SESS"})
    if p.endswith("/killSession"):
        return httpx.Response(200, json={})
    if p.endswith("/Ticket"):
        return httpx.Response(201, json={"id": 77})
    if p.endswith("/ITILFollowup"):
        return httpx.Response(201, json={"id": 5})
    if p.endswith("/Item_Ticket"):
        return httpx.Response(201, json={"id": 9})
    if "/search/Computer" in p:
        return httpx.Response(200, json={"totalcount": 1, "count": 1, "data": [{"2": "42"}]})
    return httpx.Response(404, json={"error": "no simulado"})


_real_Client = httpx.Client


def _fake_Client(*a, **k):
    k["transport"] = httpx.MockTransport(handler)
    return _real_Client(*a, **k)


httpx.Client = _fake_Client  # type: ignore[assignment]

EV = Evento("zabbix", "88213", "ci_4471", "alta", "disco D: 92%", host="SRV-FILE01")

# ── GLPITickets camino real ─────────────────────────────────────────────────
captured.clear()
with GLPITickets(url="http://glpi", app_token="APP", user_token="USR", entity_id=3) as g:
    check("no entra en modo demo", g.demo is False)
    tid = g.abrir_desde_evento(EV)
    check("abrir_desde_evento parsea el id del ticket", tid == "77", tid)
    g.anotar(tid, "nota de prueba")

init = next((c for c in captured if c["path"].endswith("/initSession")), None)
check("initSession manda App-Token + user_token",
      init and init["headers"].get("app-token") == "APP"
      and init["headers"].get("authorization") == "user_token USR")
tk = next((c for c in captured if c["path"].endswith("/Ticket") and c["method"] == "POST"), None)
check("POST /Ticket con Session-Token + entidad + urgencia",
      tk and tk["headers"].get("session-token") == "SESS"
      and tk["body"]["input"]["entities_id"] == 3 and tk["body"]["input"]["urgency"] == 4,
      str(tk["body"]["input"]) if tk else "—")
it = next((c for c in captured if c["path"].endswith("/Item_Ticket")), None)
check("POST /Item_Ticket vincula el Computer correlacionado (items_id=42)",
      it and it["body"]["input"]["tickets_id"] == 77
      and it["body"]["input"]["itemtype"] == "Computer"
      and it["body"]["input"]["items_id"] == 42, str(it["body"]["input"]) if it else "—")
fu = next((c for c in captured if c["path"].endswith("/ITILFollowup")), None)
check("anotar → POST /ITILFollowup sobre el Ticket",
      fu and fu["body"]["input"]["itemtype"] == "Ticket" and fu["body"]["input"]["items_id"] == 77)

# ── ci_por_host ─────────────────────────────────────────────────────────────
with GLPITickets(url="http://glpi", app_token="APP", user_token="USR") as g:
    ci = g.ci_por_host("SRV-FILE01")
    check("ci_por_host parsea el id del Computer", ci and ci["items_id"] == "42", str(ci))

httpx.Client = _real_Client  # type: ignore[assignment]

# ── modo demo (sin url/tokens) ──────────────────────────────────────────────
gd = GLPITickets(url=None, app_token=None, user_token=None)
check("sin url/tokens → modo demo", gd.demo is True)
check("demo: abrir_desde_evento devuelve un id numérico", gd.abrir_desde_evento(EV).isdigit())

# ── Runner: abre ticket por evento (puerto TicketsDemo) ─────────────────────
aud = Auditoria(tenant="t")
tickets = TicketsDemo()
runner = Runner(auditoria=aud, datos=DatosDemo(), agente=None, tickets=tickets)
T = dt.datetime(2026, 8, 10, 10, 0, tzinfo=dt.timezone.utc)

o1 = runner.procesar(Evento("zabbix", "e1", "ci_4471", "alta", "disco D: 92%", host="SRV-FILE01"), ahora=T)
o2 = runner.procesar(Evento("zabbix", "e2", "ci_0001", "critica", "DC caído", host="SRV-DC01"), ahora=T)
check("Runner abre un ticket por evento sin ticket previo",
      o1["accion"] == "triage_offline" and bool(o1["ticket"]) and len(tickets.abiertos) == 2, str(tickets.abiertos))
check("outcomes traen el ticket abierto", bool(o1["ticket"]) and bool(o2["ticket"]))
check("Runner anota el triage en el ticket (offline)", len(tickets.notas) == 2, str(tickets.notas))
check("auditoría registró ticket_abierto",
      sum(1 for e in aud if e["tipo"] == "ticket_abierto") == 2)

# evento que YA trae ticket → usa ese, no abre otro
o3 = runner.procesar(Evento("zabbix", "e4", "ci_9", "media", "otra cosa", ticket_id="INC-9"), ahora=T)
check("evento con ticket previo usa ese ticket (no abre otro)",
      o3["ticket"] == "INC-9" and len(tickets.abiertos) == 2, str(tickets.abiertos))

# dedup: el mismo evento repetido NO abre otro ticket
o4 = runner.procesar(Evento("zabbix", "e1", "ci_4471", "alta", "disco D: 92%", host="SRV-FILE01"),
                     ahora=T + dt.timedelta(minutes=1))
check("evento duplicado → omitido, sin nuevo ticket",
      o4["accion"] == "omitido" and len(tickets.abiertos) == 2, str(tickets.abiertos))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
