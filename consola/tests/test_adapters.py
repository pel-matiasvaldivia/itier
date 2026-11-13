"""Test de los adaptadores en modo REAL (no demo), con HTTP simulado.

La demo cortocircuita los adaptadores; este test ejercita el camino real contra
un servidor falso (httpx.MockTransport) para verificar que la consola construye
bien las requests (rutas, headers, envoltura JSON-RPC) y parsea las respuestas.

NO valida que GLPI 11 / Zabbix 7.0 usen exactamente estas formas —eso se confirma
contra el servidor real— sino que el código hace lo que pretende.

    pip install -e consola
    python consola/tests/test_adapters.py
"""

import json
import os
import sys

import httpx

os.environ.update(
    ITIER_CONSOLE_SECRET="x" * 16,
    ITIER_GLPI_URL="http://glpi",
    ITIER_GLPI_APP_TOKEN="APP",
    ITIER_GLPI_USER_TOKEN="USR",
    ITIER_ZBX_URL="http://zabbix-web:8080",
    ITIER_ZBX_TOKEN="TKN",
)

from itier_console.adapters import glpi as glpi_mod  # noqa: E402
from itier_console.adapters import zabbix as zbx_mod  # noqa: E402
from itier_console.config import Config  # noqa: E402

fails = []
captured = []
TICKET_EMPTY = False  # cuando True, GET /Ticket simula el 400 de rango vacío


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def _rpc(body, result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body.get("id")})


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    body = json.loads(request.content) if request.content else {}
    captured.append({"method": request.method, "path": p,
                     "headers": {k.lower(): v for k, v in request.headers.items()}, "body": body})
    # ── GLPI (apirest.php) ──
    if p.endswith("/initSession"):
        return httpx.Response(200, json={"session_token": "SESS"})
    if p.endswith("/killSession"):
        return httpx.Response(200, json={})
    if p.endswith("/Entity"):
        if request.method == "POST":
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(200, json=[{"id": 0, "name": "Root"}])
    if "/Ticket_User" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 1})
        # GET /Ticket/{id}/Ticket_User → actores (type 2 = asignado).
        return httpx.Response(200, json=[
            {"tickets_id": 77, "users_id": 7, "type": 2},
            {"tickets_id": 77, "users_id": 3, "type": 1},
        ])
    if "/User/" in p:
        return httpx.Response(200, json={"id": 7, "realname": "Pérez", "firstname": "Ana", "name": "aperez"})
    if "/Ticket" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 77})
        if TICKET_EMPTY:
            return httpx.Response(400, json=["ERROR_RANGE_EXCEED_TOTAL", "Range exceed total count"])
        return httpx.Response(200, json=[
            {"id": 1042, "name": "a", "status": 2, "priority": 3, "entities_id": 2},
            {"id": 1050, "name": "b", "status": 1, "priority": 4, "entities_id": 3},
        ])
    # ── Zabbix (api_jsonrpc.php) ──
    if p.endswith("/api_jsonrpc.php"):
        m = body.get("method")
        params = body.get("params") or {}
        if m == "apiinfo.version":
            return _rpc(body, "7.0.0")
        if m == "hostgroup.get":
            return _rpc(body, [])
        if m == "hostgroup.create":
            return _rpc(body, {"groupids": ["5"]})
        if m == "proxy.get":
            return _rpc(body, [])
        if m == "proxy.create":
            return _rpc(body, {"proxyids": ["9"]})
        if m == "host.get":
            if params.get("hostids"):
                return _rpc(body, [{"hostid": "501", "hostgroups": [{"groupid": "5"}]}])
            return _rpc(body, [{"hostid": "501", "host": "SRV", "name": "srv", "status": "0",
                                "proxyid": "9", "interfaces": [{"available": "1"}],
                                "hostgroups": [{"name": "iTier/X", "groupid": "5"}]}])
        if m == "host.update":
            return _rpc(body, {"hostids": ["501"]})
    return httpx.Response(404, json={"error": "ruta no simulada"})


# Inyectamos el MockTransport parcheando httpx.Client (lo usan ambos adaptadores).
_real_Client = httpx.Client


def _fake_Client(*a, **k):
    k["transport"] = httpx.MockTransport(handler)
    return _real_Client(*a, **k)


httpx.Client = _fake_Client  # type: ignore[assignment]

cfg = Config.from_env()
check("config: no es modo demo", cfg.demo is False)

# ── GLPI ────────────────────────────────────────────────────────────────────
captured.clear()
with glpi_mod.cliente(cfg) as g:
    check("glpi: no entra en modo demo", g.demo is False)
    check("glpi.health ok", g.health().get("ok") is True)
    eid = g.crear_entidad("Cliente X")
    check("glpi.crear_entidad parsea id", eid == 42, eid)
    tks = g.listar_tickets()
    check("glpi.listar_tickets ordena por id desc", len(tks) == 2 and tks[0]["id"] == 1050)
    tks2 = g.listar_tickets(entity_id=2)
    check("glpi.listar_tickets filtra por entidad client-side", len(tks2) == 1 and tks2[0]["id"] == 1042)
    tec = g.tecnico_de_ticket(77)
    check("glpi.tecnico_de_ticket resuelve el asignado (type=2)", tec == "Pérez Ana", tec)
    tid = g.crear_ticket("Asunto", "cuerpo", 2, 4)
    check("glpi.crear_ticket parsea id", tid == 77, tid)

init = next((r for r in captured if r["path"].endswith("/initSession")), None)
check("glpi: initSession manda App-Token", init and init["headers"].get("app-token") == "APP")
check("glpi: initSession manda Authorization user_token",
      init and init["headers"].get("authorization") == "user_token USR")
post_entity = next((r for r in captured if r["path"].endswith("/Entity") and r["method"] == "POST"), None)
check("glpi: POST Entity manda Session-Token", post_entity and post_entity["headers"].get("session-token") == "SESS")
check("glpi: POST Entity con input.name", post_entity and post_entity["body"]["input"]["name"] == "Cliente X")

# ── Zabbix ───────────────────────────────────────────────────────────────────
captured.clear()
with zbx_mod.cliente(cfg) as z:
    check("zbx: no entra en modo demo", z.demo is False)
    h = z.health()
    check("zbx.health ok + version", h.get("ok") is True and h.get("version") == "7.0.0")
    gid = z.crear_grupo("iTier/Cliente X")
    check("zbx.crear_grupo parsea groupid", gid == "5", gid)
    pid = z.crear_proxy("itier-cliente-x", "itier-cliente-x", "a" * 64)
    check("zbx.crear_proxy parsea proxyid", pid == "9", pid)
    hosts = z.listar_hosts("5")
    check("zbx.listar_hosts parsea + deriva disponibilidad",
          len(hosts) == 1 and hosts[0]["available"] == "1" and hosts[0]["host"] == "SRV")
    z.vincular_host("501", "9", "5")

apiinfo = next((r for r in captured if r["body"].get("method") == "apiinfo.version"), None)
check("zbx: apiinfo.version SIN Authorization", apiinfo and "authorization" not in apiinfo["headers"])
hg_create = next((r for r in captured if r["body"].get("method") == "hostgroup.create"), None)
check("zbx: llamadas autenticadas usan Bearer", hg_create and hg_create["headers"].get("authorization") == "Bearer TKN")
pcreate = next((r for r in captured if r["body"].get("method") == "proxy.create"), None)
check("zbx: proxy.create operating_mode=0 (activo)", pcreate and pcreate["body"]["params"]["operating_mode"] == 0)
check("zbx: proxy.create tls_accept=2 (PSK)", pcreate and pcreate["body"]["params"]["tls_accept"] == 2)
check("zbx: proxy.create manda PSK identity+key",
      pcreate and pcreate["body"]["params"]["tls_psk_identity"] == "itier-cliente-x"
      and len(pcreate["body"]["params"]["tls_psk"]) == 64)
hupd = next((r for r in captured if r["body"].get("method") == "host.update"), None)
check("zbx: host.update asigna proxyid y grupos",
      hupd and hupd["body"]["params"]["proxyid"] == "9"
      and any(g["groupid"] == "5" for g in hupd["body"]["params"]["groups"]))

# ── GLPI: lista de tickets vacía (install nuevo) → el 400 se trata como [] ────
TICKET_EMPTY = True
with glpi_mod.cliente(cfg) as g:
    check("glpi.listar_tickets tolera el 400 de rango vacío", g.listar_tickets() == [])
TICKET_EMPTY = False

httpx.Client = _real_Client  # type: ignore[assignment]
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
