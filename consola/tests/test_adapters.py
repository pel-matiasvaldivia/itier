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
    ITIER_ADAPTER_CACHE_S="0",   # las pruebas de protocolo ven cada llamada; el caché se prueba aparte
)

from itier_console.adapters import glpi as glpi_mod  # noqa: E402
from itier_console.adapters import zabbix as zbx_mod  # noqa: E402
from itier_console.config import Config  # noqa: E402

fails = []
captured = []
WA_ESTADO = {"status": "ok", "connected": False, "session": {"state": "STARTING", "ready": False}, "qr": None}
TICKET_EMPTY = False  # cuando True, GET /Ticket simula el 400 de rango vacío


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def _rpc(body, result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body.get("id")})


GRAPH_ESTADO = {"tokens": 0, "usuarios_get": 0, "forzar_401": False, "sin_ubicacion": False}


def _graph(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    cuerpo = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
    if request.url.host == "login.microsoftonline.com":
        if "tenant-sin-consent" in p:
            return httpx.Response(400, json={"error": "invalid_client", "error_description": "AADSTS700016: Application not found in the directory.\r\nTrace ID: x"})
        GRAPH_ESTADO["tokens"] += 1
        return httpx.Response(200, json={"access_token": f"tok{GRAPH_ESTADO['tokens']}", "expires_in": 3599})
    auth = request.headers.get("authorization", "")
    if GRAPH_ESTADO["forzar_401"] and auth == "Bearer tok1":
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken", "message": "expired"}})
    if p == "/v1.0/organization":
        return httpx.Response(200, json={"value": [{"id": "T-1", "displayName": "Cliente SA", "verifiedDomains": [
            {"name": "clientesa.onmicrosoft.com", "isDefault": False}, {"name": "clientesa.com.ar", "isDefault": True}]}]})
    if p == "/v1.0/subscribedSkus":
        return httpx.Response(200, json={"value": [
            {"skuId": "sku-std", "skuPartNumber": "O365_BUSINESS_PREMIUM", "prepaidUnits": {"enabled": 10}, "consumedUnits": 4},
            {"skuId": "sku-viejo", "skuPartNumber": "VIEJO", "prepaidUnits": {"enabled": 0}, "consumedUnits": 0}]})
    if p == "/v1.0/users" and request.method == "GET":
        GRAPH_ESTADO["usuarios_get"] += 1
        if "page2" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "u3", "displayName": "C", "userPrincipalName": "c@x"}]})
        return httpx.Response(200, json={"value": [{"id": "u1", "displayName": "A", "userPrincipalName": "a@x", "accountEnabled": True,
                                                    "assignedLicenses": [{"skuId": "sku-std"}]},
                                                   {"id": "u2", "displayName": "B", "userPrincipalName": "b@x", "accountEnabled": False}],
                                         "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?page2=1"})
    if p == "/v1.0/users" and request.method == "POST":
        if cuerpo.get("userPrincipalName") == "dup@x":
            return httpx.Response(400, json={"error": {"code": "Request_BadRequest", "message": "Another object with the same value for property userPrincipalName already exists."}})
        return httpx.Response(201, json={"id": "nuevo-1", "displayName": cuerpo["displayName"], "userPrincipalName": cuerpo["userPrincipalName"], "accountEnabled": True})
    if p.endswith("/assignLicense"):
        return httpx.Response(200, json={"id": "nuevo-1"})
    if p == "/v1.0/users/sinloc" and request.method == "GET":
        return httpx.Response(200, json={"id": "sinloc", "usageLocation": None})
    if p == "/v1.0/users/prohibido":
        return httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied", "message": "Insufficient privileges to complete the operation."}})
    if p.startswith("/v1.0/users/") and request.method in ("PATCH", "DELETE"):
        return httpx.Response(204)
    return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound", "message": "no existe"}})


GWS_ESTADO = {"tokens": 0, "forzar_401": False, "pub": None, "ultimo_jwt": None, "modo_token": "ok"}


def _gws(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    cuerpo = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
    if request.url.host == "oauth2.googleapis.com":
        if GWS_ESTADO["modo_token"] == "unauthorized":
            return httpx.Response(401, json={"error": "unauthorized_client", "error_description": "Client is unauthorized to retrieve access tokens using this method"})
        if GWS_ESTADO["modo_token"] == "grant":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Not a valid email or user ID."})
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        GWS_ESTADO["ultimo_jwt"] = form["assertion"]
        GWS_ESTADO["tokens"] += 1
        return httpx.Response(200, json={"access_token": f"gtok{GWS_ESTADO['tokens']}", "expires_in": 3599})
    if GWS_ESTADO["forzar_401"] and request.headers.get("authorization") == "Bearer gtok1":
        return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
    if p == "/admin/directory/v1/customer/my_customer/domains":
        return httpx.Response(200, json={"domains": [{"domainName": "empresa.com.ar", "verified": True, "isPrimary": True},
                                                      {"domainName": "alias.com", "verified": True}, {"domainName": "sinverificar.com", "verified": False}]})
    if p == "/admin/directory/v1/users" and request.method == "GET":
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"users": [{"id": "3", "primaryEmail": "c@empresa.com.ar", "name": {"givenName": "C", "familyName": "C"}}]})
        return httpx.Response(200, json={"users": [
            {"id": "1", "primaryEmail": "a@empresa.com.ar", "name": {"givenName": "Ana", "familyName": "Paz", "fullName": "Ana Paz"}, "isAdmin": True,
             "lastLoginTime": "2026-11-18T10:00:00.000Z", "organizations": [{"title": "CTO", "department": "IT"}]},
            {"id": "2", "primaryEmail": "b@empresa.com.ar", "name": {"givenName": "Beto", "familyName": "Ruiz"}, "suspended": True, "lastLoginTime": "1970-01-01T00:00:00.000Z"}],
            "nextPageToken": "p2"})
    if p == "/admin/directory/v1/users" and request.method == "POST":
        if cuerpo.get("primaryEmail") == "dup@empresa.com.ar":
            return httpx.Response(409, json={"error": {"code": 409, "message": "Entity already exists.", "errors": [{"reason": "duplicate"}]}})
        return httpx.Response(200, json={"id": "9", "primaryEmail": cuerpo["primaryEmail"], "name": cuerpo["name"]})
    if p == "/admin/directory/v1/users/prohibido":
        return httpx.Response(403, json={"error": {"code": 403, "message": "Not Authorized to access this resource/api", "errors": [{"reason": "forbidden"}]}})
    if p.startswith("/admin/directory/v1/users/") and request.method in ("PATCH", "DELETE"):
        return httpx.Response(200 if request.method == "PATCH" else 204, json={} if request.method == "PATCH" else None)
    return httpx.Response(404, json={"error": {"message": "no", "errors": [{"reason": "notFound"}]}})


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host in ("admin.googleapis.com", "oauth2.googleapis.com"):
        captured.append({"method": request.method, "path": request.url.path, "host": request.url.host, "query": request.url.query.decode(),
                         "headers": {k.lower(): v for k, v in request.headers.items()},
                         "body": json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}})
        return _gws(request)
    if request.url.host in ("graph.microsoft.com", "login.microsoftonline.com"):
        captured.append({"method": request.method, "path": request.url.path, "host": request.url.host,
                         "headers": {k.lower(): v for k, v in request.headers.items()},
                         "body": json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}})
        return _graph(request)
    p = request.url.path
    body = json.loads(request.content) if request.content else {}
    captured.append({"method": request.method, "path": p,
                     "headers": {k.lower(): v for k, v in request.headers.items()}, "body": body})
    # ── GLPI (apirest.php) ──
    if p.endswith("/initSession"):
        return httpx.Response(200, json={"session_token": "SESS"})
    if p.endswith("/killSession"):
        return httpx.Response(200, json={})
    if p.endswith("/Profile") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 4, "name": "Super-Admin"}, {"id": 6, "name": "Technician"}, {"id": 7, "name": "Supervisor"}])
    if p.endswith("/User") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 11, "name": "existente", "is_active": 1}])
    if p.endswith("/User") and request.method == "POST":
        return httpx.Response(201, json={"id": 77})
    if p.endswith("/User/11/Profile_User") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 301, "users_id": 11, "profiles_id": 4}])
    if "/Profile_User/301" in p and request.method == "DELETE":
        return httpx.Response(200, json=[True])
    if p.endswith("/Profile_User") and request.method == "POST":
        return httpx.Response(201, json={"id": 302})
    if "/User/11" in p and request.method == "PUT":
        return httpx.Response(200, json=[{"11": True}])
    if p.endswith("/Entity"):
        if request.method == "POST":
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(200, json=[{"id": 0, "name": "Root"}])
    if p == "/health":
        return httpx.Response(200, json=WA_ESTADO)
    if "/TicketTask" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 5})
        return httpx.Response(200, json=[{"id": 1, "tickets_id": 77, "actiontime": 5400, "users_id_tech": 7, "date": "2026-11-05 10:00:00", "content": "x"}])
    if "/ITILSolution" in p:
        return httpx.Response(201, json={"id": 3})
    if request.method == "PUT" and p.endswith("/Ticket/77"):
        return httpx.Response(200, json=[{"77": True}])
    if "/ITILFollowup" in p:
        if request.method == "GET":
            return httpx.Response(200, json=[
                {"id": 2, "content": "&lt;p&gt;Segundo&lt;/p&gt;", "users_id": 7, "date_creation": "2026-11-19 10:05:00", "is_private": 1},
                {"id": 1, "content": "Primero", "users_id": 0, "date_creation": "2026-11-19 10:00:00", "is_private": 0}])
        return httpx.Response(201, json={"id": 9})
    if "/Ticket_User" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 1})
        # GET /Ticket/{id}/Ticket_User → actores (type 2 = asignado).
        return httpx.Response(200, json=[
            {"id": 55, "tickets_id": 77, "users_id": 7, "type": 2},
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
    # ── GLPI: regla de entidad por tag ──
    if "/RuleImportEntity" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 31})
        return httpx.Response(200, json=[{"id": 30, "name": "otra"}])
    if "/RuleCriteria" in p or "/RuleAction" in p:
        return httpx.Response(201, json={"id": 1})
    # ── GLPI: criticidad (curación de CMDB) ──
    if "/BusinessCriticity" in p:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 90})
        return httpx.Response(200, json=[{"id": "5", "name": "Crítica"}, {"id": "6", "name": "Alta"}])
    if p.endswith("/Item_Disk") and request.method == "GET":
        return httpx.Response(200, json=[
            {"id": 1, "items_id": 501, "itemtype": "Computer", "mountpoint": "C:", "totalsize": 100000, "freesize": 8000},
            {"id": 2, "items_id": 501, "itemtype": "Computer", "mountpoint": "D:", "totalsize": 500000, "freesize": 400000},
            {"id": 3, "items_id": 999, "itemtype": "Computer", "mountpoint": "C:", "totalsize": 1, "freesize": 1}])
    if p.endswith("/ItemAntivirus") and request.method == "GET":
        return httpx.Response(200, json=[
            {"id": 1, "items_id": 501, "itemtype": "Computer", "name": "Defender", "is_active": 0, "is_uptodate": 1}])
    if "/Computer/" in p and request.method == "PUT":
        return httpx.Response(200, json={"id": 501})
    if p.endswith("/Manufacturer") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 1, "name": "Lenovo"}, {"id": 2, "name": "Dell"}])
    if p.endswith("/ComputerModel") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 10, "name": "ThinkPad E14"}])
    if p.endswith("/ComputerType") and request.method == "GET":
        return httpx.Response(200, json=[{"id": 20, "name": "Laptop"}, {"id": 21, "name": "Desktop"}])
    if p.endswith("/Computer") and request.method == "GET":
        return httpx.Response(200, json=[
            {"id": 501, "name": "SRV-DC01", "entities_id": 2, "businesscriticities_id": 5},
            {"id": 601, "name": "SRV-APP", "entities_id": 3, "businesscriticities_id": 0},
            {"id": 701, "name": "NOTE-ANA", "entities_id": 9, "serial": " PF3ABC12 ", "manufacturers_id": 1, "computermodels_id": 10,
             "computertypes_id": 20, "last_inventory_update": "2026-11-19 08:30:00", "date_mod": "2026-01-01 00:00:00", "contact": "ana", "is_dynamic": 1},
            {"id": 702, "name": "PC-MANUAL", "entities_id": 9, "serial": "", "manufacturers_id": 2, "computermodels_id": 0, "computertypes_id": 21,
             "date_mod": "2026-02-01 10:00:00", "is_dynamic": 0},
            {"id": 703, "name": "PLANTILLA", "entities_id": 9, "is_template": 1},
            {"id": 704, "name": "BORRADA", "entities_id": 9, "is_deleted": 1}])
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
        if m == "action.get":
            return _rpc(body, [])
        if m == "template.get":
            return _rpc(body, [{"templateid": "10081"}])
        if m == "action.create":
            return _rpc(body, {"actionids": ["44"]})
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
        if m == "role.get":
            return _rpc(body, [{"roleid": "1", "name": "User role", "type": "1"}, {"roleid": "2", "name": "Admin role", "type": "2"},
                               {"roleid": "3", "name": "Super admin role", "type": "3"}])
        if m == "usergroup.get":
            if (params.get("filter") or {}).get("name") == ["iTier Soporte"]:
                return _rpc(body, [{"usrgrpid": "90"}])
            return _rpc(body, [{"usrgrpid": "90", "hostgroup_rights": [{"id": "7", "permission": "2"}]}])
        if m == "usergroup.update":
            return _rpc(body, {"usrgrpids": ["90"]})
        if m == "user.get":
            return _rpc(body, [])
        if m == "user.create":
            return _rpc(body, {"userids": ["55"]})
        if m == "user.update":
            return _rpc(body, {"userids": ["55"]})
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

# ── GLPI: criticidad (curación de CMDB) ──────────────────────────────────────
captured.clear()
with glpi_mod.cliente(cfg) as g:
    cis = g.listar_cis(2)
    check("glpi.listar_cis filtra por entidad", len(cis) == 1 and cis[0]["name"] == "SRV-DC01", str(cis))
    check("glpi.listar_cis resuelve el nombre de criticidad actual", cis[0]["criticidad"] == "Crítica", cis[0].get("criticidad"))
    r = g.fijar_criticidad("Computer", "501", "critica")
    check("glpi.fijar_criticidad devuelve el nombre GLPI", r.get("criticidad") == "Crítica", str(r))
with glpi_mod.cliente(cfg) as g:
    sal = g.salud_cis(["501", "601"])
check("glpi.salud_cis: discos de ese equipo (y no los de otros) en MB", [d["nombre"] for d in sal["501"]["discos"]] == ["C:", "D:"]
      and sal["501"]["discos"][0]["libre_mb"] == 8000, str(sal))
check("glpi.salud_cis: antivirus apagado detectado", sal["501"]["antivirus"] == [{"nombre": "Defender", "activo": False, "actualizado": True}], str(sal))
check("glpi.salud_cis: equipo sin inventario queda marcado", sal["601"]["inventariado"] is False and sal["501"]["inventariado"] is True)
_n0 = len(captured)
with glpi_mod.cliente(cfg) as g:
    gid, que = g.asegurar_usuario("jperez", "Cl4ve-Segura!", "jperez@x.com", "Juan Pérez", "tecnico")
    gid2, que2 = g.asegurar_usuario("existente", "Otra-Clave1!", "e@x.com", "Ex Istente", "pm")
_cap = captured[_n0:]
cre = next((x for x in _cap if x["path"].endswith("/User") and x["method"] == "POST"), None)
check("glpi: crea el usuario con perfil Technician en la entidad raíz (recursivo)",
      gid == "77" and que == "creado" and cre["body"]["input"]["_profiles_id"] == 6 and cre["body"]["input"]["_is_recursive"] == 1
      and cre["body"]["input"]["_useremails"] == ["jperez@x.com"] and cre["body"]["input"]["password"] == cre["body"]["input"]["password2"], str(cre))
check("glpi: si el usuario ya existe lo actualiza (clave) y le cambia el perfil a Supervisor",
      gid2 == "11" and que2 == "actualizado"
      and any("/Profile_User/301" in x["path"] and x["method"] == "DELETE" for x in _cap)
      and any(x["path"].endswith("/Profile_User") and x["method"] == "POST" and x["body"]["input"]["profiles_id"] == 7 for x in _cap))
_n1 = len(captured)
with zbx_mod.cliente(cfg) as z:
    zid, zque = z.asegurar_usuario("jperez", "Cl4ve-Segura!", "Juan Pérez", "tecnico", "iTier Soporte")
    z.dar_acceso_grupo("iTier Soporte", "5")
_capz = captured[_n1:]
zc = next((x for x in _capz if x.get("body", {}).get("method") == "user.create"), None)
check("zabbix: crea el usuario con rol User (tipo 1), clave y grupo de soporte",
      zid == "55" and zc["body"]["params"]["roleid"] == "1" and zc["body"]["params"]["passwd"] == "Cl4ve-Segura!"
      and zc["body"]["params"]["usrgrps"] == [{"usrgrpid": "90"}] and zc["body"]["params"]["name"] == "Juan", str(zc))
zu = next((x for x in _capz if x.get("body", {}).get("method") == "usergroup.update"), None)
check("zabbix: da lectura/escritura al grupo de hosts del cliente SIN pisar los derechos que ya tenía",
      zu and {d["id"] for d in zu["body"]["params"]["hostgroup_rights"]} == {"7", "5"}
      and [d for d in zu["body"]["params"]["hostgroup_rights"] if d["id"] == "5"][0]["permission"] == "3", str(zu))
put = next((x for x in captured if x["path"].endswith("/Computer/501") and x["method"] == "PUT"), None)
check("glpi.fijar_criticidad hace PUT con businesscriticities_id",
      put and "businesscriticities_id" in put["body"]["input"], str(put["body"]) if put else "—")

# ── Autoasignación: regla GLPI por tag + autorregistro Zabbix ────────────────
captured.clear()
with glpi_mod.cliente(cfg) as g:
    rid = g.asegurar_regla_tag("iTiers · X", "itier-x", 42)
check("glpi.asegurar_regla_tag crea la regla", rid == 31, rid)
crit = next((x for x in captured if x["path"].endswith("/RuleCriteria")), None)
check("glpi: criterio tag = itier-x", crit and crit["body"]["input"]["criteria"] == "tag"
      and crit["body"]["input"]["pattern"] == "itier-x" and crit["body"]["input"]["rules_id"] == 31)
act = next((x for x in captured if x["path"].endswith("/RuleAction")), None)
check("glpi: acción asigna la entidad", act and act["body"]["input"]["field"] == "entities_id"
      and act["body"]["input"]["value"] == 42)
captured.clear()
with zbx_mod.cliente(cfg) as z:
    aid = z.asegurar_autoregistro("iTiers · X", "itier-x", "5")
check("zbx.asegurar_autoregistro crea la acción", aid == "44", aid)
ac = next((x for x in captured if x["body"].get("method") == "action.create"), None)
check("zbx: autorregistro por metadata (eventsource=2, cond 24 contiene)",
      ac and ac["body"]["params"]["eventsource"] == 2
      and ac["body"]["params"]["filter"]["conditions"][0] == {"conditiontype": 24, "operator": 2, "value": "itier-x"})
ops = ac["body"]["params"]["operations"] if ac else []
check("zbx: operaciones = grupo del cliente + plantilla",
      {"operationtype": 4, "opgroup": [{"groupid": "5"}]} in ops
      and {"operationtype": 6, "optemplate": [{"templateid": "10081"}]} in ops)

# ── Túnel a Zabbix común (Cloudflare Tunnel + Access) ────────────────────────
import dataclasses  # noqa: E402
from itier_console.services import ServiceError  # noqa: E402
from itier_console.services import enrolamiento as enr  # noqa: E402

cli_x = {"slug": "x", "nombre": "X", "estado": "activo", "psk_key": "a" * 64, "psk_identity": "itier-x"}
cfg_t = dataclasses.replace(cfg, zbx_tunnel_hostname="itier-zbx.example.com", cf_access_client_id="abc123def456.access",
                            cf_access_client_secret="0123456789abcdef0123456789abcdef", public_glpi_url="https://itier-itsm.example.com")
check("túnel: sin configurar, falta", any("túnel" in f for f in enr.faltantes(cfg, cli_x)))
check("túnel: configurado, no falta nada (sin token por cliente)", enr.faltantes(cfg_t, cli_x) == [], str(enr.faltantes(cfg_t, cli_x)))
env_x = enr.env_completo(cfg_t, cli_x)
check("túnel: el .env trae hostname + service token",
      "ZBX_TUNNEL_HOSTNAME=itier-zbx.example.com" in env_x and "CF_ACCESS_CLIENT_ID=abc123def456.access" in env_x
      and "CF_ACCESS_CLIENT_SECRET=0123456789abcdef0123456789abcdef" in env_x and "MESH" not in env_x)
try:
    enr.env_completo(dataclasses.replace(cfg_t, zbx_tunnel_hostname="malo; rm -rf /"), cli_x)
    check("túnel: hostname inyectable rechazado", False)
except ServiceError:
    check("túnel: hostname inyectable rechazado", True)

# ── GLPI: lista de tickets vacía (install nuevo) → el 400 se trata como [] ────
TICKET_EMPTY = True
with glpi_mod.cliente(cfg) as g:
    check("glpi.listar_tickets tolera el 400 de rango vacío", g.listar_tickets() == [])
TICKET_EMPTY = False

# ── MeshCentral: protocolo de /control.ashx contra un servidor WebSocket falso ─
import base64  # noqa: E402
import threading  # noqa: E402
from websockets.sync.server import serve  # noqa: E402
from itier_console.adapters import AdapterError, mesh as mesh_mod  # noqa: E402

_mesh_visto = {"auth": None, "acciones": []}
_mesh_grupos = [{"_id": "mesh//OTRO@$x", "name": "otro", "mtype": 2}]
_mesh_ugrupos = {}
_mesh_usuarios = set()
_mesh_denegar = False


def _mesh_servidor(ws):
    _mesh_visto["auth"] = ws.request.headers.get("x-meshauth")
    for raw in ws:
        m = json.loads(raw)
        _mesh_visto["acciones"].append(m["action"])
        if m["action"] == "meshes":
            ws.send(json.dumps({"action": "meshes", "responseid": m["responseid"], "meshes": _mesh_grupos}))
        elif m["action"] == "createmesh":
            _mesh_grupos.append({"_id": "mesh//NUEVO@$id", "name": m["meshname"], "mtype": m["meshtype"]})
            ws.send(json.dumps({"action": "createmesh", "responseid": m["responseid"], "result": "ok"}))
        elif m["action"] == "usergroups":
            ws.send(json.dumps({"action": "usergroups", "ugroups": _mesh_ugrupos or None}))
        elif m["action"] == "createusergroup":
            _mesh_ugrupos["ugrp//NUEVOUG"] = {"name": m["name"]}
            ws.send(json.dumps({"action": "createusergroup", "responseid": m["responseid"], "result": "ok",
                                "ugrpid": "ugrp//NUEVOUG"}))
        elif m["action"] == "adduser":
            _mesh_visto.setdefault("usuarios", []).append(m)
            dup = m["username"] in _mesh_usuarios
            _mesh_usuarios.add(m["username"])
            ws.send(json.dumps({"action": "adduser", "responseid": m["responseid"], "result": "User already exists" if dup else "ok"}))
        elif m["action"] in ("addusertousergroup", "removeuserfromusergroup", "edituser", "deleteuser"):
            _mesh_visto.setdefault("ops", []).append(m)
            ws.send(json.dumps({"action": m["action"], "responseid": m["responseid"], "result": "ok"}))
        elif m["action"] == "addmeshuser":
            _mesh_visto.setdefault("compartido", []).append((m["meshid"], m["userids"], m["meshadmin"]))
            if _mesh_denegar:
                ws.send(json.dumps({"action": "addmeshuser", "responseid": m["responseid"], "result": "Permission denied"}))
            else:  # éxito real: result vacío + success/failed
                ws.send(json.dumps({"action": "addmeshuser", "responseid": m["responseid"], "result": "", "success": 1, "failed": 0}))
        elif m["action"] == "nodes":
            ws.send(json.dumps({"action": "userinfo"}))  # ruido que debe ignorarse
            ws.send(json.dumps({"action": "nodes", "responseid": m["responseid"], "nodes": {
                "mesh//NUEVO@$id": [{"_id": "node//NODO1@x", "name": "pc-1", "conn": 1}, {"_id": "node//NODO2@x", "name": "pc-2", "conn": 0}]}}))


_srv = serve(_mesh_servidor, "127.0.0.1", 0)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
cfg_mesh = dataclasses.replace(cfg, mesh_url=f"http://127.0.0.1:{_srv.socket.getsockname()[1]}",
                               mesh_user="itier", mesh_password="clave", mesh_support_group="SOPORTE")
with mesh_mod.cliente(cfg_mesh) as mc:
    mid = mc.asegurar_grupo("itier-x")
    mc.compartir_con_soporte(mid)
    eq = mc.equipos(mid)
check("mesh: autentica con x-meshauth (usuario,clave en base64)",
      _mesh_visto["auth"] == base64.b64encode(b"itier").decode() + "," + base64.b64encode(b"clave").decode())
check("mesh: crea el grupo si no existe y devuelve el id corto", mid == "NUEVO@$id" and "createmesh" in _mesh_visto["acciones"])
check("mesh: lista equipos con su estado (ignora mensajes ajenos)",
      eq == [{"nombre": "PC-1", "conectado": True, "nodeid": "NODO1@x"},
             {"nombre": "PC-2", "conectado": False, "nodeid": "NODO2@x"}], eq)
check("mesh: enlace remoto (viewmode 11, gotonode con id corto)",
      mesh_mod.url_remoto(cfg_mesh, "NODO1@xxxxx").endswith("/?viewmode=11&gotonode=NODO1@xxxxx&hide=63") and mesh_mod.nodo_valido("NODO1@xxxxx")
      and not mesh_mod.nodo_valido('a"><script>'))
check("mesh: enlaces a terminal (viewmode 12) y archivos (viewmode 13); un modo desconocido cae en el escritorio",
      mesh_mod.url_remoto(cfg_mesh, "NODO1@xxxxx", "terminal").endswith("/?viewmode=12&gotonode=NODO1@xxxxx&hide=63")
      and mesh_mod.url_remoto(cfg_mesh, "NODO1@xxxxx", "archivos").endswith("/?viewmode=13&gotonode=NODO1@xxxxx&hide=63")
      and "viewmode=11" in mesh_mod.url_remoto(cfg_mesh, "NODO1@xxxxx", "inventado"))
with mesh_mod.cliente(cfg_mesh) as mc2:
    antes = _mesh_visto["acciones"].count("createmesh")
    check("mesh: si el grupo existe no lo crea de nuevo", mc2.asegurar_grupo("itier-x") == "NUEVO@$id"
          and _mesh_visto["acciones"].count("createmesh") == antes)
    mc2.compartir_con_soporte("NUEVO@$id")
check("mesh: URL del agente Windows x64, en segundo plano",
      mesh_mod.url_agente_windows(cfg_mesh, "NUEVO@$id").endswith("/meshagents?id=4&meshid=NUEVO@$id&installflags=2"))
check("mesh: crea el grupo de usuarios SOPORTE si no existe y comparte el grupo del cliente con él",
      _mesh_visto.get("compartido") and _mesh_visto["compartido"][0] == ("mesh//NUEVO@$id", ["ugrp//NUEVOUG"], 4 | 8 | 16 | 32 | 64 | 128),
      _mesh_visto.get("compartido"))
check("mesh: los técnicos NO reciben derechos de editar el grupo ni administrar usuarios",
      _mesh_visto["compartido"][0][2] & 3 == 0)
check("mesh: re-compartir es idempotente y reutiliza el grupo de usuarios", len(_mesh_visto["compartido"]) == 2
      and list(_mesh_ugrupos) == ["ugrp//NUEVOUG"])
_mesh_denegar = True
try:
    with mesh_mod.cliente(cfg_mesh) as mc3:
        mc3.compartir_con_soporte(mc3.asegurar_grupo("itier-x"))
    check("mesh: un rechazo al compartir se informa", False)
except AdapterError as e:
    check("mesh: un rechazo al compartir se informa", "Permission denied" in str(e), str(e))
with mesh_mod.cliente(cfg_mesh) as mu:
    est1 = mu.asegurar_usuario("jperez", "Cl4ve!", "j@x.com", "tecnico", "SOPORTE")
    est2 = mu.asegurar_usuario("jperez", "Cl4ve!", "j@x.com", "tecnico", "SOPORTE")
    mu.bloquear_usuario("jperez", True, "tecnico", "SOPORTE")
    mu.bloquear_usuario("jperez", False, "tecnico", "SOPORTE")
    mu.cambiar_rol_usuario("jperez", "pm")
    mu.eliminar_usuario("jperez")
au = _mesh_visto["usuarios"][0]
check("mesh: alta de usuario con clave, correo, cambio de clave obligatorio y sin permiso de crear grupos",
      est1 == "creado" and au["pass"] == "Cl4ve!" and au["email"] == "j@x.com" and au["resetNextLogin"] is True and au["siteadmin"] == 64, str(au))
check("mesh: si el usuario ya existía no falla y lo informa", est2 == "existente")
ops = [(o["action"], o.get("siteadmin"), o.get("usernames") or o.get("userid")) for o in _mesh_visto["ops"]]
check("mesh: lo suma al grupo SOPORTE", ("addusertousergroup", None, ["jperez"]) in ops, str(ops))
check("mesh: baja = cuenta bloqueada (32) y fuera de SOPORTE; alta = derechos del rol y de vuelta",
      ("edituser", 32, "jperez") in ops and ("removeuserfromusergroup", None, "user//jperez") in ops and ("edituser", 64, "jperez") in ops, str(ops))
check("mesh: cambio de rol y eliminación", ("edituser", 64, "jperez") in ops and ("deleteuser", None, "jperez") in ops)
_srv.shutdown()

# ── Rendimiento: sesión de GLPI y token de Zabbix reutilizados, lecturas cacheadas, reintento si vencen ────
import dataclasses as _dc  # noqa: E402
from itier_console import perf  # noqa: E402

cfg_c = _dc.replace(cfg, adapter_cache_s=60)
glpi_mod._SESIONES.vaciar(); glpi_mod._LECTURAS.vaciar()
captured.clear()
with glpi_mod.cliente(cfg_c) as g1:
    g1.listar_cis(2)
with glpi_mod.cliente(cfg_c) as g2:
    g2.listar_cis(3)
n_init = sum(1 for x in captured if x["path"].endswith("/initSession"))
n_kill = sum(1 for x in captured if x["path"].endswith("/killSession"))
n_comp = sum(1 for x in captured if x["path"].endswith("/Computer") and x["method"] == "GET")
check("rendimiento: GLPI abre UNA sesión para varias pantallas y no la cierra", n_init == 1 and n_kill == 0, (n_init, n_kill))
check("rendimiento: la lista de equipos se baja una sola vez para dos clientes", n_comp == 1, n_comp)
with glpi_mod.cliente(cfg_c) as g3:
    g3.fijar_criticidad("Computer", "501", "critica")
captured.clear()
with glpi_mod.cliente(cfg_c) as g4:
    g4.listar_cis(2)
check("rendimiento: una escritura invalida el caché de lecturas", any(x["path"].endswith("/Computer") for x in captured))
# sesión vencida en GLPI: se vuelve a abrir y la llamada sale igual
_vencida = {"n": 0}
_handler_ok = handler
def _handler_vence(request):
    if request.url.path.endswith("/Computer") and request.method == "GET" and _vencida["n"] == 0:
        _vencida["n"] += 1
        return httpx.Response(401, json=["ERROR_SESSION_TOKEN_INVALID", "token inválido"])
    return _handler_ok(request)
glpi_mod._SESIONES.vaciar(); glpi_mod._LECTURAS.vaciar()
with glpi_mod.cliente(cfg_c) as gx:
    gx.listar_cis(2)           # abre sesión y la deja cacheada
glpi_mod._LECTURAS.vaciar(); captured.clear()
_real_h = handler
handler = _handler_vence  # type: ignore[assignment]
with glpi_mod.cliente(cfg_c) as gy:
    cis_y = gy.listar_cis(2)
handler = _real_h  # type: ignore[assignment]
check("rendimiento: si GLPI da por vencida la sesión cacheada, reabre y reintenta una vez",
      len(cis_y) == 1 and sum(1 for x in captured if x["path"].endswith("/initSession")) == 1, str(cis_y))
# Zabbix con usuario/clave: un solo user.login para varias pantallas
_logins = {"n": 0}
_zh = handler
def _handler_zlogin(request):
    body = json.loads(request.content) if request.content else {}
    if request.url.path.endswith("/api_jsonrpc.php"):
        if body.get("method") == "user.login":
            _logins["n"] += 1
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "TOK-%d" % _logins["n"], "id": body.get("id")})
        if body.get("method") == "hostgroup.get":
            if request.headers.get("authorization") == "Bearer TOK-1" and _logins.get("vence"):
                _logins["vence"] = False
                return httpx.Response(200, json={"jsonrpc": "2.0", "error": {"message": "Session terminated, re-login, please.", "data": ""}, "id": body.get("id")})
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": [], "id": body.get("id")})
    return _zh(request)
handler = _handler_zlogin  # type: ignore[assignment]
cfg_z = _dc.replace(cfg_c, zbx_token=None, zbx_user="u", zbx_password="p")
zbx_mod._TOKENS.vaciar()
for _ in range(3):
    with zbx_mod.cliente(cfg_z) as zz:
        zz._rpc("hostgroup.get", {})
check("rendimiento: Zabbix hace un solo user.login para varias pantallas", _logins["n"] == 1, _logins["n"])
_logins["vence"] = True
with zbx_mod.cliente(cfg_z) as zz:
    zz._rpc("hostgroup.get", {})
check("rendimiento: si el token de Zabbix venció, se vuelve a loguear y repite", _logins["n"] == 2, _logins["n"])
handler = _zh  # type: ignore[assignment]
# utilidades de medición
_antes = perf.iniciar()
with perf.medir("glpi"):
    pass
check("rendimiento: Server-Timing nombra el total y cada sistema", perf.server_timing(0.25, _antes).startswith("app;dur=250")
      and 'glpi;dur=' in perf.server_timing(0.25, _antes))
_res = perf.paralelo([lambda: 1, lambda: (_ for _ in ()).throw(ValueError("x")), lambda: 3])
check("rendimiento: paralelo conserva el orden y captura los errores", [r[0] for r in _res] == [True, False, True] and _res[0][1] == 1 and _res[2][1] == 3)
# asignar / tomar un ticket es idempotente y la reasignación reemplaza
_n3 = len(captured)
with glpi_mod.cliente(cfg) as g:
    g.asignar_ticket(77, 7)           # ya es el asignado → no debe pedir un duplicado
_c3 = captured[_n3:]
check("GLPI: tomar un ticket que ya es tuyo no hace POST (evita ERROR_GLPI_ADD)", not any(x["method"] == "POST" for x in _c3), _c3)
_n3 = len(captured)
with glpi_mod.cliente(cfg) as g:
    g.asignar_ticket(77, 9, reemplazar=True)
_c3 = captured[_n3:]
check("GLPI: reasignar quita al asignado anterior y suma al nuevo",
      any(x["method"] == "DELETE" and x["path"].endswith("/Ticket_User/55") for x in _c3)
      and any(x["method"] == "POST" and x["body"]["input"]["users_id"] == 9 for x in _c3), _c3)
# horas: tareas con tiempo
_n4 = len(captured)
with glpi_mod.cliente(cfg) as g:
    g.registrar_tarea(77, 1800, "Cambio de toner", 7)
    _tareas = g.listar_tareas()
_p = [x for x in captured[_n4:] if x["method"] == "POST" and x["path"].endswith("/TicketTask")]
check("GLPI: registrar_tarea crea una TicketTask con el tiempo en segundos",
      len(_p) == 1 and _p[0]["body"]["input"]["tickets_id"] == 77 and _p[0]["body"]["input"]["actiontime"] == 1800
      and _p[0]["body"]["input"]["users_id_tech"] == 7, _p)
check("GLPI: listar_tareas devuelve el tiempo y quién lo hizo", _tareas == [{"tickets_id": 77, "actiontime": 5400, "users_id_tech": 7, "date": "2026-11-05 10:00:00", "content": "x"}], _tareas)
# conversación, respuestas y cambios de estado
_n7 = len(captured)
with glpi_mod.cliente(cfg) as g:
    g.agregar_seguimiento(77, "Ya lo revisamos", privado=True, users_id=7)
    _conv = g.seguimientos(77)
    g.cambiar_estado(77, "pendiente")
    g.cambiar_estado(77, "resolver", "Se cambió el tóner")
    g.cambiar_estado(77, "cerrar")
_c7 = captured[_n7:]
_fu = next(x for x in _c7 if x["method"] == "POST" and x["path"].endswith("/ITILFollowup"))
check("GLPI: la respuesta de un técnico lleva is_private y su users_id", _fu["body"]["input"] == {"itemtype": "Ticket", "items_id": 77, "content": "Ya lo revisamos", "is_private": 1, "users_id": 7}, _fu)
check("GLPI: la conversación sale ordenada con autor, fecha y privacidad", [x["id"] for x in _conv] == [1, 2] and _conv[1]["privado"] is True and _conv[1]["fecha"] == "2026-11-19 10:05", _conv)
_puts = [x for x in _c7 if x["method"] == "PUT"]
check("GLPI: en espera y cerrar son PUT del estado (4 y 6)", [x["body"]["input"]["status"] for x in _puts] == [4, 6], _puts)
_sol = next(x for x in _c7 if x["path"].endswith("/ITILSolution"))
check("GLPI: resolver crea la solución (GLPI pasa el ticket a Resuelto)", _sol["body"]["input"] == {"itemtype": "Ticket", "items_id": 77, "content": "Se cambió el tóner"}, _sol)
try:
    with glpi_mod.cliente(cfg) as g:
        g.cambiar_estado(77, "inventado")
    _ok = False
except Exception:  # noqa: BLE001
    _ok = True
check("GLPI: una acción de estado desconocida se rechaza", _ok)
# equipos inventariados (para cruzar con la lista de activos)
with glpi_mod.cliente(cfg) as g:
    _eq = g.equipos_inventariados(9)
_por = {e["nombre"]: e for e in _eq}
check("GLPI: equipos inventariados de la entidad (sin plantillas ni borrados) con fabricante, modelo y tipo en texto",
      sorted(_por) == ["NOTE-ANA", "PC-MANUAL"] and _por["NOTE-ANA"]["fabricante"] == "Lenovo" and _por["NOTE-ANA"]["modelo"] == "ThinkPad E14"
      and _por["NOTE-ANA"]["tipo"] == "Laptop" and _por["NOTE-ANA"]["serie"] == "PF3ABC12", _eq)
check("GLPI: el último inventario sale de last_inventory_update (o de date_mod) y marca si lo reportó el agente",
      _por["NOTE-ANA"]["ultimo_inventario"] == "2026-11-19 08:30:00" and _por["NOTE-ANA"]["por_agente"] is True and _por["NOTE-ANA"]["usuario"] == "ana"
      and _por["PC-MANUAL"]["ultimo_inventario"] == "2026-02-01 10:00:00" and _por["PC-MANUAL"]["por_agente"] is False)
check("GLPI: una entidad vacía no devuelve equipos", glpi_mod.cliente(cfg).equipos_inventariados("") == [])
# seguimiento de ticket (respuesta del solicitante por correo/WhatsApp)
_n2 = len(captured)
with glpi_mod.cliente(cfg) as g:
    g.agregar_seguimiento(1042, "Ya volvió, gracias")
_f = [x for x in captured[_n2:] if x["path"].endswith("/ITILFollowup")]
check("GLPI: agregar_seguimiento hace POST /ITILFollowup sobre el ticket",
      len(_f) == 1 and _f[0]["method"] == "POST" and _f[0]["body"]["input"] == {"itemtype": "Ticket", "items_id": 1042, "content": "Ya volvió, gracias", "is_private": 0}, _f)
# WhatsApp (OpenWA): estado y QR
from itier_console.services import whatsapp as wa_svc  # noqa: E402
import dataclasses as _dc  # noqa: E402
_cw = _dc.replace(cfg, wa_api_url="http://openwa:8080", wa_api_key="k")
check("WhatsApp: arrancando → «iniciando», sin QR", wa_svc.estado(_cw)["estado"] == "iniciando")
WA_ESTADO = {"connected": False, "session": {"state": "WAITING_FOR_QR", "ready": False}, "qr": "2@AbCdEfGhIjKlMnOp,xyz=,abc=,def="}
_e = wa_svc.estado(_cw)
check("WhatsApp: con el texto del QR dibuja la imagen", _e["estado"] == "esperando_qr" and _e["qr"].startswith("data:image/svg+xml"), _e)
WA_ESTADO = {"connected": False, "session": {"state": "WAITING_FOR_QR"}, "qr": {"dataUrl": "data:image/png;base64,AAAA"}}
check("WhatsApp: acepta una imagen ya hecha dentro de un objeto", wa_svc.estado(_cw)["qr"] == "data:image/png;base64,AAAA")
WA_ESTADO = {"connected": True, "session": {"state": "READY", "ready": True}, "qr": None}
check("WhatsApp: sesión lista → «conectado», sin QR", wa_svc.estado(_cw)["estado"] == "conectado" and wa_svc.estado(_cw)["qr"] is None)
check("WhatsApp: la API key viaja como X-API-Key", any(x["headers"].get("x-api-key") == "k" for x in captured if x["path"] == "/health"))
check("WhatsApp: sin URL queda «no configurado»", wa_svc.estado(_dc.replace(cfg, wa_api_url=None))["estado"] == "no_configurado")
# ── Microsoft 365 (Graph) ──
from itier_console.adapters import m365 as m365_mod  # noqa: E402
_cm = _dc.replace(cfg, m365_client_id="app-id", m365_client_secret="secreto")
m365_mod._TOKENS.clear()
_g = m365_mod.cliente(_cm, "tenant-1")
_org = _g.organizacion()
check("M365: lee la organización y su dominio principal", _org["nombre"] == "Cliente SA" and _org["dominio_principal"] == "clientesa.com.ar" and len(_org["dominios"]) == 2, _org)
_pl = _g.planes()
check("M365: planes con nombre comercial y licencias libres, sin los vencidos", len(_pl) == 1 and _pl[0]["nombre"] == "Microsoft 365 Business Standard" and _pl[0]["libres"] == 6, _pl)
_us = _g.listar_usuarios()
check("M365: lista usuarios siguiendo la paginación", [u["id"] for u in _us] == ["u1", "u2", "u3"] and _us[1]["habilitado"] is False and _us[0]["licencias"] == ["sku-std"], _us)
check("M365: el token se pide una sola vez por tenant (caché)", GRAPH_ESTADO["tokens"] == 1, GRAPH_ESTADO)
_tk = [x for x in captured if x.get("host") == "login.microsoftonline.com"]
check("M365: el token sale por client credentials con el scope de Graph", _tk and "/tenant-1/oauth2/v2.0/token" in _tk[0]["path"], _tk)
check("M365: las llamadas a Graph llevan el Bearer", all(x["headers"].get("authorization") == "Bearer tok1" for x in captured if x.get("host") == "graph.microsoft.com"))
_n5 = len(captured)
_nuevo = _g.crear_usuario(upn="ana@clientesa.com.ar", nombre="Ana", apellido="Gómez", clave="Cl4ve-Temporal!", cargo="Ventas", sku_ids=["sku-std"])
_c5 = [x for x in captured[_n5:] if x.get("host") == "graph.microsoft.com"]
_post = next(x for x in _c5 if x["method"] == "POST" and x["path"] == "/v1.0/users")
check("M365: crear usuario manda UPN, ubicación AR y clave con cambio obligatorio",
      _post["body"]["userPrincipalName"] == "ana@clientesa.com.ar" and _post["body"]["usageLocation"] == "AR" and _post["body"]["mailNickname"] == "ana"
      and _post["body"]["passwordProfile"] == {"forceChangePasswordNextSignIn": True, "password": "Cl4ve-Temporal!"} and _post["body"]["displayName"] == "Ana Gómez", _post)
_al = next(x for x in _c5 if x["path"].endswith("/assignLicense"))
check("M365: asigna la licencia al crear", _al["body"]["addLicenses"][0]["skuId"] == "sku-std" and _al["path"] == "/v1.0/users/nuevo-1/assignLicense", _al)
try:
    _g.crear_usuario(upn="dup@x", nombre="D", apellido="D", clave="x")
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "already exists" in str(_e)
check("M365: un UPN repetido devuelve el motivo de Microsoft", _ok)
try:
    _g.reiniciar_clave("prohibido", "Nueva-Clave1!")
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "no tiene el permiso necesario" in str(_e)
check("M365: un 403 se explica en castellano", _ok)
_n5 = len(captured)
_g.reiniciar_clave("u1", "Nueva-Clave1!")
_g.habilitar("u1", False)
_g.eliminar("u1")
_c5 = captured[_n5:]
check("M365: reiniciar clave, bloquear y eliminar usan PATCH/PATCH/DELETE",
      [(x["method"], x["path"]) for x in _c5 if x.get("host") == "graph.microsoft.com"] == [("PATCH", "/v1.0/users/u1"), ("PATCH", "/v1.0/users/u1"), ("DELETE", "/v1.0/users/u1")]
      and _c5[0]["body"]["passwordProfile"]["password"] == "Nueva-Clave1!" and _c5[1]["body"] == {"accountEnabled": False}, _c5)
_n5 = len(captured)
_g.asignar_licencias("sinloc", ["sku-std"], ["viejo"])
_c5 = [(x["method"], x["path"]) for x in captured[_n5:] if x.get("host") == "graph.microsoft.com"]
check("M365: si falta la ubicación de uso, se completa antes de asignar licencias", _c5 == [("GET", "/v1.0/users/sinloc"), ("PATCH", "/v1.0/users/sinloc"), ("POST", "/v1.0/users/sinloc/assignLicense")], _c5)
try:
    m365_mod.cliente(_cm, "tenant-sin-consent").organizacion()
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "no autorizó la aplicación" in str(_e) and "Trace ID" not in str(_e)
check("M365: sin consentimiento del administrador el error lo explica", _ok)
m365_mod._TOKENS.clear(); GRAPH_ESTADO["tokens"] = 0; GRAPH_ESTADO["forzar_401"] = True
_g.organizacion()
check("M365: ante un 401 pide un token nuevo y reintenta", GRAPH_ESTADO["tokens"] == 2, GRAPH_ESTADO)
GRAPH_ESTADO["forzar_401"] = False
try:
    m365_mod.cliente(_dc.replace(cfg, m365_client_id=None), "t").organizacion()
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "Falta configurar la aplicación" in str(_e)
check("M365: sin credenciales de aplicación avisa qué falta", _ok)
check("M365: nombres comerciales de planes comunes", m365_mod.nombre_plan("SPB") == "Microsoft 365 Business Premium" and m365_mod.nombre_plan("RARO") == "RARO")
# ── Google Workspace (Directory API) ──
import base64 as _b64m  # noqa: E402
from cryptography.hazmat.primitives import hashes as _h, serialization as _ser  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding as _pad, rsa as _rsa  # noqa: E402
from itier_console.adapters import gws as gws_mod  # noqa: E402
_priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _priv.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption()).decode()
_saj = json.dumps({"type": "service_account", "client_email": "itier@proy.iam.gserviceaccount.com", "client_id": "112233", "private_key": _pem})
check("GWS: valida el JSON de la cuenta de servicio", gws_mod.parsear_cuenta_servicio(_saj)["client_id"] == "112233")
for _malo, _txt in (("no json", "JSON"), ('{"type": "x"}', "service_account"), (json.dumps({"type": "service_account", "client_email": "a", "client_id": "1"}), "private_key"),
                    (json.dumps({"type": "service_account", "client_email": "a", "client_id": "1", "private_key": "basura"}), "clave privada")):
    try:
        gws_mod.parsear_cuenta_servicio(_malo)
        _ok = False
    except ValueError as _e:
        _ok = _txt in str(_e)
    check(f"GWS: JSON inválido rechazado ({_txt})", _ok)
_cg = _dc.replace(cfg, gws_sa_json=_saj)
gws_mod._TOKENS.clear()
_gg = gws_mod.cliente(_cg, "soporte@empresa.com.ar")
_d = _gg.dominios()
check("GWS: dominios verificados y principal (sin los no verificados)", _d == {"dominios": ["empresa.com.ar", "alias.com"], "principal": "empresa.com.ar"}, _d)
_jwt = GWS_ESTADO["ultimo_jwt"].split(".")
_claims = json.loads(_b64m.urlsafe_b64decode(_jwt[1] + "=="))
check("GWS: el JWT lleva emisor, administrador suplantado, scopes y destino", _claims["iss"] == "itier@proy.iam.gserviceaccount.com" and _claims["sub"] == "soporte@empresa.com.ar"
      and "admin.directory.user" in _claims["scope"] and _claims["aud"] == "https://oauth2.googleapis.com/token" and _claims["exp"] - _claims["iat"] == 3600, _claims)
try:
    _priv.public_key().verify(_b64m.urlsafe_b64decode(_jwt[2] + "=="), f"{_jwt[0]}.{_jwt[1]}".encode(), _pad.PKCS1v15(), _h.SHA256())
    _ok = True
except Exception:  # noqa: BLE001
    _ok = False
check("GWS: la firma RS256 del JWT es válida con la clave pública", _ok)
_us = _gg.listar_usuarios()
check("GWS: lista usuarios siguiendo la paginación y normaliza campos", [u["id"] for u in _us] == ["1", "2", "3"] and _us[0]["admin"] and _us[0]["cargo"] == "CTO"
      and _us[1]["suspendido"] and _us[1]["ultimo_ingreso"] == "" and _us[0]["ultimo_ingreso"] == "2026-11-18", _us)
_pg = [x for x in captured if x.get("host") == "admin.googleapis.com" and x["path"] == "/admin/directory/v1/users"]
check("GWS: pide los usuarios del cliente (my_customer) ordenados por correo", _pg and "customer=my_customer" in _pg[0]["query"] and "orderBy=email" in _pg[0]["query"], _pg)
check("GWS: el token se pide una sola vez (caché)", GWS_ESTADO["tokens"] == 1, GWS_ESTADO)
check("GWS: las llamadas llevan el Bearer", all(x["headers"].get("authorization") == "Bearer gtok1" for x in captured if x.get("host") == "admin.googleapis.com"))
_n6 = len(captured)
_nu = _gg.crear_usuario(email="nueva@empresa.com.ar", nombre="Nora", apellido="Gil", clave="Cl4ve-Temporal!", cargo="Ventas", departamento="Comercial")
_post = next(x for x in captured[_n6:] if x["method"] == "POST")
check("GWS: crear usuario manda nombre, clave con cambio obligatorio y organización",
      _post["body"]["primaryEmail"] == "nueva@empresa.com.ar" and _post["body"]["password"] == "Cl4ve-Temporal!" and _post["body"]["changePasswordAtNextLogin"] is True
      and _post["body"]["name"] == {"givenName": "Nora", "familyName": "Gil"} and _post["body"]["organizations"][0]["title"] == "Ventas", _post)
try:
    _gg.crear_usuario(email="dup@empresa.com.ar", nombre="D", apellido="D", clave="x")
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "Ya existe un usuario" in str(_e)
check("GWS: un correo repetido se explica", _ok)
try:
    _gg.reiniciar_clave("prohibido", "Nueva-Clave1!")
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "no tiene privilegios" in str(_e)
check("GWS: un 403 se explica en castellano", _ok)
_n6 = len(captured)
_gg.reiniciar_clave("2", "Nueva-Clave1!")
_gg.suspender("2", True)
_gg.actualizar_usuario("2", {"nombre": "Beto", "apellido": "R"})
_gg.eliminar("2")
_c6 = [x for x in captured[_n6:] if x.get("host") == "admin.googleapis.com"]
check("GWS: reiniciar clave, suspender, editar y eliminar usan PATCH/PATCH/PATCH/DELETE",
      [(x["method"], x["path"]) for x in _c6] == [("PATCH", "/admin/directory/v1/users/2")] * 3 + [("DELETE", "/admin/directory/v1/users/2")]
      and _c6[0]["body"] == {"password": "Nueva-Clave1!", "changePasswordAtNextLogin": True} and _c6[1]["body"] == {"suspended": True}
      and _c6[2]["body"] == {"name": {"givenName": "Beto", "familyName": "R"}}, _c6)
for _modo, _txt in (("unauthorized", "Delegación en todo el dominio"), ("grant", "super administrador activo")):
    gws_mod._TOKENS.clear(); GWS_ESTADO["modo_token"] = _modo
    try:
        gws_mod.cliente(_cg, "soporte@empresa.com.ar").dominios()
        _ok = False
    except Exception as _e:  # noqa: BLE001
        _ok = _txt in str(_e) and ("112233" in str(_e) if _modo == "unauthorized" else True)
    check(f"GWS: error de token «{_modo}» explicado con el ID de cliente / el motivo", _ok)
GWS_ESTADO["modo_token"] = "ok"
gws_mod._TOKENS.clear(); GWS_ESTADO["tokens"] = 0; GWS_ESTADO["forzar_401"] = True
gws_mod.cliente(_cg, "soporte@empresa.com.ar").dominios()
check("GWS: ante un 401 pide un token nuevo y reintenta", GWS_ESTADO["tokens"] == 2, GWS_ESTADO)
GWS_ESTADO["forzar_401"] = False
try:
    gws_mod.cliente(_dc.replace(cfg, gws_sa_json=None), "a@b.com").dominios()
    _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "Falta configurar la cuenta de servicio" in str(_e)
check("GWS: sin cuenta de servicio avisa qué falta", _ok)
# Token de aplicación para IMAP de Exchange Online (buzón compartido)
from itier_console.services import canales as _cnl  # noqa: E402
_ci = _dc.replace(cfg, imap_auth="oauth2_ms", imap_host="outlook.office365.com", imap_user="soporte@x.com", imap_ms_tenant="tenant-1", imap_ms_client_id="app", imap_ms_secret="sec")
_real_post = httpx.post
_form = {}
def _post_falso(url, **k):
    _form.update(k.get("data", {})); _form["url"] = url
    return httpx.Response(200, json={"access_token": "tok-imap"})
httpx.post = _post_falso
try:
    _t = _cnl._token_imap_ms(_ci)
finally:
    httpx.post = _real_post
check("IMAP Microsoft 365: pide el token de aplicación con el scope de Exchange Online", _t == "tok-imap" and _form["scope"] == "https://outlook.office365.com/.default"
      and _form["grant_type"] == "client_credentials" and _form["url"].endswith("/tenant-1/oauth2/v2.0/token") and _form["client_id"] == "app", _form)
httpx.post = lambda url, **k: httpx.Response(400, json={"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided.\r\nTrace ID: x"})
try:
    _cnl._token_imap_ms(_ci); _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "secreto de la aplicación es inválido o venció" in str(getattr(_e, "mensaje", _e)) and "Trace ID" not in str(getattr(_e, "mensaje", _e))
finally:
    httpx.post = _real_post
check("IMAP Microsoft 365: un secreto vencido se explica", _ok)
httpx.Client = _real_Client  # type: ignore[assignment]
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
