"""Smoke test funcional de la consola en modo demo (sin GLPI/Zabbix reales).

Recorre el camino crítico de punta a punta con el TestClient de Starlette:
gate de autenticación, login + enrolamiento/verificación de TOTP (el código se
calcula de verdad con pyotp), alta de cliente con bundle, agentes, tickets, la
cadena de auditoría, y pruebas negativas (2FA inválido, CSRF falso, clave mala).

    pip install -e consola
    python consola/tests/smoke_demo.py

Sale con código != 0 si algún check falla (apto para CI).
"""

import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(
    ITIER_CONSOLE_DEMO="1",
    ITIER_CONSOLE_COOKIE_SECURE="0",
    ITIER_CONSOLE_DB=os.path.join(tmp, "consola.sqlite3"),
    ITIER_CONSOLE_SECRET="smoke-" + "0" * 32,
    ITIER_LOG_LEVEL="WARNING",
    ITIER_WA_HOOK_TOKEN="tok-wa-secreto-123",
    ITIER_SOPORTE_EMAIL="soporte@pymesenlinea.com.ar",
)

import pyotp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from itier_console.main import app  # noqa: E402

c = TestClient(app, follow_redirects=True)
fails = []


def check(nombre, cond, extra=""):
    print(("  ok " if cond else " FALLA ") + nombre + ("" if cond else f"  <<< {extra}"))
    if not cond:
        fails.append(nombre)


def csrf_de(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


# 1) Health sin auth
r = c.get("/healthz")
check("healthz 200", r.status_code == 200 and r.json().get("status") == "ok")

# 2) Gate de auth
check("gate: / sin sesión lleva a /login", c.get("/").url.path == "/login")
check("gate: /clientes sin sesión lleva a /login", c.get("/clientes").url.path == "/login")

# 3) Login primer factor (demo admin)
html = c.get("/login").text
r = c.post("/login", data={"username": "admin", "password": "itier-demo-admin", "csrf": csrf_de(html)})
check("login lleva a enrolar 2FA", r.url.path == "/login/2fa/enrolar", r.url.path)

# 4) Enrolamiento TOTP con código real
html = r.text
m = re.search(r'class="codebox"><code>([^<]+)</code>', html)
secret = m.group(1) if m else ""
check("enrolar: se muestra el secreto TOTP", bool(secret))
r = c.post("/login/2fa/enrolar", data={"code": pyotp.TOTP(secret).now(), "csrf": csrf_de(html)})
check("2FA enrolado → entra al tablero", r.url.path == "/" and "Tablero" in r.text, r.url.path)
check("dashboard: panel de tickets recientes", "Tickets recientes" in r.text)
check("dashboard: ticket con asunto y técnico", "No anda el correo" in r.text and "María Técnica" in r.text)

# 5) Rutas protegidas accesibles
check("clientes 200", c.get("/clientes").status_code == 200)
check("auditoria 200", c.get("/auditoria").status_code == 200)

# 6) Alta de cliente + bundle
html = c.get("/clientes/nuevo").text
r = c.post("/clientes/nuevo", data={"nombre": "Prueba SRL", "notas": "test", "csrf": csrf_de(html)})
check("alta de cliente → instaladores", r.url.path == "/clientes/prueba-srl/instaladores", r.url.path)
det = c.get("/clientes/prueba-srl").text
check("detalle enlaza a instaladores", "/clientes/prueba-srl/instaladores" in det)
check("bundle manual tiene ITIER_TENANT", "ITIER_TENANT=prueba-srl" in det)
check("bundle manual NO expone el secreto del túnel", "demo-secreto" not in det)
check("bundle manual tiene PSK identity", "ZBX_TLSPSKIDENTITY=itier-prueba-srl" in det)
check("bundle manual muestra PSK (64 hex)", bool(re.search(r'id="bundle-psk">[0-9a-f]{64}<', det)))

# 6b) Enrolamiento automatizado: instaladores preconfigurados
import subprocess  # noqa: E402

INST = "/clientes/prueba-srl/instaladores"
pag = c.get(INST).text
check("instaladores: túnel a Zabbix configurado (común)", "Túnel seguro al servidor Zabbix" in pag and "itier-zbx.demo.example" in pag)
SECRETO = "demo-secreto-0123456789abcdef"
check("instaladores: la página NO muestra el secreto del túnel", SECRETO not in pag)
r = c.get(INST)

r = c.post(INST + "/enlace", data={"csrf": csrf_de(r.text)})
m = re.search(r"/enrolar/([A-Za-z0-9_\-]+)/install\.sh \| sudo bash", r.text)
check("instaladores: genera comando de una línea", bool(m))
tok = m.group(1) if m else "x"
check("instaladores: el comando se muestra una sola vez", "/enrolar/" + tok not in c.get(INST).text)

pub = TestClient(app)  # el appliance: sin sesión
r = pub.get(f"/enrolar/{tok}/install.sh")
sh = r.text
check("install.sh: 200 sin sesión con token válido", r.status_code == 200)
check("install.sh: trae el .env completo",
      "ITIER_TENANT=prueba-srl" in sh and "ZBX_TUNNEL_HOSTNAME=itier-zbx.demo.example" in sh
      and f"CF_ACCESS_CLIENT_SECRET={SECRETO}" in sh and "ZBX_TLSCONNECT=psk" in sh)
check("install.sh: trae la PSK y el compose", bool(re.search(r"printf '%s' '[0-9a-f]{64}'", sh)) and "zbx-tunnel:" in sh and "cap_add" not in sh)
check("install.sh: sin marcadores sin reemplazar", "@@" not in sh)
check("install.sh: no cacheable", r.headers.get("cache-control") == "no-store")
ruta_sh = os.path.join(tmp, "install.sh")
open(ruta_sh, "w").write(sh)
check("install.sh: sintaxis bash válida", subprocess.run(["bash", "-n", ruta_sh]).returncode == 0)
check("token inexistente → 404", pub.get("/enrolar/token-que-no-existe/install.sh").status_code == 404)

# Ejecución REAL del instalador con docker/systemctl simulados (sandbox en tmp).
stub = os.path.join(tmp, "bin"); os.makedirs(stub)
for nombre, cuerpo in {
    "docker": 'echo "docker $*" >> "$STUB_LOG"\ncase "$*" in *"--format"*) echo "zabbix-proxy running";; esac\n',
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n',
    "curl": 'echo "curl $*" >> "$STUB_LOG"\n',
}.items():
    f = os.path.join(stub, nombre)
    open(f, "w").write("#!/bin/sh\n" + cuerpo)
    os.chmod(f, 0o755)
dest = os.path.join(tmp, "opt-itier"); units = os.path.join(tmp, "units"); os.makedirs(units)
env = dict(os.environ, PATH=stub + ":" + os.environ["PATH"], STUB_LOG=os.path.join(tmp, "stub.log"),
           ITIER_DIR=dest, ITIER_UNIT_DIR=units, ITIER_SKIP_CHECKS="1", ITIER_ESPERA="0")
pr = subprocess.run(["bash", ruta_sh], env=env, capture_output=True, text=True)
check("install.sh: corre de punta a punta", pr.returncode == 0, pr.stderr[-400:])
log_stub = open(env["STUB_LOG"]).read() if os.path.exists(env["STUB_LOG"]) else ""
envf = os.path.join(dest, ".env")
check("install.sh: escribe .env 600 con el tenant",
      os.path.exists(envf) and oct(os.stat(envf).st_mode)[-3:] == "600" and "ITIER_TENANT=prueba-srl" in open(envf).read())
pskf = os.path.join(dest, "enc", "proxy.psk")
check("install.sh: escribe la PSK (64 hex, sin salto de línea)",
      os.path.exists(pskf) and bool(re.fullmatch(r"[0-9a-f]{64}", open(pskf).read())))
check("install.sh: el compose escrito es idéntico a la plantilla",
      open(os.path.join(dest, "docker-compose.yml")).read().rstrip("\n")
      == open(os.path.join(os.path.dirname(__file__), "..", "itier_console", "deploy_templates", "docker-compose.yml")).read().rstrip("\n"))
check("install.sh: instala y arranca itier.service",
      os.path.exists(os.path.join(units, "itier.service")) and "systemctl restart itier.service" in log_stub)
check("install.sh: hace pull de imágenes", "docker compose pull" in log_stub)
check("install.sh: reporta 'ok' a la consola", f"/enrolar/{tok}/reporte" in log_stub and "estado=ok" in log_stub)

# El appliance "llama a casa"
r = pub.post(f"/enrolar/{tok}/reporte", data={"estado": "ok", "host": "appliance-01", "ip": "10.20.30.5"})
check("reporte del appliance aceptado", r.status_code == 200)
check("reporte con token falso rechazado", pub.post("/enrolar/falso/reporte", data={"estado": "ok"}).status_code == 404)
pag = c.get(INST).text
check("instaladores: muestra el appliance instalado", "Instalado" in pag and "appliance-01" in pag and "10.20.30.5" in pag)

# Usos acotados y revocación
pub.get(f"/enrolar/{tok}/install.sh"); pub.get(f"/enrolar/{tok}/install.sh")
check("enlace agotado tras max_usos → 404", pub.get(f"/enrolar/{tok}/install.sh").status_code == 404)
r = c.post(INST + "/enlace", data={"csrf": csrf_de(pag)})
tok2 = re.search(r"/enrolar/([A-Za-z0-9_\-]+)/install\.sh", r.text).group(1)
eid = re.findall(r"/enlace/(\d+)/revocar", r.text)[0]
c.post(f"{INST}/enlace/{eid}/revocar", data={"csrf": csrf_de(r.text)})
check("enlace revocado → 404", pub.get(f"/enrolar/{tok2}/install.sh").status_code == 404)

# Descargas del operador
r = c.get(INST + "/appliance.sh")
check("descarga appliance.sh (operador)", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "")
      and "ITIER_TENANT=prueba-srl" in r.text)
r = c.get(INST + "/windows.ps1")
check("descarga windows.ps1 preconfigurado",
      r.status_code == 200 and 'TAG=itier-$Tenant' in r.text and '$Tenant     = "prueba-srl"' in r.text
      and '$ProxyHost = "10.20.30.5"' in r.text and "@@" not in r.text and "\r\n" in r.text)
r = c.get(INST + "/windows.cmd")
cmd = r.text
check("descarga windows.cmd (doble clic, autoelevación UAC)",
      r.status_code == 200 and cmd.startswith("@echo off") and "-Verb RunAs" in cmd
      and cmd.count("#__ITIER_PS__") == 1 and 'TAG=itier-$Tenant' in cmd and "@@" not in cmd)
check("windows.cmd: ASCII puro y CRLF", cmd.isascii() and "\r\n" in cmd and "\n" not in cmd.replace("\r\n", ""))
check("windows.cmd: el bloque PowerShell arranca en el marcador",
      cmd[cmd.rfind("#__ITIER_PS__"):].splitlines()[1].startswith("#Requires"))

# Enlace para el cliente final (se manda por correo)
pag = c.get(INST).text
r = c.post(INST + "/enlace-equipos", data={"email": "no-es-un-mail", "csrf": csrf_de(pag)})
check("enlace equipos: correo inválido rechazado", "no es válida" in r.text and "/descargar/" not in r.text)
r = c.post(INST + "/enlace-equipos", data={"email": "contacto@cliente.example", "csrf": csrf_de(r.text)})
m = re.search(r"/descargar/([A-Za-z0-9_\-]+)<", r.text)
check("enlace equipos: se genera y trae mailto", bool(m) and "mailto:contacto%40cliente.example" in r.text)
tk = m.group(1) if m else "x"
r = pub.get(f"/descargar/{tk}")
check("página pública de descarga (sin sesión)", r.status_code == 200 and "Prueba SRL" in r.text and "doble clic" in r.text)
r = pub.get(f"/descargar/{tk}/instalador.cmd")
check("cliente descarga el .cmd por enlace", r.status_code == 200 and r.text.startswith("@echo off")
      and "itier-prueba-srl-instalar.cmd" in r.headers.get("content-disposition", ""))
check("enlace de equipos NO sirve para el instalador del appliance (secretos)",
      pub.get(f"/enrolar/{tk}/install.sh").status_code == 404)
check("enlace de appliance NO sirve como descarga pública", pub.get(f"/descargar/{tok}").status_code == 404)
check("descarga pública con token falso → 404", pub.get("/descargar/falso").status_code == 404)
check("autoasignación creada en el alta", "Asignación automática activa" in c.get(INST).text)

# Control post-instalación: el equipo verifica y reporta a la consola
check("windows: verifica servicios y conexión y muestra el resultado",
      'Estado-Servicio "GLPI-Agent"' in cmd and "RESULTADO DE LA INSTALACION" in cmd and "pause >nul" in cmd)
mrep = re.search(r'\$ReportUrl  = "(http[^"]+/equipos/[A-Za-z0-9_\-]+/reporte)"', cmd)
check("windows: trae la URL de reporte del cliente", bool(mrep))
ruta_rep = "/" + mrep.group(1).split("/", 3)[3] if mrep else "/equipos/x/reporte"
r = pub.post(ruta_rep, data={"host": "pc-ventas-01", "estado": "ok", "os": "Microsoft Windows 11 Pro",
                             "glpi_servicio": "activo", "glpi_conexion": "ok",
                             "zbx_servicio": "activo", "zbx_conexion": "ok", "detalle": ""})
check("reporte de equipo aceptado", r.status_code == 200, r.text)
pub.post(ruta_rep, data={"host": "PC-DEPOSITO", "estado": "error", "glpi_servicio": "activo", "glpi_conexion": "ok",
                         "zbx_servicio": "detenido", "zbx_conexion": "sin_respuesta",
                         "detalle": "<script>x</script> msiexec devolvio 1603"})
pub.post(ruta_rep, data={"host": "pc-ventas-01", "estado": "ok", "glpi_servicio": "activo", "glpi_conexion": "ok",
                         "zbx_servicio": "activo", "zbx_conexion": "ok"})
check("reporte con clave falsa rechazado", pub.post("/equipos/clave-falsa/reporte", data={"host": "x", "estado": "ok"}).status_code == 404)
pag = c.get(INST).text
check("instaladores: lista los equipos (uno por host, sin duplicar)", pag.count("<strong>PC-VENTAS-01</strong>") == 1 and "PC-DEPOSITO" in pag)
check("instaladores: muestra el equipo con problemas y su detalle", "con problemas" in pag and "msiexec devolvio 1603" in pag)
check("instaladores: el detalle se escapa (sin HTML inyectado)", "<script>x</script>" not in pag)
check("instaladores: distingue instalado de transmitiendo", "esperando" in pag)

# Equipo central de Windows: lleva el túnel (secretos) y fija la IP que usan los demás
check("equipo común: sin secretos del túnel", SECRETO not in cmd and "access tcp --hostname $TunelHost" in cmd
      and '$CfSecret   = ""' in cmd and '[string]$Rol = "equipo"' in cmd)
r = c.get(INST + "/windows.ps1?rol=central")
cen = r.text
check("central: trae túnel, token de servicio y rol",
      r.status_code == 200 and '$Rol = "central"' in cen and SECRETO in cen and "itier-zbx.demo.example" in cen
      and "cloudflared-windows-amd64.exe" in cen and "plugins/glpiinventory/" in cen
      and '$ProxyHost = "127.0.0.1"' in cen and "@@" not in cen and cen.isascii())
check("central: el nombre del archivo lo distingue", "itier-prueba-srl-central.ps1" in r.headers.get("content-disposition", ""))
r = c.get(INST + "/windows.cmd?rol=central")
check("central .cmd: doble clic con elevación", r.status_code == 200 and "-Verb RunAs" in r.text and SECRETO in r.text)
check("central: exige sesión", pub.get(INST + "/windows.ps1?rol=central", follow_redirects=False).status_code == 303)
mc = re.search(r'\$ReportUrl  = "(http[^"]+/equipos/[A-Za-z0-9_\-]+/reporte)"', cen)
check("central: reporta con una clave propia", bool(mc) and mc.group(1) != mrep.group(1))
ruta_c = "/" + mc.group(1).split("/", 3)[3]
r = pub.post(ruta_rep, data={"host": "pc-falsa", "estado": "ok", "ip": "6.6.6.6", "glpi_servicio": "activo",
                             "glpi_conexion": "ok", "zbx_servicio": "activo", "zbx_conexion": "ok"})
check("equipo común NO puede fijar la IP del central", "6.6.6.6" not in c.get(INST).text)
r = pub.post(ruta_c, data={"host": "srv-central", "estado": "ok", "ip": "192.168.50.10", "glpi_servicio": "activo",
                           "glpi_conexion": "ok", "zbx_servicio": "activo", "zbx_conexion": "ok"})
check("central reporta y fija la IP del equipo central", r.status_code == 200 and "192.168.50.10" in c.get(INST).text)
pub.post(ruta_c, data={"host": "srv-x", "estado": "ok", "ip": "999.1.1.1", "glpi_servicio": "activo",
                       "glpi_conexion": "ok", "zbx_servicio": "activo", "zbx_conexion": "ok"})
check("IP inválida del central se ignora", "999.1.1.1" not in c.get(INST).text)
check("los equipos comunes ahora apuntan al central",
      '$ProxyHost = "192.168.50.10"' in c.get(INST + "/windows.ps1").text)

# MeshCentral: el instalador de Windows lleva el agente del grupo del cliente
r = c.get(INST + "/windows.ps1")
check("windows: instala Mesh Agent desde el grupo del cliente (comillas simples por el «$»)",
      "-fullinstall" in r.text and "meshagent.exe" in r.text
      and re.search(r"\$MeshUrl    = 'https://itier-mesh\.demo\.example/meshagents\?id=4&meshid=[^']+&installflags=2'", r.text) is not None)
check("windows: Mesh no usa Zabbix ni muestra «pendiente» sin equipo central",
      "pendiente (todavia no esta instalado el equipo central" not in r.text and 'Estado-Servicio "Mesh Agent"' in r.text)
pub.post(ruta_rep, data={"host": "pc-ventas-01", "estado": "ok", "glpi_servicio": "activo", "glpi_conexion": "ok",
                         "mesh_servicio": "activo", "mesh_conexion": "ok", "zbx_servicio": "omitido", "zbx_conexion": "omitido"})
pagM = c.get(INST).text
check("instaladores: muestra el equipo en línea en Mesh", "en línea" in pagM and "Soporte remoto (Mesh)" in pagM)
check("instaladores: Zabbix queda en «Avanzado»", "<details" in pagM and "Avanzado" in pagM)
check("el cliente quedó con su grupo de Mesh", "Grupo del cliente: creado" in pagM)

# Panel de equipos del técnico: alertas de disco/antivirus y conexión remota embebida
from itier_console.services import equipos as _eq_svc  # noqa: E402

_ev = _eq_svc.evaluar({"inventariado": True,
                       "discos": [{"nombre": "C:", "total_mb": 100000, "libre_mb": 6000},
                                  {"nombre": "D:", "total_mb": 100000, "libre_mb": 50000}],
                       "antivirus": [{"nombre": "Defender", "activo": False, "actualizado": False}]}, 90, True)
check("alertas: disco C: al 94 % (media) y D: sin alerta",
      [d["pct"] for d in _ev["discos"]] == [94, 50] and ("media", "Disco C: al 94 % (5.9 GB libres)") in _ev["alertas"], _ev)
check("alertas: antivirus apagado = alta y ordenada primero", _ev["av_estado"] == "off" and _ev["alertas"][0][0] == "alta")
check("alertas: disco >= 95 % es alta", _eq_svc.evaluar({"inventariado": True, "discos": [{"nombre": "C:", "total_mb": 100, "libre_mb": 4}], "antivirus": [{"nombre": "A", "activo": True, "actualizado": True}]}, 90)["alertas"][0][0] == "alta")
check("alertas: sin antivirus detectado", _eq_svc.evaluar({"inventariado": True, "discos": [], "antivirus": []}, 90)["av_estado"] == "ninguno")
check("alertas: sin inventario no inventa alertas de antivirus", _eq_svc.evaluar(None, 90)["alertas"] == [])
check("alertas: sin conexión es baja", _eq_svc.evaluar(None, 90, False)["alertas"] == [("baja", "Sin conexión ahora")])

_orig_listar = _eq_svc.listar
_eq_svc.listar = lambda cfg, cli, salud=False: {"proxy": None, "notas": [], "equipos": [
    {"nombre": "PC-VENTAS-01", "en_glpi": True, "en_mesh": True, "en_linea": True, "zbx": None, "nodeid": "NODO-DEMO@abc$123",
     "url_remoto": "x", "discos": _ev["discos"], "antivirus": _ev["antivirus"], "av_estado": "off", "alertas": _ev["alertas"]},
    {"nombre": "PC-OK", "en_glpi": True, "en_mesh": True, "en_linea": True, "zbx": None, "nodeid": "NODO-OK@abc$123",
     "url_remoto": "x", "discos": [], "antivirus": [], "av_estado": "on", "alertas": []}]}
import itier_console.routes.equipos as _r_eq  # noqa: E402
_r_eq._CACHE.clear()
pe = c.get("/equipos")
check("equipos: el técnico ve la lista con discos, antivirus OFF y alertas",
      pe.status_code == 200 and "PC-VENTAS-01" in pe.text and "Antivirus APAGADO" in pe.text and 'l-pill bad"><i></i>OFF' in pe.text and 'class="l-bar' in pe.text
      and "Disco C: al 94 %" in pe.text, pe.text[-300:])
check("equipos: botón Conectar lleva al control remoto embebido", "/remoto?n=PC-VENTAS-01" in pe.text and "Conectar" in pe.text)
check("equipos: filtro «solo con alertas» oculta los equipos sanos",
      "PC-OK" not in c.get("/equipos?alertas=1").text and "PC-VENTAS-01" in c.get("/equipos?alertas=1").text)
rr = c.get("/equipos/prueba-srl/NODO-DEMO%40abc%24123/remoto?n=PC-VENTAS-01")
check("remoto: embebe MeshCentral en un iframe dentro del panel",
      rr.status_code == 200 and "<iframe" in rr.text and "gotonode=NODO-DEMO@abc$123" in rr.text.replace("&amp;", "&")
      and "viewmode=11" in rr.text, rr.text[-300:])
check("remoto: pestañas Escritorio, Terminal y Archivos", all(x in rr.text for x in ("🖥 Escritorio", "⌨️ Terminal", "📁 Archivos")) and "modo=terminal" in rr.text)
_rt_ = c.get("/equipos/prueba-srl/NODO-DEMO%40abc%24123/remoto?n=PC-VENTAS-01&modo=terminal")
check("remoto: la pestaña Terminal embebe viewmode=12 y marca la pestaña activa", "viewmode=12" in _rt_.text and "viewmode=11" not in _rt_.text.split("<iframe")[1].split(">")[0] and 'class="on" href' in _rt_.text)
_ra_ = c.get("/equipos/prueba-srl/NODO-DEMO%40abc%24123/remoto?n=PC-VENTAS-01&modo=archivos")
check("remoto: la pestaña Archivos embebe viewmode=13", "viewmode=13" in _ra_.text.split("<iframe")[1].split(">")[0])
check("remoto: un modo inventado cae en el escritorio", "viewmode=11" in c.get("/equipos/prueba-srl/NODO-DEMO%40abc%24123/remoto?n=PC&modo=hack%22").text.split("<iframe")[1].split(">")[0])
_aud = c.get("/auditoria").text
check("remoto: la auditoría distingue qué vista se abrió (terminal/archivos)", "terminal" in _aud and "archivos" in _aud and "equipo.remoto.abierto" in _aud)
check("remoto: nodo con caracteres raros se rechaza", "<iframe" not in c.get("/equipos/prueba-srl/a%22%3E%3Cscript/remoto").text)
check("remoto: exige sesión de operador", pub.get("/equipos", follow_redirects=False).status_code == 303
      and pub.get("/equipos/prueba-srl/NODO-DEMO%40abc/remoto", follow_redirects=False).status_code == 303)
check("auditoría registra la conexión remota", "equipo.remoto.abierto" in c.get("/auditoria").text)
_eq_svc.listar = _orig_listar

check("descargas exigen sesión", pub.get(INST + "/appliance.sh", follow_redirects=False).status_code == 303)
aud = c.get("/auditoria").text
check("auditoría registra el enrolamiento", "enrolamiento.instalador.descargado" in aud and "enrolamiento.appliance.reporte" in aud)
check("auditoría NO contiene el secreto del túnel", SECRETO not in aud)

# 7) Agentes: panel + vincular
ag = c.get("/clientes/prueba-srl/agentes").text
check("agentes: lista hosts de demo", "SRV-DC01" in ag)
r = c.post("/clientes/prueba-srl/agentes/vincular",
           data={"hostid": "501", "host_label": "SRV-DC01", "csrf": csrf_de(ag)})
check("vincular host → ok", r.status_code == 200 and "vinculado" in r.text.lower())

# 7b) Descubrimiento de red: alta de rango + credencial SNMP (demo → aplicado)
dsc = c.get("/clientes/prueba-srl/descubrimiento")
check("descubrimiento: página carga", "Descubrimiento de red" in dsc.text)
r = c.post("/clientes/prueba-srl/descubrimiento",
           data={"nombre": "LAN oficina", "ip_start": "10.20.30.1", "ip_end": "10.20.30.254",
                 "version": "2c", "community": "public", "csrf": csrf_de(dsc.text)})
page = c.get("/clientes/prueba-srl/descubrimiento").text
# Uso una IP que NO sea placeholder del form, así solo matchea la fila de la tabla.
check("descubrimiento: rango aplicado en demo", "10.20.30.254" in page and "aplicado" in page)
check("auditoria: registró descubrimiento.alta", "descubrimiento.alta" in c.get("/auditoria").text)
# validación: IP inválida no crea el rango
r = c.post("/clientes/prueba-srl/descubrimiento",
           data={"nombre": "rango-malo", "ip_start": "999.0.0.1", "ip_end": "999.0.0.2",
                 "version": "2c", "community": "public", "csrf": csrf_de(page)})
check("descubrimiento: IP inválida rechazada", "rango-malo" not in c.get("/clientes/prueba-srl/descubrimiento").text)
# validación: v3 sin usuario no crea el rango
r = c.post("/clientes/prueba-srl/descubrimiento",
           data={"nombre": "v3-sin-user", "ip_start": "10.0.0.1", "ip_end": "10.0.0.9",
                 "version": "3", "username": "", "csrf": csrf_de(page)})
check("descubrimiento: v3 sin usuario rechazado", "v3-sin-user" not in c.get("/clientes/prueba-srl/descubrimiento").text)
# eliminar el rango creado (id 1 en la base demo fresca)
r = c.post("/clientes/prueba-srl/descubrimiento/1/eliminar", data={"csrf": csrf_de(page)})
check("descubrimiento: eliminar quita el rango", "10.20.30.254" not in c.get("/clientes/prueba-srl/descubrimiento").text)

# 7c) Curación de criticidad (CMDB): la página carga y se fija una criticidad
cr = c.get("/clientes/prueba-srl/criticidad")
check("criticidad: página carga", "Criticidad (CMDB)" in cr.text)
r = c.post("/clientes/prueba-srl/criticidad",
           data={"itemtype": "Computer", "items_id": "501", "nombre": "SRV-DC01",
                 "criticidad": "critica", "csrf": csrf_de(cr.text)})
check("criticidad: se fija (vuelve a la página)", r.url.path.endswith("/criticidad"), r.url.path)
check("auditoria: registró criticidad.fijada", "criticidad.fijada" in c.get("/auditoria").text)
# criticidad inválida no rompe ni registra
r = c.post("/clientes/prueba-srl/criticidad",
           data={"itemtype": "Computer", "items_id": "501", "nombre": "X",
                 "criticidad": "inexistente", "csrf": csrf_de(cr.text)})
check("criticidad inválida se rechaza sin romper", r.url.path.endswith("/criticidad"))

# 8) Tickets: lista + alta
check("tickets lista demo", "No anda el correo" in c.get("/tickets").text)
html = c.get("/tickets/nuevo").text
check("tickets/nuevo ofrece el cliente", "Prueba SRL" in html)
r = c.post("/tickets/nuevo", data={"cliente": "prueba-srl", "titulo": "Caso de prueba",
                                   "contenido": "detalle", "prioridad": "4", "csrf": csrf_de(html)})
check("alta de ticket → detalle", r.url.path.startswith("/tickets/") and "Caso de prueba" in r.text, r.url.path)

# 9) Auditoría: cadena íntegra y con eventos
aud = c.get("/auditoria").text
check("auditoria: cadena íntegra", "íntegra" in aud)
check("auditoria: registró alta de cliente", "cliente.alta" in aud)
check("auditoria: registró vinculación", "agente.vinculado" in aud)
check("auditoria: registró alta de ticket", "ticket.creado" in aud)

# 9b) Reintentar aprovisionamiento (idempotente sobre un cliente ya activo)
det = c.get("/clientes/prueba-srl").text
r = c.post("/clientes/prueba-srl/reintentar", data={"csrf": csrf_de(det)})
check("reintentar deja el cliente OK", r.url.path == "/clientes/prueba-srl" and "aprovisionado" in r.text.lower())

# 9b-bis) Portal del cliente: aislamiento por tenant
# El operador crea un usuario de portal para prueba-srl.
det = c.get("/clientes/prueba-srl").text
r = c.post("/clientes/prueba-srl/portal-usuarios",
           data={"username": "cliente1", "password": "clave-portal-1", "csrf": csrf_de(det)})
check("operador crea usuario de portal", r.url.path == "/clientes/prueba-srl" and "cliente1" in r.text)

# El cliente entra en una sesión nueva: login + 2FA + cambio de clave inicial.
pc = TestClient(app, follow_redirects=True)
html = pc.get("/login").text
r = pc.post("/login", data={"username": "cliente1", "password": "clave-portal-1", "csrf": csrf_de(html)})
check("cliente: login → enrolar 2FA", r.url.path == "/login/2fa/enrolar", r.url.path)
m = re.search(r'class="codebox"><code>([^<]+)</code>', r.text)
csecret = m.group(1) if m else ""
r = pc.post("/login/2fa/enrolar", data={"code": pyotp.TOTP(csecret).now(), "csrf": csrf_de(r.text)})
if r.url.path == "/cambiar-clave":
    r = pc.post("/cambiar-clave", data={"actual": "", "nueva": "clave-nueva-portal",
                                        "repetir": "clave-nueva-portal", "csrf": csrf_de(r.text)})
check("cliente: entra al portal", r.url.path == "/portal", r.url.path)
check("cliente: ve su panel de tickets", pc.get("/portal/tickets").status_code == 200)
rq = pc.get("/portal/equipos")
check("cliente: ve sus equipos (GLPI + Mesh, ya no solo Zabbix)", rq.status_code == 200 and "PC-VENTAS-01" in rq.text and "En línea" in rq.text, rq.text[-400:])
# (el fixture de Zabbix en demo devuelve los mismos hosts a todos; el aislamiento real es por grupo y se prueba con GLPI)
check("cliente: no ve equipos de otras entidades de GLPI", "SRV-FILE01" not in rq.text and "PC-RECEPCION" not in rq.text and "SRV-APP" not in rq.text)
check("cliente: el inicio cuenta los equipos en línea", "Equipos en línea" in pc.get("/portal").text)
check("cliente: /clientes (operador) lo manda al portal", pc.get("/clientes").url.path == "/portal")
check("cliente: /equipos del operador lo manda al portal", pc.get("/equipos").url.path == "/portal")

# El cliente abre un ticket desde el portal (entidad forzada a la suya).
html = pc.get("/portal/tickets/nuevo").text
r = pc.post("/portal/tickets/nuevo", data={"titulo": "No tengo internet", "contenido": "desde hoy",
                                           "prioridad": "4", "csrf": csrf_de(html)})
check("cliente: abre un ticket", r.url.path.startswith("/portal/tickets/") and "No tengo internet" in r.text, r.url.path)
check("cliente: su ticket aparece en su lista", "No tengo internet" in pc.get("/portal/tickets").text)

# IDOR: no puede ver un ticket de OTRA entidad (demo 1042, entidad 2).
check("cliente: bloqueado de tickets de otra entidad (IDOR)", pc.get("/portal/tickets/1042").url.path == "/portal/tickets")

# El operador NO entra al portal.
check("operador: /portal lo manda a la consola", c.get("/portal").url.path == "/")

# 9c) Eliminar el registro del cliente (también borra sus usuarios de portal)
det = c.get("/clientes/prueba-srl").text
r = c.post("/clientes/prueba-srl/eliminar", data={"csrf": csrf_de(det)})
check("eliminar lleva a la lista", r.url.path == "/clientes", r.url.path)
check("eliminar quitó el cliente", "Prueba SRL" not in c.get("/clientes").text)

# ── ABM de usuarios de la plataforma + permisos por rol ─────────────────────
def _login_operador(usuario, clave):
    t = TestClient(app, follow_redirects=True)
    html_ = t.get("/login").text
    r_ = t.post("/login", data={"username": usuario, "password": clave, "csrf": csrf_de(html_)})
    m_ = re.search(r'class="codebox"><code>([^<]+)</code>', r_.text)
    sec = m_.group(1) if m_ else ""
    r_ = t.post("/login/2fa/enrolar", data={"code": pyotp.TOTP(sec).now(), "csrf": csrf_de(r_.text)})
    if r_.url.path == "/cambiar-clave":
        r_ = t.post("/cambiar-clave", data={"actual": "", "nueva": "Clave-Propia-123", "repetir": "Clave-Propia-123",
                                            "csrf": csrf_de(r_.text)})
    return t, r_


pag = c.get("/usuarios")
check("usuarios: el administrador ve el ABM", pag.status_code == 200 and "Usuarios de la plataforma" in pag.text)
r = c.post("/usuarios", data={"username": "Mal Usuario", "email": "x@y.com", "nombre": "", "rol": "tecnico", "csrf": csrf_de(pag.text)})
check("usuarios: nombre inválido rechazado", "Usuario inválido" in r.text and "Clave:" not in r.text)
r = c.post("/usuarios", data={"username": "jperez", "email": "jperez@pymesenlinea.com.ar", "nombre": "Juan Pérez",
                              "rol": "tecnico", "csrf": csrf_de(c.get("/usuarios").text)})
mclave = re.search(r"Clave:\s+([^\s<]+)", r.text)
check("usuarios: alta de técnico muestra usuario y clave UNA vez", "Usuario creado" in r.text and bool(mclave), r.text[-300:])
clave_jp = mclave.group(1) if mclave else ""
check("usuarios: la clave generada es fuerte", len(clave_jp) >= 16 and re.search(r"[a-z]", clave_jp) and re.search(r"[A-Z]", clave_jp)
      and re.search(r"\d", clave_jp))
check("usuarios: en GLPI, Zabbix y Mesh queda creado (demo)", r.text.count('title="ok"') >= 3, r.text[-600:])
lst = c.get("/usuarios").text
check("usuarios: la clave NO se vuelve a mostrar", clave_jp not in lst and "Clave:" not in lst)
check("usuarios: aparece en la lista con su rol", "jperez" in lst and "Técnico" in lst)
check("auditoría del alta no guarda la clave", clave_jp not in c.get("/auditoria").text and "usuario.creado" in c.get("/auditoria").text)
r = c.post("/usuarios", data={"username": "jperez", "email": "otro@y.com", "nombre": "", "rol": "pm", "csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: usuario repetido rechazado", "Ya existe" in r.text)

tj, rj = _login_operador("jperez", clave_jp)
check("técnico: entra a la consola con la clave generada (pide cambiarla)", rj.url.path == "/", rj.url.path)
check("técnico: ve equipos y tickets", tj.get("/equipos").status_code == 200 and tj.get("/tickets").status_code == 200)
check("técnico: NO ve el ABM de usuarios", tj.get("/usuarios").url.path == "/" and "Usuarios de la plataforma" not in tj.get("/usuarios").text)
check("técnico: NO ve auditoría ni instaladores", tj.get("/auditoria").url.path == "/" and tj.get("/instaladores").status_code == 404)
tj_menu = tj.get("/").text
check("técnico: el menú no muestra Usuarios ni Auditoría", "/usuarios" not in tj_menu and "/auditoria" not in tj_menu and "/equipos" in tj_menu)
check("técnico: no puede crear clientes", tj.post("/clientes/nuevo", data={"nombre": "X", "notas": "", "csrf": csrf_de(tj.get("/").text)}).url.path == "/")

pp = c.post("/usuarios", data={"username": "mgomez", "email": "mgomez@pymesenlinea.com.ar", "nombre": "María Gómez",
                               "rol": "pm", "csrf": csrf_de(c.get("/usuarios").text)})
clave_pm = re.search(r"Clave:\s+([^\s<]+)", pp.text).group(1)
tp, _ = _login_operador("mgomez", clave_pm)
check("PM: ve auditoría y clientes pero NO el ABM de usuarios",
      "Auditoría" in tp.get("/auditoria").text and tp.get("/clientes").status_code == 200 and tp.get("/usuarios").url.path == "/")

# Correo de bienvenida: credenciales + responsabilidades + manual del rol
from itier_console.services import correo as _correo_svc  # noqa: E402

_enviados = []
_cfg_ok, _env_ok = _correo_svc.configurado, _correo_svc.enviar
_correo_svc.configurado = lambda cfg: True
_correo_svc.enviar = lambda cfg, dest, asunto, cuerpo, html=None: _enviados.append((dest, asunto, cuerpo, html))
pag = c.get("/usuarios")
check("usuarios: con SMTP configurado ofrece enviar el correo", 'name="enviar"' in pag.text and "disabled" not in pag.text.split('name="enviar"')[1][:60])
r = c.post("/usuarios", data={"username": "ltec", "email": "ltec@pymesenlinea.com.ar", "nombre": "<b>Lucía</b> Técnica",
                              "rol": "tecnico", "enviar": "1", "csrf": csrf_de(pag.text)})
clave_lt = re.search(r"Clave:\s+([^\s<]+)", r.text).group(1)
dest, asunto, cuerpo, html = _enviados[-1]
check("correo: va al correo del formulario", dest == "ltec@pymesenlinea.com.ar" and "Lucía" in cuerpo)
check("correo: informa el alta, el rol y las credenciales", "Técnico" in asunto and "Usuario: ltec" in cuerpo and f"Clave temporal: {clave_lt}" in cuerpo
      and clave_lt in html)
check("correo: explica la misión y las responsabilidades del técnico", "TUS RESPONSABILIDADES" in cuerpo and "tickets que se te asignan" in cuerpo
      and "Conectarte de forma remota solo con un motivo" in cuerpo)
check("correo: trae el manual de operaciones del rol (revisar alertas, conexión remota)",
      "MANUAL DE OPERACIONES" in cuerpo and "REVISAR EQUIPOS Y ALERTAS" in cuerpo and "CONEXIÓN REMOTA A UN EQUIPO" in cuerpo)
check("correo: el técnico NO recibe procedimientos de PM ni de administrador",
      "INSTALAR AGENTES" not in cuerpo and "ADMINISTRAR USUARIOS" not in cuerpo and "DAR DE ALTA UN CLIENTE" not in cuerpo)
check("correo: pide cambiar la clave y activar 2FA, y borrar el correo", "2FA" in cuerpo and "borrá este correo" in cuerpo)
check("correo: el HTML escapa lo que escribió el administrador", "<b>Lucía</b>" not in html and "&lt;b&gt;" in html)
check("correo: la card del alta informa el envío", "enviado a ltec@pymesenlinea.com.ar" in r.text or "ok*" in r.text or 'title="ok (enviado' in r.text)
check("correo: la clave NO queda en la auditoría", clave_lt not in c.get("/auditoria").text)
r = c.post("/usuarios", data={"username": "pmgr", "email": "pmgr@pymesenlinea.com.ar", "nombre": "Paula Mgr", "rol": "pm",
                              "enviar": "1", "csrf": csrf_de(c.get("/usuarios").text)})
cuerpo_pm = _enviados[-1][2]
check("correo PM: incluye alta de clientes e instaladores pero no el ABM de usuarios",
      "DAR DE ALTA UN CLIENTE" in cuerpo_pm and "INSTALAR AGENTES" in cuerpo_pm and "ADMINISTRAR USUARIOS" not in cuerpo_pm)
r = c.post("/usuarios", data={"username": "adm2", "email": "adm2@pymesenlinea.com.ar", "nombre": "Admin Dos", "rol": "admin",
                              "enviar": "1", "csrf": csrf_de(c.get("/usuarios").text)})
check("correo administrador: incluye el ABM de usuarios y la baja de clientes",
      "ADMINISTRAR USUARIOS" in _enviados[-1][2] and "DAR DE BAJA UN CLIENTE" in _enviados[-1][2])
n0 = len(_enviados)
c.post("/usuarios", data={"username": "sincorreo", "email": "sc@pymesenlinea.com.ar", "nombre": "", "rol": "tecnico",
                          "csrf": csrf_de(c.get("/usuarios").text)})
check("correo: si no se tilda «enviar» no se manda nada", len(_enviados) == n0)
_correo_svc.enviar = lambda *a, **k: (_ for _ in ()).throw(__import__("itier_console.services", fromlist=["ServiceError"]).ServiceError("SMTP caído"))
r = c.post("/usuarios", data={"username": "fallo", "email": "fa@pymesenlinea.com.ar", "nombre": "", "rol": "tecnico",
                              "enviar": "1", "csrf": csrf_de(c.get("/usuarios").text)})
check("correo: si el SMTP falla el alta igual se crea y la clave se muestra", "Clave:" in r.text and "SMTP caído" in r.text)
uid_lt = int(re.search(r'/usuarios/(\d+)/rol', c.get("/usuarios").text.split("<strong>ltec</strong>")[1]).group(1))
_correo_svc.enviar = lambda cfg, dest, asunto, cuerpo, html=None: _enviados.append((dest, asunto, cuerpo, html))
c.post(f"/usuarios/{uid_lt}/clave", data={"csrf": csrf_de(c.get("/usuarios").text)})
check("correo: al reiniciar la clave se mandan las credenciales nuevas (sin repetir el manual)",
      _enviados[-1][1] == "iTier: tu clave fue reiniciada" and "Clave temporal:" in _enviados[-1][2] and "MANUAL DE OPERACIONES" not in _enviados[-1][2])
_correo_svc.configurado, _correo_svc.enviar = _cfg_ok, _env_ok
# (se limpian los usuarios de prueba que sumarían administradores y falsearían las pruebas de protección)
for _n in ("adm2", "pmgr", "sincorreo", "fallo"):
    _m = re.search(r'/usuarios/(\d+)/rol', c.get("/usuarios").text.split(f"<strong>{_n}</strong>")[1]) if f"<strong>{_n}</strong>" in c.get("/usuarios").text else None
    if _m:
        c.post(f"/usuarios/{_m.group(1)}/eliminar", data={"csrf": csrf_de(c.get("/usuarios").text)})

# Manual en la consola
tt, _ = _login_operador("ltec", clave_lt) if False else (tj, None)
man = tj.get("/manual")
check("manual: cada operador lee el de su rol", man.status_code == 200 and "Manual de operaciones" in man.text)
check("manual: el administrador puede ver el de otro rol", "Conexión remota a un equipo" in c.get("/manual?rol=tecnico").text
      and "Administrar usuarios y roles" in c.get("/manual?rol=admin").text and "Administrar usuarios y roles" not in c.get("/manual?rol=tecnico").text)
check("manual: un técnico no puede pedir el manual de otro rol", "Administrar usuarios y roles" not in tj.get("/manual?rol=admin").text)
check("clientes: solo el administrador puede eliminar clientes", tp.post("/clientes/prueba-srl/eliminar", data={"csrf": csrf_de(tp.get("/").text)}).url.path == "/")

# cambio de rol, reinicio de clave, baja/alta y protecciones
uid_jp = int(re.search(r'/usuarios/(\d+)/rol', c.get("/usuarios").text.split("<strong>jperez</strong>")[1]).group(1))
r = c.post(f"/usuarios/{uid_jp}/rol", data={"rol": "pm", "csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: cambio de rol", "Rol actualizado" in r.text)
check("usuarios: el técnico ascendido a PM ya ve auditoría", "Auditoría" in tj.get("/auditoria").text)
r = c.post(f"/usuarios/{uid_jp}/clave", data={"csrf": csrf_de(c.get("/usuarios").text)})
clave_nueva = re.search(r"Clave:\s+([^\s<]+)", r.text).group(1)
check("usuarios: reiniciar clave genera otra y la muestra una vez", "Clave reiniciada" in r.text and clave_nueva != clave_jp)
r = c.post(f"/usuarios/{uid_jp}/baja", data={"csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: baja en todos los sistemas", "dado de baja" in r.text)
tbaja = TestClient(app, follow_redirects=True)
rb = tbaja.post("/login", data={"username": "jperez", "password": clave_nueva, "csrf": csrf_de(tbaja.get("/login").text)})
check("usuarios: un usuario dado de baja no puede entrar", rb.url.path == "/login", rb.url.path)
r = c.post(f"/usuarios/{uid_jp}/alta", data={"csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: reactivación", "Usuario activado" in r.text)
uid_admin = int(re.search(r'/usuarios/(\d+)/rol', c.get("/usuarios").text.split("<strong>admin</strong>")[1]).group(1))
r = c.post(f"/usuarios/{uid_admin}/rol", data={"rol": "tecnico", "csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: no se puede dejar la plataforma sin administrador", "al menos un administrador" in r.text)
r = c.post(f"/usuarios/{uid_admin}/eliminar", data={"csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: nadie puede eliminarse a sí mismo", "propio usuario" in r.text)
r = c.post(f"/usuarios/{uid_jp}/eliminar", data={"csrf": csrf_de(c.get("/usuarios").text)})
check("usuarios: eliminar", "Usuario eliminado" in r.text and "<strong>jperez</strong>" not in c.get("/usuarios").text)
check("usuarios: CSRF inválido no crea", "zzz" not in c.post("/usuarios", data={"username": "zzz", "email": "z@z.com", "rol": "tecnico", "csrf": "mal"}).text)

# Rendimiento: estáticos cacheables con huella, Server-Timing y barra de progreso
_pg = c.get("/equipos")
_m = re.search(r'/static/app\.css\?v=([0-9a-f]+)', _pg.text)
check("rendimiento: las páginas apuntan a estáticos con huella (?v=)", bool(_m) and "/static/app.js?v=" in _pg.text)
_css = c.get(f"/static/app.css?v={_m.group(1)}") if _m else None
check("rendimiento: el CSS versionado se guarda un año (inmutable)", _css is not None and "immutable" in _css.headers.get("cache-control", "")
      and "max-age=31536000" in _css.headers.get("cache-control", ""))
check("rendimiento: sin versión se revalida", c.get("/static/app.css").headers.get("cache-control") == "no-cache")
check("rendimiento: cada pantalla informa Server-Timing", "app;dur=" in _pg.headers.get("server-timing", ""), _pg.headers.get("server-timing"))
check("rendimiento: hay barra de progreso de navegación", "nav-bar" in c.get(f"/static/app.js?v={_m.group(1)}").text)

# 10) Logout

# Técnico de prueba vigente para los chequeos de permisos de las secciones nuevas
_rt = c.post("/usuarios", data={"username": "tecrbac", "email": "tecrbac@pymesenlinea.com.ar", "nombre": "Tec RBAC", "rol": "tecnico", "csrf": csrf_de(c.get("/usuarios").text)})
ttec, _ = _login_operador("tecrbac", re.search(r"Clave:\s+([^\s<]+)", _rt.text).group(1))
check("RBAC: técnico de prueba operativo", ttec.get("/equipos").status_code == 200)
_rp = c.post("/usuarios", data={"username": "pmrbac", "email": "pmrbac@pymesenlinea.com.ar", "nombre": "PM RBAC", "rol": "pm", "csrf": csrf_de(c.get("/usuarios").text)})
tpm, _ = _login_operador("pmrbac", re.search(r"Clave:\s+([^\s<]+)", _rp.text).group(1))
check("RBAC: PM de prueba operativo y ve la Bandeja", tpm.get("/bandeja").url.path == "/bandeja")
# ── Canales de entrada: WhatsApp (OpenWA) y correo ──────────────────────────────
from itier_console.services import canales as _cn  # noqa: E402
from itier_console import db as _db  # noqa: E402

check("canales: normaliza números argentinos", _cn.normalizar_whatsapp("11 5555-1234") == "5491155551234"
      and _cn.normalizar_whatsapp("+54 9 11 5555 1234") == "5491155551234"
      and _cn.normalizar_whatsapp("5491155551234@c.us") == "5491155551234" and _cn.normalizar_whatsapp("abc") is None)
c.post("/clientes/nuevo", data={"nombre": "Canal SA", "notas": "", "csrf": csrf_de(c.get("/clientes/nuevo").text)})
det = c.get("/clientes/canal-sa").text
check("canales: la ficha del cliente muestra Canales de contacto", "Canales de contacto" in det and "soporte@pymesenlinea.com.ar" in det)
csrf_c = lambda: csrf_de(c.get("/clientes/canal-sa").text)  # noqa: E731
r = c.post("/clientes/canal-sa/dominios", data={"dominio": "gmail.com", "csrf": csrf_c()})
check("canales: no se puede asociar un dominio público", "correo público" in r.text and "@gmail.com" not in r.text.split("Dominios de correo")[1].split("Personas")[0])
r = c.post("/clientes/canal-sa/dominios", data={"dominio": "@PruebaSRL.com.ar", "csrf": csrf_c()})
check("canales: dominio del cliente asociado", "@pruebasrl.com.ar" in r.text)
r = c.post("/clientes/canal-sa/contactos", data={"nombre": "Ana", "email": "", "whatsapp": "11 5555 1234", "csrf": csrf_c()})
check("canales: contacto de WhatsApp cargado", "Ana" in r.text and "5491155551234" in r.text)
r = c.post("/clientes/canal-sa/contactos", data={"nombre": "Otra", "email": "", "whatsapp": "1155551234", "csrf": csrf_c()})
check("canales: no se repite un número", "ya está cargado" in r.text)

def _wa(texto, de="5491155551234@c.us", mid="m1", **extra):
    m = {"id": mid, "from": de, "body": texto, "type": "chat", "isGroupMsg": False, "fromMe": False, "notifyName": "Ana"}
    m.update(extra)
    return {"event": "onMessage", "data": m}

hook = "/hooks/whatsapp/tok-wa-secreto-123"
anon = TestClient(app, follow_redirects=True)
check("webhook: token incorrecto → 404", anon.post("/hooks/whatsapp/otro", json=_wa("hola")).status_code == 404)
r = anon.post(hook, json=_wa("No me anda la impresora de recepción"))
check("webhook: número conocido → ticket (sin login)", r.status_code == 200 and r.json()["estado"] == "ticket", r.text)
_conn = _db.connect(os.environ["ITIER_CONSOLE_DB"])
t1 = _conn.execute("SELECT ticket_id FROM entrantes WHERE canal='whatsapp' AND msg_id='m1'").fetchone()["ticket_id"]
check("webhook: el ticket queda con origen WhatsApp", _conn.execute("SELECT canal FROM ticket_origen WHERE ticket_id=?", (t1,)).fetchone()["canal"] == "whatsapp")
r = anon.post(hook, json=_wa("Es la del primer piso", mid="m2"))
check("webhook: el segundo mensaje se suma al mismo ticket", r.json()["estado"] == "seguimiento"
      and _conn.execute("SELECT ticket_id FROM entrantes WHERE msg_id='m2'").fetchone()["ticket_id"] == t1)
check("webhook: reenvío del mismo mensaje no duplica", anon.post(hook, json=_wa("Es la del primer piso", mid="m2")).json()["estado"] == "duplicado")
for nombre, payload in (("grupo", _wa("hola", de="123-456@g.us", mid="g1", isGroupMsg=True)),
                        ("propio", _wa("hola", mid="g2", fromMe=True)), ("estado", _wa("hola", de="status@broadcast", mid="g3")),
                        ("imagen", _wa("x", mid="g4", type="image")), ("vacío", _wa("  ", mid="g5"))):
    check(f"webhook: ignora mensajes de tipo {nombre}", anon.post(hook, json=payload).json()["estado"] == "ignorado")
r = anon.post(hook, json=_wa("Hola, necesito ayuda", de="5491122223333@c.us", mid="d1"))
check("webhook: número desconocido → Bandeja", r.json()["estado"] == "sin_cliente")
_v5 = {"webhookId": "w", "sessionId": "itier-soporte", "event": "message.received", "timestamp": 1,
       "payload": {"message": {"id": "v5-1", "from": "5491155551234@c.us", "body": "Consulta por el formato v5"}}}
check("webhook: el sobre de OpenWA v5 (payload.message) se entiende", anon.post(hook, json=_v5).json()["estado"] in ("ticket", "seguimiento"))
check("webhook: otros eventos de v5 se ignoran", anon.post(hook, json={"event": "session.state.changed", "payload": {"state": "READY"}}).json()["estado"] == "ignorado")
# WhatsApp con ID interno (@lid): sin número visible
check("lid: se normaliza como ID interno", _cn.normalizar_whatsapp("168199593165032@lid") == "lid:168199593165032"
      and _cn.normalizar_whatsapp("lid:168199593165032") == "lid:168199593165032")
_lid = {"event": "message.received", "payload": {"message": {"id": "lid-1", "from": "168199593165032@lid", "body": "Q paso pa?", "to": "5492615708516@c.us", "sender": {"pushname": "Bruno"}}}}
r = anon.post(hook, json=_lid)
check("lid: sin número real va a la Bandeja con su ID interno (nunca toma el `to` de soporte)", r.json()["estado"] == "sin_cliente"
      and _conn.execute("SELECT remitente FROM entrantes WHERE msg_id='lid-1'").fetchone()["remitente"] == "lid:168199593165032")
check("lid: la Bandeja lo muestra como ID interno", "ID interno de WhatsApp 168199593165032" in c.get("/bandeja").text)
_e_lid = _conn.execute("SELECT id FROM entrantes WHERE msg_id='lid-1'").fetchone()["id"]
c.post(f"/bandeja/{_e_lid}/asignar", data={"cliente": "canal-sa", "recordar": "1", "csrf": csrf_de(c.get("/bandeja").text)})
_lid["payload"]["message"]["id"] = "lid-2"
check("lid: tras «Recordar», el siguiente mensaje ya abre ticket solo", anon.post(hook, json=_lid).json()["estado"] in ("ticket", "seguimiento"))
check("lid: la ficha del cliente lo muestra como ID interno", "ID interno 168199593165032" in c.get("/clientes/canal-sa").text)
_lid2 = {"event": "message.received", "payload": {"message": {"id": "lid-3", "from": "999888777666555@lid", "body": "hola", "sender": {"id": "5491155551234@c.us"}}}}
check("lid: si el mensaje trae el teléfono real, se usa ese", _cn.parsear_whatsapp(_lid2)["remitente"] == "5491155551234")
check("webhook: JSON inválido → 400", anon.post(hook, content=b"no es json").status_code == 400)

band = c.get("/bandeja")
check("bandeja: el PM/admin ve el mensaje sin cliente", band.status_code == 200 and "Hola, necesito ayuda" in band.text and "5491122223333" in band.text)
check("bandeja: el tablero avisa mensajes pendientes", "sin atender en la Bandeja" in c.get("/").text)
eid = _conn.execute("SELECT id FROM entrantes WHERE msg_id='d1'").fetchone()["id"]
r = c.post(f"/bandeja/{eid}/asignar", data={"cliente": "canal-sa", "recordar": "1", "csrf": csrf_de(band.text)})
check("bandeja: asignar a un cliente crea el ticket", re.fullmatch(r"/tickets/\d+", r.url.path) is not None, r.url.path)
check("bandeja: «recordar» deja cargado el contacto", _conn.execute("SELECT 1 FROM client_contactos WHERE whatsapp='5491122223333'").fetchone() is not None)
check("bandeja: ya no está pendiente", "Hola, necesito ayuda" not in c.get("/bandeja").text.split("Últimos mensajes")[0])
r = c.post(f"/bandeja/{eid}/asignar", data={"cliente": "canal-sa", "csrf": csrf_de(c.get("/bandeja").text)})
check("bandeja: no se asigna dos veces", "ya fue atendido" in r.text)

# Correo
def _mail(de, asunto, cuerpo, mid, extra=""):
    return (f"From: {de}\r\nTo: soporte@pymesenlinea.com.ar\r\nSubject: {asunto}\r\nMessage-ID: <{mid}@x>\r\n{extra}"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{cuerpo}\r\n").encode()
cfg_ = app.state.cfg
r = _cn.procesar_email(cfg_, _conn, _mail("Luis Gómez <luis@pruebasrl.com.ar>", "Sin internet en caja", "Se cayó la red.\n\nEl lun escribió:\n> viejo", "e1"))
check("correo: el dominio registrado asocia el ticket al cliente", r["estado"] == "ticket", str(r))
cid_ = _conn.execute("SELECT id FROM clients WHERE slug='canal-sa'").fetchone()["id"]
check("correo: queda en el cliente correcto y sin la cita", _conn.execute("SELECT client_id, cuerpo FROM entrantes WHERE msg_id='<e1@x>'").fetchone()["client_id"] == cid_
      and "viejo" not in _conn.execute("SELECT cuerpo FROM entrantes WHERE msg_id='<e1@x>'").fetchone()["cuerpo"])
t2 = r["ticket_id"]
r = _cn.procesar_email(cfg_, _conn, _mail("luis@pruebasrl.com.ar", f"RE: [#{t2}] Sin internet en caja", "Ya volvió, gracias", "e2"))
check("correo: la respuesta con [#id] se suma al ticket", r["estado"] == "seguimiento" and r["ticket_id"] == t2, str(r))
r = _cn.procesar_email(cfg_, _conn, _mail("alguien@otraempresa.com", f"[#{t2}] metido", "quiero colarme en el ticket", "e3"))
check("correo: un desconocido no puede escribir en un ticket ajeno", r["estado"] == "sin_cliente")
r = _cn.procesar_email(cfg_, _conn, _mail("Pepe <pepe@desconocida.com.ar>", "Consulta", "¿Cuánto sale?", "e4"))
check("correo: dominio no registrado → Bandeja", r["estado"] == "sin_cliente")
check("correo: autorrespuestas y noreply se ignoran",
      _cn.procesar_email(cfg_, _conn, _mail("luis@pruebasrl.com.ar", "Fuera de oficina", "estoy de vacaciones", "e5", "Auto-Submitted: auto-replied\r\n"))["estado"] == "ignorado"
      and _cn.procesar_email(cfg_, _conn, _mail("noreply@pruebasrl.com.ar", "Aviso", "x", "e6"))["estado"] == "ignorado")
r = _cn.procesar_email(cfg_, _conn, _mail("fake@pruebasrl.com.ar", "Urgente: transferí", "pasame los datos", "e7",
                                        "Authentication-Results: mx.x; dkim=fail; spf=fail\r\n"))
check("correo: SPF/DKIM fallido → a revisar, sin ticket", r["estado"] == "revisar" and r["ticket_id"] is None)
check("correo: mismo Message-ID no se procesa dos veces", _cn.procesar_email(cfg_, _conn, _mail("luis@pruebasrl.com.ar", "Sin internet en caja", "Se cayó la red.", "e1"))["estado"] == "duplicado")
html_mail = ("From: luis@pruebasrl.com.ar\r\nSubject: Impresora\r\nMessage-ID: <e8@x>\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
             "<html><style>p{}</style><body><p>No <b>imprime</b></p><p>Gracias</p></body></html>").encode()
check("correo: HTML se convierte a texto", "No imprime" in _cn.parsear_email(html_mail)["cuerpo"] and "<" not in _cn.parsear_email(html_mail)["cuerpo"])

# Lectura del buzón IMAP (simulado)
class _FakeImap:
    def __init__(self, mails): self.mails, self.vistos = mails, []
    def select(self, f): return "OK", [b"1"]
    def search(self, *a): return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.mails)))]
    def fetch(self, n, q): return "OK", [(b"1 (BODY[] {1})", self.mails[int(n) - 1])]
    def store(self, n, *a): self.vistos.append(int(n))
    def logout(self): pass
_fi = _FakeImap([_mail("luis@pruebasrl.com.ar", "Pedido por IMAP", "Necesito un mouse", "i1")])
n_ = _cn.sondear_buzon(cfg_, conectar=lambda _c: _fi)
check("imap: lee, procesa y marca leído", n_ == 1 and _fi.vistos == [1]
      and _conn.execute("SELECT estado FROM entrantes WHERE msg_id='<i1@x>'").fetchone()["estado"] == "ticket")

# Anti-inundación
estados = [anon.post(hook, json=_wa(f"spam {i}", de="5491100000000@c.us", mid=f"f{i}")).json()["estado"] for i in range(25)]
check("anti-inundación: pasado el tope por hora se ignoran los mensajes", estados[-1] == "ignorado" and "sin_cliente" in estados)

# Tomar caso y asignación
tkt = c.get(f"/tickets/{t1}")
check("ticket: muestra el canal de origen", "WhatsApp" in tkt.text and "5491155551234" in tkt.text)
check("tickets: la lista muestra la vía", "💬 WhatsApp" in c.get("/tickets").text and "✉️ Correo" in c.get("/tickets").text)
r = c.post(f"/tickets/{t1}/tomar", data={"csrf": csrf_de(tkt.text)})
check("ticket: «tomar caso» responde (con o sin cuenta GLPI vinculada)", r.status_code == 200 and r.url.path == f"/tickets/{t1}")

# Pantalla de configuración de canales (administrador)
pg = c.get("/canales")
check("canales: el administrador ve la configuración de correo y WhatsApp", pg.status_code == 200 and "Correo de soporte" in pg.text and "WhatsApp de soporte" in pg.text)
check("canales: WhatsApp informa estado y QR (demo)", c.get("/canales/whatsapp/estado").json()["estado"] == "esperando_qr" and c.get("/canales/whatsapp/estado").json()["qr"].startswith("data:image"))
r = c.post("/canales/correo", data={"soporte_email": "soporte@pymesenlinea.com.ar", "imap_host": "imap.ejemplo.com", "imap_port": "993",
          "imap_user": "soporte@pymesenlinea.com.ar", "imap_password": "S3cr3to-imap!", "imap_folder": "INBOX", "imap_tls": "ssl",
          "imap_poll_s": "45", "csrf": csrf_de(pg.text)})
check("canales: se guarda la cuenta IMAP", "Configuración del correo guardada" in r.text and "imap.ejemplo.com" in r.text)
check("canales: la contraseña no se vuelve a mostrar", "S3cr3to-imap!" not in r.text and "guardada: dejá vacío" in r.text)
_raw = _conn.execute("SELECT valor, secreto FROM ajustes WHERE clave='imap_password'").fetchone()
check("canales: la contraseña queda cifrada en la base", _raw["secreto"] == 1 and "S3cr3to-imap!" not in _raw["valor"])
from itier_console.services import ajustes as _aj  # noqa: E402
_ef = _aj.efectiva(_conn, app.state.cfg)
check("canales: la config guardada pisa al entorno", _ef.imap_configurado and _ef.imap_host == "imap.ejemplo.com" and _ef.imap_poll_s == 45 and _ef.imap_password == "S3cr3to-imap!")
r = c.post("/canales/correo", data={"soporte_email": "soporte@pymesenlinea.com.ar", "imap_host": "imap.otro.com", "imap_port": "993",
          "imap_user": "soporte@pymesenlinea.com.ar", "imap_password": "", "imap_folder": "INBOX", "imap_tls": "ssl",
          "imap_poll_s": "60", "csrf": csrf_de(c.get("/canales").text)})
check("canales: contraseña vacía conserva la guardada", _aj.efectiva(_conn, app.state.cfg).imap_password == "S3cr3to-imap!" and _aj.efectiva(_conn, app.state.cfg).imap_host == "imap.otro.com")
r = c.post("/canales/correo", data={"soporte_email": "no-es-correo", "imap_host": "x", "imap_port": "993", "imap_user": "u", "imap_tls": "ssl",
          "imap_poll_s": "60", "csrf": csrf_de(c.get("/canales").text)})
check("canales: valida la casilla de soporte", "no es un correo válido" in r.text)
check("canales: la auditoría registra el cambio sin la clave", "canal.correo_configurado" in c.get("/auditoria").text and "S3cr3to-imap!" not in c.get("/auditoria").text)
# probar_imap con servidor simulado
import imaplib as _imaplib  # noqa: E402
class _I:
    def __init__(s, *a, **k): pass
    def login(s, u, p):
        if p != "S3cr3to-imap!": raise _imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    def select(s, f, readonly=False): return "OK", [b"3"]
    def search(s, *a): return "OK", [b"1 2"]
    def logout(s): pass
_real_ssl = _imaplib.IMAP4_SSL; _imaplib.IMAP4_SSL = _I
try:
    check("canales: probar conexión OK informa los sin leer", "2 correo(s) sin leer" in _cn.probar_imap(_aj.efectiva(_conn, app.state.cfg)))
    _mala = _aj.efectiva(_conn, app.state.cfg).__class__(**{**_aj.efectiva(_conn, app.state.cfg).__dict__, "imap_password": "otra"})
    try:
        _cn.probar_imap(_mala); _ok = False
    except Exception as _e:  # noqa: BLE001
        _ok = "rechazó" in str(getattr(_e, "mensaje", _e))
    check("canales: probar conexión con clave mala da un error claro", _ok)
finally:
    _imaplib.IMAP4_SSL = _real_ssl
r = c.post("/canales/whatsapp", data={"wa_api_url": "http://openwa:8080", "wa_api_key": "k-wa-123", "wa_numero": "5491155550000", "csrf": csrf_de(c.get("/canales").text)})
check("canales: se guarda la config de WhatsApp (clave oculta)", "5491155550000" in r.text and "k-wa-123" not in r.text)
check("canales: URL inválida rechazada", "debe empezar con http" in c.post("/canales/whatsapp", data={"wa_api_url": "ftp://x", "wa_numero": "", "csrf": csrf_de(c.get("/canales").text)}).text)
check("RBAC: PM y técnico no entran a Canales", tpm.get("/canales").url.path != "/canales" and ttec.get("/canales/whatsapp/estado").url.path != "/canales/whatsapp/estado")
check("RBAC: el menú del administrador muestra Canales", 'href="/canales"' in c.get("/").text)

# Reporte de horas por cliente
from itier_console.services import horas as _hs  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
_conn.execute("UPDATE users SET glpi_user_id='77' WHERE username='admin'"); _conn.commit()
_tk = c.get(f"/tickets/{t1}")
check("horas: el ticket ofrece registrar tiempo", "Registrar tiempo" in _tk.text)
r = c.post(f"/tickets/{t1}/tiempo", data={"minutos": "90", "nota": "Cambio de toner", "csrf": csrf_de(_tk.text)})
check("horas: se registra el tiempo de un ticket", "Registraste 90 min" in r.text and "Cambio de toner" in r.text, r.text[-300:])
c.post(f"/tickets/{t1}/tiempo", data={"minutos": "30", "nota": "", "csrf": csrf_de(c.get(f"/tickets/{t1}").text)})
check("horas: el ticket muestra el total (2 h)", "Total: <b>2.0 h</b>" in c.get(f"/tickets/{t1}").text)
check("horas: minutos fuera de rango rechazados", "entre 1 y 1440" in c.post(f"/tickets/{t1}/tiempo", data={"minutos": "5000", "nota": "", "csrf": csrf_de(c.get(f"/tickets/{t1}").text)}).text)
rp = c.get("/reportes/horas")
check("horas: la pantalla del reporte carga", rp.status_code == 200 and "Horas y rentabilidad por cliente" in rp.text and "Canal SA" in rp.text)
check("horas: avisa que falta la cotización y la tarifa", "Falta la cotización del dólar" in rp.text and "Sin tarifa por hora" in rp.text)
c.post("/reportes/parametros", data={"fx_ars_usd": "1.400", "costo_plataforma_usd": "60", "mes": "", "csrf": csrf_de(rp.text)})
check("horas: la cotización «1.400» se lee como 1400 (formato argentino)", _aj.efectiva(_conn, app.state.cfg).fx_ars_usd == 1400.0)
check("horas: números a la argentina", _hs.numero_ar("257.000,50") == 257000.5 and _hs.numero_ar("1455,5") == 1455.5 and _hs.numero_ar("$ 60") == 60.0)
c.post("/reportes/parametros", data={"fx_ars_usd": "1400", "costo_plataforma_usd": "60", "mes": "", "csrf": csrf_de(c.get("/reportes/horas").text)})
check("horas: se guardan cotización y costo de plataforma", _aj.efectiva(_conn, app.state.cfg).fx_ars_usd == 1400.0 and _aj.efectiva(_conn, app.state.cfg).costo_plataforma_usd == 60.0)
c.post("/reportes/tarifas", data={"t_admin": "35", "mes": "", "csrf": csrf_de(c.get("/reportes/horas").text)})
c.post("/reportes/facturacion", data={"f_canal-sa": "257.000", "mes": "", "csrf": csrf_de(c.get("/reportes/horas").text)})
check("horas: la facturación 257.000 se lee como 257000", _conn.execute("SELECT facturacion_ars FROM clients WHERE slug='canal-sa'").fetchone()[0] == 257000.0)
rp = c.get("/reportes/horas")
_ing = 257000 / 1400
check("horas: el reporte suma 2.0 h al cliente y muestra su facturación en USD", "2.0 h" in rp.text and f"US$ {_ing:.0f}" in rp.text, rp.text[rp.text.find("Por cliente"):][:900])
_rep = _hs.reporte(_conn, _aj.efectiva(_conn, app.state.cfg), _dt.now().strftime("%Y-%m"))
_fc = next(f for f in _rep["filas"] if f["slug"] == "canal-sa")
check("horas: costo = horas × tarifa + parte de la plataforma; margen = ingreso − costo",
      abs(_fc["costo_horas"] - 70.0) < 0.01 and abs(_fc["margen"] - (_ing - _fc["costo"])) < 0.01 and _fc["plataforma"] > 0, str(_fc))
check("horas: otro mes no suma las horas de éste", next(f for f in _hs.reporte(_conn, _aj.efectiva(_conn, app.state.cfg), "2020-01")["filas"] if f["slug"] == "canal-sa")["horas"] == 0)
check("horas: el costo de plataforma se mide en USD", "Costo mensual de la plataforma" in rp.text)
_real_bcra = _hs.cotizacion_bcra
_hs.cotizacion_bcra = lambda: (1455.5, "2026-11-18")
r = c.post("/reportes/fx-bcra", data={"mes": "", "csrf": csrf_de(rp.text)})
check("horas: trae la cotización del BCRA", _aj.efectiva(_conn, app.state.cfg).fx_ars_usd == 1455.5 and "BCRA 2026-11-18" in r.text, r.text[-200:])
_hs.cotizacion_bcra = _real_bcra
import httpx as _hx  # noqa: E402
_g = _hx.get
_hx.get = lambda *a, **k: type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"status": 200, "results": {"fecha": "2026-11-17", "detalle": [{"codigoMoneda": "USD", "tipoCotizacion": 1432.25}]}}})()
check("horas: interpreta la respuesta del BCRA", _hs.cotizacion_bcra() == (1432.25, "2026-11-17"))
_hx.get = _g
check("RBAC: el reporte de horas es solo del administrador", tpm.get("/reportes/horas").url.path != "/reportes/horas" and ttec.get("/reportes/horas").url.path != "/reportes/horas")
check("RBAC: el menú muestra Horas al administrador", 'href="/reportes/horas"' in c.get("/").text)

# ── Microsoft 365: ABM de usuarios del tenant de un cliente ─────────────────────────────
from itier_console.adapters import m365 as _m365  # noqa: E402
_fc = c.get("/clientes/canal-sa")
check("m365: la ficha del cliente tiene la sección Microsoft 365", "☁️ Microsoft 365" in _fc.text and "Conectar tenant" in _fc.text)
check("m365: sin conectar, la lista de usuarios manda a la ficha", c.get("/clientes/canal-sa/m365").url.path == "/clientes/canal-sa")
check("m365: un técnico no puede conectar un tenant", "Conectado" not in (ttec.post("/clientes/canal-sa/m365/conectar", data={"tenant": "", "csrf": csrf_de(ttec.get("/").text)}).text))
r = c.post("/clientes/canal-sa/m365/conectar", data={"tenant": "", "csrf": csrf_de(_fc.text)})
check("m365: conectar el tenant (demo) lo deja conectado con su nombre", "Conectado" in r.text and "Cliente Demo SRL" in r.text and "Administrar usuarios" in r.text)
check("m365: sin dominio/ID válido el tenant se rechaza", "no es válido" in c.post("/clientes/canal-sa/m365/conectar", data={"tenant": "no valido!!", "csrf": csrf_de(c.get("/clientes/canal-sa").text)}).text)
lu = c.get("/clientes/canal-sa/m365")
check("m365: lista usuarios con licencias y estado", lu.status_code == 200 and "Ana Pérez" in lu.text and "Microsoft 365 Business Standard" in lu.text and "Bloqueado" in lu.text)
check("m365: muestra los planes con licencias libres", "4 licencias libres" in lu.text or "8 licencias libres" in lu.text)
check("m365: buscar filtra", "Luis Gómez" in c.get("/clientes/canal-sa/m365?q=luis").text and "Ana Pérez" not in c.get("/clientes/canal-sa/m365?q=luis").text)
check("m365: filtro de bloqueados", "Eva Ruiz" in c.get("/clientes/canal-sa/m365?estado=bloqueados").text and "Ana Pérez" not in c.get("/clientes/canal-sa/m365?estado=bloqueados").text)
check("m365: un técnico ve la lista", ttec.get("/clientes/canal-sa/m365").url.path == "/clientes/canal-sa/m365")
nf = c.get("/clientes/canal-sa/m365/nuevo")
check("m365: el alta ofrece dominios verificados y planes", "@clientedemo.com.ar" in nf.text and "Business Basic" in nf.text)
_sku = _m365._SKU_BASICO
r = c.post("/clientes/canal-sa/m365/nuevo", data={"nombre": "María", "apellido": "Gómez", "usuario": "", "dominio": "clientedemo.com.ar",
           "cargo": "Compras", "departamento": "Adm", "licencias": _sku, "enviar_a": "", "csrf": csrf_de(nf.text)})
_mc = re.search(r"Clave:\s+([^\s<]+)", r.text)
check("m365: el alta muestra usuario y clave UNA vez", "Usuario creado" in r.text and "maria.gomez@clientedemo.com.ar" in r.text and bool(_mc), r.text[:300])
_cl = _mc.group(1) if _mc else ""
check("m365: la clave generada es fuerte", len(_cl) >= 16 and re.search(r"[A-Z]", _cl) and re.search(r"[a-z]", _cl) and re.search(r"\d", _cl))
lu = c.get("/clientes/canal-sa/m365")
check("m365: el usuario nuevo aparece con su licencia y la clave no se vuelve a mostrar", "María Gómez" in lu.text and _cl not in lu.text and "Clave:" not in lu.text)
check("m365: la auditoría no guarda la clave", "m365.usuario_creado" in c.get("/auditoria").text and _cl not in c.get("/auditoria").text)
_dup = c.post("/clientes/canal-sa/m365/nuevo", data={"nombre": "María", "apellido": "Gómez", "usuario": "maria.gomez", "dominio": "clientedemo.com.ar",
              "cargo": "", "departamento": "", "enviar_a": "", "csrf": csrf_de(nf.text)})
check("m365: usuario repetido rechazado con el motivo", "Ya existe" in _dup.text and _dup.status_code == 400)
check("m365: dominio que no es del tenant rechazado", "dominio verificado" in c.post("/clientes/canal-sa/m365/nuevo", data={"nombre": "A", "apellido": "B", "usuario": "ab", "dominio": "otro.com",
      "enviar_a": "", "csrf": csrf_de(nf.text)}).text)
check("m365: usuario con caracteres inválidos rechazado", "solo puede llevar" in c.post("/clientes/canal-sa/m365/nuevo", data={"nombre": "A", "apellido": "B", "usuario": "a b/c", "dominio": "clientedemo.com.ar",
      "enviar_a": "", "csrf": csrf_de(nf.text)}).text)
_uid = next(k for k, v in _m365._DEMO_USERS.items() if v["userPrincipalName"] == "maria.gomez@clientedemo.com.ar")
dt = c.get(f"/clientes/canal-sa/m365/{_uid}")
check("m365: el detalle muestra datos, licencias y acciones", "Maria Gómez".replace("Maria", "María") in dt.text and "Reiniciar clave" in dt.text and "Eliminar usuario" in dt.text)
r = c.post(f"/clientes/canal-sa/m365/{_uid}/editar", data={"nombre": "María", "apellido": "Gómez Ruiz", "cargo": "Jefa de compras", "departamento": "Adm", "csrf": csrf_de(dt.text)})
check("m365: editar los datos", "Datos actualizados" in r.text and "Gómez Ruiz" in r.text and "Jefa de compras" in r.text)
r = c.post(f"/clientes/canal-sa/m365/{_uid}/licencias", data={"licencias": _m365._SKU_STD, "csrf": csrf_de(r.text)})
check("m365: cambiar licencias quita una y asigna otra", "Licencias actualizadas" in r.text and [x["skuId"] for x in _m365._DEMO_USERS[_uid]["assignedLicenses"]] == [_m365._SKU_STD])
r = c.post(f"/clientes/canal-sa/m365/{_uid}/estado", data={"habilitar": "0", "csrf": csrf_de(r.text)})
check("m365: bloquear el inicio de sesión", "Usuario bloqueado" in r.text and _m365._DEMO_USERS[_uid]["accountEnabled"] is False)
r = c.post(f"/clientes/canal-sa/m365/{_uid}/estado", data={"habilitar": "1", "csrf": csrf_de(r.text)})
check("m365: habilitar de nuevo", "Usuario habilitado" in r.text and _m365._DEMO_USERS[_uid]["accountEnabled"] is True)
r = c.post(f"/clientes/canal-sa/m365/{_uid}/clave", data={"enviar_a": "", "csrf": csrf_de(r.text)})
_c2 = re.search(r"Clave:\s+([^\s<]+)", r.text)
check("m365: reiniciar clave muestra la nueva una vez", "Clave reiniciada" in r.text and bool(_c2) and _c2.group(1) != _cl and _c2.group(1) not in c.get(f"/clientes/canal-sa/m365/{_uid}").text)
check("m365: un técnico NO puede eliminar usuarios", ttec.post(f"/clientes/canal-sa/m365/{_uid}/eliminar", data={"csrf": csrf_de(ttec.get("/").text)}).url.path != "/clientes/canal-sa/m365"
      and _uid in _m365._DEMO_USERS)
check("m365: id de usuario inválido rechazado", "inválido" in c.get("/clientes/canal-sa/m365/u..x").text)
check("m365: CSRF inválido no cambia nada", "Sesión expirada" in c.post(f"/clientes/canal-sa/m365/{_uid}/estado", data={"habilitar": "0", "csrf": "falso"}).text and _m365._DEMO_USERS[_uid]["accountEnabled"] is True)
r = c.post(f"/clientes/canal-sa/m365/{_uid}/eliminar", data={"csrf": csrf_de(c.get(f"/clientes/canal-sa/m365/{_uid}").text)})
check("m365: el administrador elimina al usuario", "Usuario eliminado" in r.text and _uid not in _m365._DEMO_USERS and "María Gómez" not in r.text)
check("m365: la auditoría registra todas las acciones", all(a in c.get("/auditoria").text for a in ("m365.usuario_editado", "m365.licencias", "m365.usuario_bloqueado", "m365.clave_reiniciada", "m365.usuario_eliminado")))
# aplicación del MSP
ap = c.get("/m365/aplicacion")
check("m365: el administrador ve la configuración de la aplicación con el URI de redirección", ap.status_code == 200 and "/m365/consentimiento" in ap.text and "User.ReadWrite.All" in ap.text)
check("m365: client id inválido rechazado", "36 caracteres" in c.post("/m365/aplicacion", data={"client_id": "corto", "secreto": "", "csrf": csrf_de(ap.text)}).text)
r = c.post("/m365/aplicacion", data={"client_id": "12345678-1234-1234-1234-123456789012", "secreto": "S3cr3to~app", "csrf": csrf_de(ap.text)})
check("m365: se guarda la aplicación y el secreto no se muestra", "guardada" in r.text and "S3cr3to~app" not in r.text and "Configurada" in r.text)
_rw = _conn.execute("SELECT valor, secreto FROM ajustes WHERE clave='m365_client_secret'").fetchone()
check("m365: el secreto queda cifrado en la base", _rw["secreto"] == 1 and "S3cr3to~app" not in _rw["valor"])
check("m365: PM y técnico no configuran la aplicación", tpm.get("/m365/aplicacion").url.path != "/m365/aplicacion" and ttec.get("/m365/aplicacion").url.path != "/m365/aplicacion")
check("m365: el retorno de Microsoft con un state ajeno se rechaza", "no coincide" in c.get("/m365/consentimiento?state=falso&tenant=x&admin_consent=True").text)
_ef = _aj.efectiva(_conn, app.state.cfg)
_u = _m365.url_consentimiento(_ef, "https://c.example/m365/consentimiento", "st123", "empresa.onmicrosoft.com")
check("m365: el enlace de consentimiento lleva client id, redirect y state", _u.startswith("https://login.microsoftonline.com/empresa.onmicrosoft.com/v2.0/adminconsent?")
      and "client_id=12345678-1234-1234-1234-123456789012" in _u and "state=st123" in _u and "redirect_uri=https%3A%2F%2Fc.example" in _u)
r = c.post("/clientes/canal-sa/m365/desconectar", data={"csrf": csrf_de(c.get("/clientes/canal-sa").text)})
check("m365: desconectar el tenant", "Tenant desconectado" in r.text and "Conectar tenant" in r.text)

# ── Google Workspace: ABM de usuarios del dominio de un cliente ─────────────────────────
from itier_console.adapters import gws as _gws  # noqa: E402
_fg = c.get("/clientes/canal-sa")
check("gws: la ficha del cliente tiene la sección Google Workspace", "🟦 Google Workspace" in _fg.text and "Conectar dominio" in _fg.text)
check("gws: sin conectar, la lista manda a la ficha", c.get("/clientes/canal-sa/gws").url.path == "/clientes/canal-sa")
check("gws: un técnico no puede conectar un dominio", "Conectado" not in ttec.post("/clientes/canal-sa/gws/conectar", data={"admin": "admin@gdemo.com.ar", "csrf": csrf_de(ttec.get("/").text)}).text)
check("gws: el correo del administrador se valida", "no es válida" in c.post("/clientes/canal-sa/gws/conectar", data={"admin": "no-es-correo", "csrf": csrf_de(_fg.text)}).text or "No se pudo conectar" in c.get("/clientes/canal-sa").text)
r = c.post("/clientes/canal-sa/gws/conectar", data={"admin": "admin@gdemo.com.ar", "csrf": csrf_de(c.get("/clientes/canal-sa").text)})
check("gws: conectar (demo) deja el dominio conectado", "Conectado" in r.text and "gdemo.com.ar" in r.text and "Administrar usuarios" in r.text)
lg = c.get("/clientes/canal-sa/gws")
check("gws: lista usuarios con estado y administradores", lg.status_code == 200 and "Sofía Paz" in lg.text and "Suspendido" in lg.text and "Admin" in lg.text)
check("gws: buscar y filtrar", "Sofía Paz" in c.get("/clientes/canal-sa/gws?q=sofia").text and "Raúl Díaz" not in c.get("/clientes/canal-sa/gws?q=sofia").text
      and "Raúl Díaz" in c.get("/clientes/canal-sa/gws?estado=suspendidos").text)
check("gws: un técnico ve la lista", ttec.get("/clientes/canal-sa/gws").url.path == "/clientes/canal-sa/gws")
nf = c.get("/clientes/canal-sa/gws/nuevo")
check("gws: el alta ofrece los dominios verificados", "@gdemo.com.ar" in nf.text and "@gdemo.com" in nf.text)
r = c.post("/clientes/canal-sa/gws/nuevo", data={"nombre": "Lucía", "apellido": "Fernández", "usuario": "", "dominio": "gdemo.com.ar", "cargo": "Compras",
           "departamento": "Adm", "enviar_a": "", "csrf": csrf_de(nf.text)})
_gm = re.search(r"Clave:\s+([^\s<]+)", r.text)
check("gws: el alta muestra usuario y clave UNA vez", "Usuario creado" in r.text and "lucia.fernandez@gdemo.com.ar" in r.text and bool(_gm), r.text[:300])
_gc = _gm.group(1) if _gm else ""
check("gws: la clave generada es fuerte y no se vuelve a mostrar", len(_gc) >= 16 and _gc not in c.get("/clientes/canal-sa/gws").text and "Lucía Fernández" in c.get("/clientes/canal-sa/gws").text)
check("gws: la auditoría no guarda la clave", "gws.usuario_creado" in c.get("/auditoria").text and _gc not in c.get("/auditoria").text)
check("gws: usuario repetido rechazado", "Ya existe" in c.post("/clientes/canal-sa/gws/nuevo", data={"nombre": "L", "apellido": "F", "usuario": "lucia.fernandez", "dominio": "gdemo.com.ar", "enviar_a": "", "csrf": csrf_de(nf.text)}).text)
check("gws: dominio ajeno rechazado", "dominio verificado" in c.post("/clientes/canal-sa/gws/nuevo", data={"nombre": "A", "apellido": "B", "usuario": "ab", "dominio": "otro.com", "enviar_a": "", "csrf": csrf_de(nf.text)}).text)
check("gws: usuario inválido rechazado", "solo puede llevar" in c.post("/clientes/canal-sa/gws/nuevo", data={"nombre": "A", "apellido": "B", "usuario": "a b/c", "dominio": "gdemo.com.ar", "enviar_a": "", "csrf": csrf_de(nf.text)}).text)
_gid = next(k for k, v in _gws._DEMO_USERS.items() if v["primaryEmail"] == "lucia.fernandez@gdemo.com.ar")
dg = c.get(f"/clientes/canal-sa/gws/{_gid}")
r = c.post(f"/clientes/canal-sa/gws/{_gid}/editar", data={"nombre": "Lucía", "apellido": "Fernández Paz", "cargo": "Jefa de compras", "departamento": "Adm", "csrf": csrf_de(dg.text)})
check("gws: editar datos", "Datos actualizados" in r.text and "Fernández Paz" in r.text and "Jefa de compras" in r.text)
r = c.post(f"/clientes/canal-sa/gws/{_gid}/estado", data={"suspender": "1", "csrf": csrf_de(r.text)})
check("gws: suspender el inicio de sesión", "Usuario suspendido" in r.text and _gws._DEMO_USERS[_gid]["suspended"] is True)
r = c.post(f"/clientes/canal-sa/gws/{_gid}/estado", data={"suspender": "0", "csrf": csrf_de(r.text)})
check("gws: reactivar", "Usuario reactivado" in r.text and _gws._DEMO_USERS[_gid]["suspended"] is False)
r = c.post(f"/clientes/canal-sa/gws/{_gid}/clave", data={"enviar_a": "", "csrf": csrf_de(r.text)})
_g2 = re.search(r"Clave:\s+([^\s<]+)", r.text)
check("gws: reiniciar clave muestra la nueva una vez", "Clave reiniciada" in r.text and bool(_g2) and _g2.group(1) != _gc and _g2.group(1) not in c.get(f"/clientes/canal-sa/gws/{_gid}").text)
_adm = "g-admin"
da = c.get(f"/clientes/canal-sa/gws/{_adm}")
check("gws: el administrador del dominio no ofrece reiniciar/suspender/eliminar", "Reiniciar clave" not in da.text and "Eliminar usuario" not in da.text and "no reinicia su clave" in da.text)
check("gws: aunque se fuerce la petición, no se opera sobre un administrador",
      "es administrador del dominio" in c.post(f"/clientes/canal-sa/gws/{_adm}/clave", data={"enviar_a": "", "csrf": csrf_de(da.text)}).text
      and "es administrador del dominio" in c.post(f"/clientes/canal-sa/gws/{_adm}/estado", data={"suspender": "1", "csrf": csrf_de(da.text)}).text
      and "es administrador del dominio" in c.post(f"/clientes/canal-sa/gws/{_adm}/eliminar", data={"csrf": csrf_de(da.text)}).text
      and _gws._DEMO_USERS[_adm]["suspended"] is False and _adm in _gws._DEMO_USERS)
check("gws: un técnico NO puede eliminar", ttec.post(f"/clientes/canal-sa/gws/{_gid}/eliminar", data={"csrf": csrf_de(ttec.get("/").text)}).url.path != "/clientes/canal-sa/gws" and _gid in _gws._DEMO_USERS)
check("gws: id inválido rechazado", "inválido" in c.get("/clientes/canal-sa/gws/u..x").text)
check("gws: CSRF inválido no cambia nada", "Sesión expirada" in c.post(f"/clientes/canal-sa/gws/{_gid}/estado", data={"suspender": "1", "csrf": "falso"}).text and _gws._DEMO_USERS[_gid]["suspended"] is False)
r = c.post(f"/clientes/canal-sa/gws/{_gid}/eliminar", data={"csrf": csrf_de(c.get(f"/clientes/canal-sa/gws/{_gid}").text)})
check("gws: el administrador de la consola elimina al usuario", "Usuario eliminado" in r.text and _gid not in _gws._DEMO_USERS)
check("gws: la auditoría registra las acciones", all(a in c.get("/auditoria").text for a in ("gws.conectado", "gws.usuario_editado", "gws.usuario_suspendido", "gws.clave_reiniciada", "gws.usuario_eliminado")))
ga = c.get("/gws/aplicacion")
check("gws: la pantalla de la cuenta de servicio explica los pasos", ga.status_code == 200 and "Admin SDK API" in ga.text and "admin.directory.user" in ga.text)
check("gws: JSON inválido rechazado", "no se guardó" in c.post("/gws/aplicacion", data={"clave_json": "{no es json", "csrf": csrf_de(ga.text)}).text)
check("gws: un JSON que no es de cuenta de servicio se rechaza", "service_account" in c.post("/gws/aplicacion", data={"clave_json": "{\"type\": \"otra\"}", "csrf": csrf_de(ga.text)}).text)
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa  # noqa: E402
from cryptography.hazmat.primitives import serialization as _ser  # noqa: E402
import json as _json  # noqa: E402
_pem = _rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption()).decode()
_sa_json = _json.dumps({"type": "service_account", "client_email": "itier@proy.iam.gserviceaccount.com", "client_id": "112233445566778899", "private_key": _pem})
r = c.post("/gws/aplicacion", data={"clave_json": _sa_json, "csrf": csrf_de(ga.text)})
check("gws: se guarda la cuenta de servicio y se muestran solo correo e ID de cliente", "itier@proy.iam.gserviceaccount.com" in r.text and "112233445566778899" in r.text and "BEGIN PRIVATE KEY" not in r.text)
_rg = _conn.execute("SELECT valor, secreto FROM ajustes WHERE clave='gws_sa_json'").fetchone()
check("gws: la clave privada queda cifrada en la base", _rg["secreto"] == 1 and "BEGIN PRIVATE KEY" not in _rg["valor"])
check("gws: la auditoría no guarda la clave privada", "gws.cuenta_servicio_configurada" in c.get("/auditoria").text and "PRIVATE KEY" not in c.get("/auditoria").text)
check("gws: PM y técnico no configuran la cuenta de servicio", tpm.get("/gws/aplicacion").url.path != "/gws/aplicacion" and ttec.get("/gws/aplicacion").url.path != "/gws/aplicacion")
r = c.post("/clientes/canal-sa/gws/desconectar", data={"csrf": csrf_de(c.get("/clientes/canal-sa").text)})
check("gws: desconectar el dominio", "Dominio desconectado" in r.text and "Conectar dominio" in r.text)

# Cambiar la clave propia se propaga a GLPI, Zabbix y MeshCentral
from itier_console.adapters import glpi as _gl, zabbix as _zb, mesh as _ms  # noqa: E402
_llamadas = []
_orig = (_gl.GLPIClient.fijar_password_usuario, _zb.ZabbixClient.fijar_password_usuario, _ms.MeshClient.eliminar_usuario, _ms.MeshClient.asegurar_usuario)
_gl.GLPIClient.fijar_password_usuario = lambda self, uid, pw: _llamadas.append(("glpi", uid, pw))
_zb.ZabbixClient.fijar_password_usuario = lambda self, uid, pw: _llamadas.append(("zbx", uid, pw))
_ms.MeshClient.eliminar_usuario = lambda self, u: _llamadas.append(("mesh-del", u))
_ms.MeshClient.asegurar_usuario = lambda self, u, pw, em, rol, g, reset_next_login=True: (_llamadas.append(("mesh-add", u, pw, reset_next_login)), "creado")[1]
_rs = c.post("/usuarios", data={"username": "tecsync", "email": "tecsync@pymesenlinea.com.ar", "nombre": "Tec Sync", "rol": "tecnico", "csrf": csrf_de(c.get("/usuarios").text)})
_cs = re.search(r"Clave:\s+([^\s<]+)", _rs.text).group(1)
_llamadas.clear()
_tsy, _rsy = _login_operador("tecsync", _cs)
_ids = _conn.execute("SELECT glpi_user_id, zbx_user_id FROM users WHERE username='tecsync'").fetchone()
check("clave propia: al cambiarla por primera vez se actualiza en GLPI y Zabbix con los ids de la cuenta",
      ("glpi", _ids["glpi_user_id"], "Clave-Propia-123") in _llamadas and ("zbx", _ids["zbx_user_id"], "Clave-Propia-123") in _llamadas, _llamadas)
check("clave propia: en Mesh se recrea la cuenta con la clave nueva y sin pedir otro cambio",
      ("mesh-del", "tecsync") in _llamadas and ("mesh-add", "tecsync", "Clave-Propia-123", False) in _llamadas, _llamadas)
check("clave propia: el mensaje confirma los cuatro sistemas", "también en GLPI, Zabbix, MeshCentral" in _rsy.text, _rsy.text[-400:])
check("clave propia: la auditoría registra la propagación sin la clave", "usuario.clave_propagada" in c.get("/auditoria").text and "Clave-Propia-123" not in c.get("/auditoria").text)
_llamadas.clear()
_r2 = _tsy.post("/cambiar-clave", data={"actual": "Clave-Propia-123", "nueva": "Otra-Clave-456", "repetir": "Otra-Clave-456", "csrf": csrf_de(_tsy.get("/cambiar-clave").text)})
check("clave propia: un cambio voluntario también se propaga", ("glpi", _ids["glpi_user_id"], "Otra-Clave-456") in _llamadas and ("zbx", _ids["zbx_user_id"], "Otra-Clave-456") in _llamadas)
def _falla(self, uid, pw): raise _AdErr("GLPI", "caído")
from itier_console.adapters import AdapterError as _AdErr  # noqa: E402
_gl.GLPIClient.fijar_password_usuario = _falla
_llamadas.clear()
_r3 = _tsy.post("/cambiar-clave", data={"actual": "Otra-Clave-456", "nueva": "Tercera-Clave-789", "repetir": "Tercera-Clave-789", "csrf": csrf_de(_tsy.get("/cambiar-clave").text)})
check("clave propia: si un sistema falla, se avisa cuál y la clave de la consola igual cambió",
      "NO se pudo actualizar en GLPI" in _r3.text and ("zbx", _ids["zbx_user_id"], "Tercera-Clave-789") in _llamadas
      and _tsy.post("/cambiar-clave", data={"actual": "Tercera-Clave-789", "nueva": "Cuarta-Clave-000", "repetir": "Cuarta-Clave-000", "csrf": csrf_de(_tsy.get("/cambiar-clave").text)}).status_code == 200, _r3.text[-300:])
_gl.GLPIClient.fijar_password_usuario, _zb.ZabbixClient.fijar_password_usuario, _ms.MeshClient.eliminar_usuario, _ms.MeshClient.asegurar_usuario = _orig
check("clave propia: un usuario del portal no toca los otros sistemas", _hs is not None and __import__("itier_console.services.usuarios", fromlist=["x"]).propagar_clave(_conn, app.state.cfg, 999999, "x") == {})

# Responder y cerrar tickets
from itier_console.services import tickets as _ts  # noqa: E402
check("tickets: texto plano desde HTML de GLPI", _ts.texto_plano("&lt;p&gt;Hola &amp;amp; chau&lt;/p&gt;&lt;p&gt;linea 2&lt;/p&gt;") == "Hola & chau\nlinea 2")
_tp = c.get(f"/tickets/{t1}")
check("tickets: el detalle tiene conversación, responder y estado del caso", all(x in _tp.text for x in ("💬 Conversación", "Enviar respuesta", "🚦 Estado del caso", "Marcar como resuelto")))
check("tickets: ofrece enviar por WhatsApp al solicitante (vino por ahí)", "Avisar al cliente: WhatsApp +5491155551234" in _tp.text)
r = c.post(f"/tickets/{t1}/responder", data={"texto": "Ya revisamos la impresora, pasá por recepción.", "enviar": "1", "csrf": csrf_de(_tp.text)})
check("tickets: responder agrega el mensaje y lo envía por WhatsApp", "Respuesta agregada y se avisó a WhatsApp +5491155551234" in r.text and "Ya revisamos la impresora" in r.text, r.text[:400])
r = c.post(f"/tickets/{t1}/responder", data={"texto": "Revisar el cable antes de ir", "privado": "1", "csrf": csrf_de(r.text)})
check("tickets: la nota interna queda marcada y no se envía", "Nota interna agregada." in r.text and "🔒 Nota interna" in r.text)
check("tickets: respuesta vacía rechazada", "Escribí la respuesta" in c.post(f"/tickets/{t1}/responder", data={"texto": "  ", "csrf": csrf_de(r.text)}).text)
check("tickets: CSRF inválido no agrega nada", "Sesión expirada" in c.post(f"/tickets/{t1}/responder", data={"texto": "x", "csrf": "falso"}).text)
r2 = c.post(f"/tickets/{t2}/responder", data={"texto": "Te lo resolvemos hoy", "enviar": "1", "csrf": csrf_de(c.get(f"/tickets/{t2}").text)})
check("tickets: si no se puede enviar por correo, la respuesta igual queda en el ticket y avisa", "no se pudo avisar al cliente" in r2.text and "Te lo resolvemos hoy" in r2.text)
r = c.post(f"/tickets/{t1}/estado", data={"accion": "pendiente", "csrf": csrf_de(c.get(f"/tickets/{t1}").text)})
check("tickets: pasar a en espera", "quedó en espera" in r.text and "En espera" in r.text)
check("tickets: resolver exige la solución", "indicá qué se hizo" in c.post(f"/tickets/{t1}/estado", data={"accion": "resolver", "solucion": "", "csrf": csrf_de(r.text)}).text)
r = c.post(f"/tickets/{t1}/estado", data={"accion": "resolver", "solucion": "Se cambió el tóner", "enviar": "1", "csrf": csrf_de(r.text)})
check("tickets: resolver deja el ticket en Resuelto y avisa al cliente", "marcado como resuelto y se avisó a WhatsApp" in r.text and "Cerrar" in r.text and "Reabrir" in r.text, re.findall(r"flash[^>]*>([^<]*)", r.text))
r = c.post(f"/tickets/{t1}/estado", data={"accion": "cerrar", "csrf": csrf_de(r.text)})
check("tickets: cerrar oculta el formulario de respuesta", "Ticket cerrado" in r.text and "Enviar respuesta" not in r.text and "El ticket está cerrado" in r.text)
r = c.post(f"/tickets/{t1}/estado", data={"accion": "reabrir", "csrf": csrf_de(r.text)})
check("tickets: reabrir vuelve a En curso con el formulario de respuesta", "Ticket reabierto" in r.text and "Enviar respuesta" in r.text and "En curso" in r.text)
check("tickets: acción de estado inválida rechazada", "Acción inválida" in c.post(f"/tickets/{t1}/estado", data={"accion": "borrar", "csrf": csrf_de(r.text)}).text)
check("tickets: el técnico también puede responder y resolver", "Respuesta agregada" in ttec.post(f"/tickets/{t1}/responder", data={"texto": "Listo", "csrf": csrf_de(ttec.get(f"/tickets/{t1}").text)}).text)
_au = c.get("/auditoria").text
check("tickets: la auditoría registra respuestas y cambios de estado", all(a in _au for a in ("ticket.respuesta", "ticket.nota", "ticket.pendiente", "ticket.resolver", "ticket.cerrar", "ticket.reabrir", "ticket.cliente_avisado")))

# Portal: conversación, respuestas del cliente y avisos por correo
from itier_console.services import correo as _co, notificaciones as _nt  # noqa: E402
_enviados = []
_co_orig = (_co.configurado, _co.enviar)
_co.configurado = lambda cfg: True
_co.enviar = lambda cfg, dest, asunto, cuerpo, html=None: _enviados.append((dest, asunto, cuerpo))
_rpu = c.post("/clientes/canal-sa/portal-usuarios", data={"username": "ronan.cp", "password": "Clave-Portal-123", "email": "ronan@canalsa.com.ar", "csrf": csrf_de(c.get("/clientes/canal-sa").text)})
check("portal: el usuario se crea con su correo y se ve en la ficha", "ronan@canalsa.com.ar" in c.get("/clientes/canal-sa").text and "Correo (avisos)" in c.get("/clientes/canal-sa").text)
check("portal: correo inválido rechazado al crear", "no es válida" in c.post("/clientes/canal-sa/portal-usuarios", data={"username": "x.cp", "password": "Clave-Portal-123", "email": "malo", "csrf": csrf_de(c.get("/clientes/canal-sa").text)}).text)
_pid = _conn.execute("SELECT id FROM users WHERE username='ronan.cp'").fetchone()["id"]
c.post(f"/clientes/canal-sa/portal-usuarios/{_pid}/email", data={"email": "ronan.p@canalsa.com.ar", "csrf": csrf_de(c.get("/clientes/canal-sa").text)})
check("portal: se puede cambiar el correo de un usuario del portal", _conn.execute("SELECT email FROM users WHERE id=?", (_pid,)).fetchone()["email"] == "ronan.p@canalsa.com.ar")
c.post(f"/clientes/canal-sa/portal-usuarios/{_pid}/email", data={"email": "ronan@canalsa.com.ar", "csrf": csrf_de(c.get("/clientes/canal-sa").text)})
pcs = TestClient(app, follow_redirects=True)
_h = pcs.get("/login").text
_r = pcs.post("/login", data={"username": "ronan.cp", "password": "Clave-Portal-123", "csrf": csrf_de(_h)})
_sec = re.search(r'class="codebox"><code>([^<]+)</code>', _r.text).group(1)
_r = pcs.post("/login/2fa/enrolar", data={"code": pyotp.TOTP(_sec).now(), "csrf": csrf_de(_r.text)})
if _r.url.path == "/cambiar-clave":
    _r = pcs.post("/cambiar-clave", data={"actual": "", "nueva": "Clave-Portal-Nueva1", "repetir": "Clave-Portal-Nueva1", "csrf": csrf_de(_r.text)})
check("portal: el usuario entra", _r.url.path == "/portal", _r.url.path)
_r = pcs.post("/portal/tickets/nuevo", data={"titulo": "No imprime el plotter", "contenido": "Desde ayer", "prioridad": "3", "csrf": csrf_de(pcs.get("/portal/tickets/nuevo").text)})
_tp_ = int(re.search(r"/portal/tickets/(\d+)", _r.url.path).group(1))
check("portal: el ticket abierto desde el portal queda con su origen", _conn.execute("SELECT canal, remitente FROM ticket_origen WHERE ticket_id=?", (_tp_,)).fetchone()["canal"] == "portal")
check("portal: el cliente ve la conversación vacía y puede escribir", "Todavía no hay respuestas" in _r.text and "Enviar" in _r.text)
_pp = c.get(f"/tickets/{_tp_}")
check("tickets: ofrece avisar al correo del usuario que abrió el ticket", "Avisar al cliente: ronan@canalsa.com.ar" in _pp.text)
_enviados.clear()
r = c.post(f"/tickets/{_tp_}/responder", data={"texto": "Reiniciamos el servicio de impresión, probá ahora.", "enviar": "1", "csrf": csrf_de(_pp.text)})
check("tickets: responder le manda un correo al cliente con el enlace al portal", "se avisó a ronan@canalsa.com.ar" in r.text and len(_enviados) == 1
      and _enviados[0][0] == "ronan@canalsa.com.ar" and f"[#{_tp_}]" in _enviados[0][1] and "No imprime el plotter" in _enviados[0][1]
      and "Reiniciamos el servicio" in _enviados[0][2], _enviados)
c.post(f"/tickets/{_tp_}/responder", data={"texto": "Nota: el cliente es complicado", "privado": "1", "enviar": "1", "csrf": csrf_de(r.text)})
check("tickets: una nota interna NO se envía al cliente", len(_enviados) == 1)
pv = pcs.get(f"/portal/tickets/{_tp_}")
check("portal: el cliente ve la respuesta de soporte y NO la nota interna", "Reiniciamos el servicio" in pv.text and "Soporte" in pv.text and "complicado" not in pv.text)
_enviados.clear()
r = pcs.post(f"/portal/tickets/{_tp_}/responder", data={"texto": "Sigue sin imprimir, error 5", "csrf": csrf_de(pv.text)})
check("portal: el cliente responde y lo ve como «Vos»", "Tu mensaje fue enviado a soporte" in r.text and "Sigue sin imprimir" in r.text and "Vos" in r.text and "Cliente ·" not in r.text, r.text[-500:])
check("portal: se avisa a la casilla de soporte con la respuesta del cliente", any(e[0] == "soporte@pymesenlinea.com.ar" and "Respuesta del cliente" in e[1] and "error 5" in e[2] for e in _enviados), _enviados)
check("tickets: el técnico ve el mensaje del cliente en la conversación", "Sigue sin imprimir" in c.get(f"/tickets/{_tp_}").text)
check("portal: la respuesta del cliente queda auditada", "portal.ticket.respuesta" in c.get("/auditoria").text)
c.post(f"/tickets/{_tp_}/estado", data={"accion": "resolver", "solucion": "Se reinstaló el driver", "enviar": "1", "csrf": csrf_de(c.get(f"/tickets/{_tp_}").text)})
check("tickets: resolver también avisa al cliente por correo", any("Resolvimos tu consulta" in e[2] and e[0] == "ronan@canalsa.com.ar" for e in _enviados))
r = pcs.post(f"/portal/tickets/{_tp_}/responder", data={"texto": "Volvió a fallar", "csrf": csrf_de(pcs.get(f"/portal/tickets/{_tp_}").text)})
check("portal: responder a un ticket resuelto lo reabre", "En curso" in pcs.get(f"/portal/tickets/{_tp_}").text, pcs.get(f"/portal/tickets/{_tp_}").text[:600])
c.post(f"/tickets/{_tp_}/estado", data={"accion": "resolver", "solucion": "ok", "csrf": csrf_de(c.get(f"/tickets/{_tp_}").text)})
c.post(f"/tickets/{_tp_}/estado", data={"accion": "cerrar", "csrf": csrf_de(c.get(f"/tickets/{_tp_}").text)})
check("portal: en un ticket cerrado no se puede responder", "cerrado" in pcs.post(f"/portal/tickets/{_tp_}/responder", data={"texto": "hola", "csrf": csrf_de(pcs.get(f"/portal/tickets/{_tp_}").text)}).text)
check("portal: no se puede responder un ticket de otra empresa (IDOR)", "Ticket no encontrado" in pcs.post("/portal/tickets/1042/responder", data={"texto": "intruso", "csrf": csrf_de(pcs.get("/portal/tickets").text)}).text)
check("portal: respuesta vacía rechazada", "Escribí tu mensaje" in pcs.post(f"/portal/tickets/{_tp_}/responder", data={"texto": "  ", "csrf": csrf_de(pcs.get(f"/portal/tickets/{_tp_}").text)}).text)
# ticket abierto desde la consola (sin origen): se avisa a los usuarios del portal con correo
_rn = c.post("/tickets/nuevo", data={"cliente": "canal-sa", "titulo": "Ticket de consola", "contenido": "x", "prioridad": "3", "csrf": csrf_de(c.get("/tickets/nuevo").text)})
_tc = int(re.search(r"/tickets/(\d+)", _rn.url.path).group(1))
check("tickets: sin origen, avisa a los usuarios del portal que tienen correo", "Avisar al cliente: ronan@canalsa.com.ar" in c.get(f"/tickets/{_tc}").text)
check("avisos: sin destinatarios devuelve lista vacía", _nt.destinatarios(_conn, 999999, None) == [])
check("avisos: se reconocen los mensajes del cliente por su marca", _nt.es_del_cliente("Cliente · Ana:\nhola") and _nt.es_del_cliente("texto\n\n— Recibido por WhatsApp de X") and not _nt.es_del_cliente("Ya lo vimos"))
_co.configurado, _co.enviar = _co_orig

# Correo con Microsoft 365 (buzón compartido, OAuth2)
_pg = c.get("/canales")
check("canales: ofrece el método Microsoft 365 con los pasos para prepararlo", "Microsoft 365 (aplicación, para buzones compartidos)" in _pg.text and "IMAP.AccessAsApp" in _pg.text and "Add-MailboxPermission" in _pg.text)
r = c.post("/canales/correo", data={"soporte_email": "soporte@pymesenlinea.com.ar", "imap_auth": "oauth2_ms", "imap_host": "outlook.office365.com", "imap_port": "993",
          "imap_user": "soporte@pymesenlinea.com.ar", "imap_password": "", "imap_folder": "INBOX", "imap_tls": "ssl", "imap_poll_s": "60",
          "imap_ms_tenant": "11111111-2222-3333-4444-555555555555", "imap_ms_client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "imap_ms_secret": "S3cr3t0~MS", "csrf": csrf_de(_pg.text)})
_efm = _aj.efectiva(_conn, app.state.cfg)
check("canales: se guarda el acceso Microsoft 365 y queda configurado", _efm.imap_auth == "oauth2_ms" and _efm.imap_configurado and _efm.imap_ms_client_id.startswith("aaaaaaaa") and "S3cr3t0~MS" not in r.text, r.text[-300:])
_rm = _conn.execute("SELECT valor, secreto FROM ajustes WHERE clave='imap_ms_secret'").fetchone()
check("canales: el secreto de la aplicación queda cifrado", _rm["secreto"] == 1 and "S3cr3t0~MS" not in _rm["valor"])
check("canales: la auditoría no guarda el secreto", "S3cr3t0~MS" not in c.get("/auditoria").text)
_incompleto = _aj.efectiva(_conn, app.state.cfg).__class__(**{**_efm.__dict__, "imap_ms_secret": None})
check("canales: sin el secreto de la aplicación no se considera configurado", not _incompleto.imap_configurado)
_visto = {}
class _IM:
    def __init__(s, *a, **k): pass
    def authenticate(s, mec, cb): _visto["mec"], _visto["resp"] = mec, cb(None)
    def select(s, f, readonly=False): return "OK", [b"1"]
    def search(s, *a): return "OK", [b"1"]
    def logout(s): pass
_rs2, _rt2 = _imaplib.IMAP4_SSL, _cn._token_imap_ms
_imaplib.IMAP4_SSL, _cn._token_imap_ms = _IM, (lambda cfg: "TOKEN-MS")
try:
    _txt = _cn.probar_imap(_efm)
finally:
    _imaplib.IMAP4_SSL, _cn._token_imap_ms = _rs2, _rt2
check("canales: Microsoft 365 autentica por XOAUTH2 con el buzón y el token", _visto.get("mec") == "XOAUTH2"
      and _visto["resp"] == b"user=soporte@pymesenlinea.com.ar\x01auth=Bearer TOKEN-MS\x01\x01" and "1 correo(s) sin leer" in _txt, _visto)
class _IM2(_IM):
    def authenticate(s, mec, cb): raise _imaplib.IMAP4.error("AUTHENTICATE failed.")
_imaplib.IMAP4_SSL, _cn._token_imap_ms = _IM2, (lambda cfg: "TOKEN-MS")
try:
    _cn.probar_imap(_efm); _ok = False
except Exception as _e:  # noqa: BLE001
    _ok = "IMAP.AccessAsApp" in str(getattr(_e, "mensaje", _e)) and "FullAccess" in str(getattr(_e, "mensaje", _e))
finally:
    _imaplib.IMAP4_SSL, _cn._token_imap_ms = _rs2, _rt2
check("canales: si Exchange rechaza a la aplicación, el error dice qué revisar", _ok)
# volver al método clásico para no afectar otras pruebas
c.post("/canales/correo", data={"soporte_email": "soporte@pymesenlinea.com.ar", "imap_auth": "password", "imap_host": "imap.otro.com", "imap_port": "993",
       "imap_user": "soporte@pymesenlinea.com.ar", "imap_password": "", "imap_folder": "INBOX", "imap_tls": "ssl", "imap_poll_s": "60", "csrf": csrf_de(c.get("/canales").text)})

# Correo saliente: Reply-To al buzón de soporte y no procesar lo propio
import smtplib as _smtp  # noqa: E402
_msgs = []
class _SM:
    def __init__(s, *a, **k): pass
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def login(s, *a): pass
    def starttls(s, **k): pass
    def send_message(s, m): _msgs.append(m)
_sm0 = (_smtp.SMTP, _smtp.SMTP_SSL)
_smtp.SMTP = _smtp.SMTP_SSL = _SM
import dataclasses as _dcl  # noqa: E402
_cs = _dcl.replace(app.state.cfg, smtp_host="smtp.x", smtp_from="no-reply@pymesenlinea.com.ar", soporte_email="soporte@pymesenlinea.com.ar", smtp_tls="ssl")
_co.enviar(_cs, "cli@x.com", "Hola", "cuerpo")
_cs2 = _dcl.replace(_cs, smtp_from="Soporte <soporte@pymesenlinea.com.ar>")
_co.enviar(_cs2, "cli@x.com", "Hola", "cuerpo")
_smtp.SMTP, _smtp.SMTP_SSL = _sm0
check("correo: si envía desde otra dirección, las respuestas van al buzón de soporte (Reply-To)", _msgs[0]["Reply-To"] == "soporte@pymesenlinea.com.ar")
check("correo: si envía desde la propia casilla de soporte no agrega Reply-To", _msgs[1]["Reply-To"] is None)
_cr = _dcl.replace(_cs, imap_user="soporte@pymesenlinea.com.ar")
check("correo: ignora lo enviado desde las direcciones propias (sin bucles)",
      _cn.procesar_email(_cr, _conn, _mail("Soporte <no-reply@pymesenlinea.com.ar>", "Re: [#1] x", "eco", "loop1"))["estado"] == "ignorado"
      and _cn.procesar_email(_cr, _conn, _mail("soporte@pymesenlinea.com.ar", "Re: [#1] x", "eco", "loop2"))["estado"] == "ignorado")

# ── Activos de un cliente y asignación a personas ───────────────────────────────────────
from itier_console.services import activos as _ac  # noqa: E402
from datetime import date as _date, timedelta as _td  # noqa: E402
_fa = c.get("/clientes/canal-sa")
check("activos: la ficha del cliente tiene la sección Activos", "📦 Activos" in _fa.text and "Ver y gestionar activos" in _fa.text)
la = c.get("/clientes/canal-sa/activos")
check("activos: lista vacía con importación y exportación", la.status_code == 200 and "Todavía no hay activos" in la.text and "Importar desde Excel / CSV" in la.text and "plantilla" in la.text)
c.post("/clientes/canal-sa/personas", data={"nombre": "Ana Pérez", "cargo": "Gerente", "area": "Gerencia", "email": "ana@canalsa.com.ar", "csrf": csrf_de(c.get("/clientes/canal-sa/personas").text)})
c.post("/clientes/canal-sa/personas", data={"nombre": "Luis Gómez", "cargo": "", "area": "Ventas", "email": "", "csrf": csrf_de(c.get("/clientes/canal-sa/personas").text)})
pg_ = c.get("/clientes/canal-sa/personas")
check("activos: se cargan personas (con o sin correo)", "Ana Pérez" in pg_.text and "Luis Gómez" in pg_.text and "ana@canalsa.com.ar" in pg_.text)
check("activos: correo de persona inválido o repetido rechazado", "no es válido" in c.post("/clientes/canal-sa/personas", data={"nombre": "X", "email": "malo", "csrf": csrf_de(pg_.text)}).text
      and "ya está cargado" in c.post("/clientes/canal-sa/personas", data={"nombre": "Otra Ana", "email": "ana@canalsa.com.ar", "csrf": csrf_de(pg_.text)}).text)
_pa = _conn.execute("SELECT id FROM client_contactos WHERE nombre='Ana Pérez'").fetchone()["id"]
_pl = _conn.execute("SELECT id FROM client_contactos WHERE nombre='Luis Gómez'").fetchone()["id"]
_cid_ = _conn.execute("SELECT id FROM clients WHERE slug='canal-sa'").fetchone()["id"]
_hoy = _date.today()
def _alta(**kw):
    d = {"tipo": "notebook", "etiqueta": "", "serie": "", "marca": "", "modelo": "", "imei": "", "linea": "", "fecha_compra": "", "garantia_hasta": "", "notas": "", "persona_id": "", "csrf": csrf_de(c.get("/clientes/canal-sa/activos/nuevo").text)}
    d.update(kw)
    return c.post("/clientes/canal-sa/activos/nuevo", data=d)
nf_ = c.get("/clientes/canal-sa/activos/nuevo")
check("activos: el formulario ofrece los tipos y las personas", all(x in nf_.text for x in ("Notebook", "Desktop", "Monitor", "Celular")) and "Ana Pérez" in nf_.text)
r = _alta(tipo="notebook", etiqueta="NB-001", marca="Lenovo", modelo="ThinkPad E14", serie="PF3ABC12", fecha_compra=(_hoy - _td(days=100)).isoformat(),
          garantia_hasta=(_hoy + _td(days=900)).isoformat(), persona_id=str(_pa))
check("activos: alta de una notebook asignada queda «En uso» con su historial", "Activo agregado" in r.text and "NB-001" in r.text and "En uso" in r.text and "Asignado a" in r.text and "Ana Pérez" in r.text, r.text[-400:])
_alta(tipo="desktop", etiqueta="PC-002", marca="HP", modelo="ProDesk")
_alta(tipo="monitor", etiqueta="MON-014", marca="Dell", modelo="P2422H", serie="CN0XYZ99", garantia_hasta=(_hoy + _td(days=30)).isoformat())
_alta(tipo="celular", etiqueta="CEL-003", marca="Samsung", modelo="Galaxy A54", imei="356938035643809", linea="1155551234", garantia_hasta=(_hoy - _td(days=10)).isoformat(), persona_id=str(_pl))
la = c.get("/clientes/canal-sa/activos")
check("activos: la lista muestra los cuatro tipos con su estado y asignación", all(x in la.text for x in ("NB-001", "PC-002", "MON-014", "CEL-003")) and "En stock" in la.text and "IMEI 356938035643809" in la.text)
check("activos: el resumen cuenta total, asignados y garantías", _ac.resumen(_conn, _cid_)["total"] == 4 and _ac.resumen(_conn, _cid_)["asignados"] == 2 and _ac.resumen(_conn, _cid_)["garantia"] == 2)
check("activos: la garantía muestra vencida y por vencer", "Vencida" in la.text and "Por vencer" in la.text)
check("activos: filtros por tipo, persona, estado y garantía", "MON-014" in c.get("/clientes/canal-sa/activos?tipo=monitor").text and "NB-001" not in c.get("/clientes/canal-sa/activos?tipo=monitor").text
      and "NB-001" in c.get(f"/clientes/canal-sa/activos?persona={_pa}").text and "PC-002" not in c.get(f"/clientes/canal-sa/activos?persona={_pa}").text
      and "PC-002" in c.get("/clientes/canal-sa/activos?persona=sin").text and "PC-002" in c.get("/clientes/canal-sa/activos?estado=en_stock").text
      and "MON-014" in c.get("/clientes/canal-sa/activos?estado=garantia").text and "NB-001" not in c.get("/clientes/canal-sa/activos?estado=garantia").text)
check("activos: búsqueda por serie, IMEI y persona", "NB-001" in c.get("/clientes/canal-sa/activos?q=PF3ABC").text and "CEL-003" in c.get("/clientes/canal-sa/activos?q=356938").text and "CEL-003" in c.get("/clientes/canal-sa/activos?q=Luis").text)
for nombre, datos, texto in (
    ("sin tipo ni identificador", {"tipo": "otro"}, "al menos la etiqueta"),
    ("etiqueta repetida", {"tipo": "notebook", "etiqueta": "NB-001"}, "Ya existe un activo con esa etiqueta"),
    ("serie repetida", {"tipo": "monitor", "etiqueta": "MON-99", "serie": "CN0XYZ99"}, "número de serie"),
    ("fecha inválida", {"tipo": "notebook", "etiqueta": "NB-77", "fecha_compra": "32/13/2025"}, "no es válida"),
    ("garantía anterior a la compra", {"tipo": "notebook", "etiqueta": "NB-78", "fecha_compra": "2025-05-01", "garantia_hasta": "2025-01-01"}, "no puede vencer antes"),
    ("tipo inexistente", {"tipo": "tostadora", "etiqueta": "X"}, "Elegí el tipo"),
    ("persona inexistente", {"tipo": "notebook", "etiqueta": "NB-79", "persona_id": "999999"}, "persona de este cliente")):
    check(f"activos: rechaza {nombre}", texto in _alta(**datos).text)
c.post("/clientes/nuevo", data={"nombre": "Otro Cliente", "notas": "", "csrf": csrf_de(c.get("/clientes/nuevo").text)})
_oc = _conn.execute("SELECT id FROM clients WHERE slug='otro-cliente'").fetchone()["id"]
_po = _ac.crear_persona(_conn, _oc, "admin", "Persona Ajena")
check("activos: no se puede asignar a una persona de OTRO cliente", "persona de este cliente" in _alta(tipo="notebook", etiqueta="NB-80", persona_id=str(_po)).text)
_aid = _conn.execute("SELECT id FROM activos WHERE etiqueta='PC-002'").fetchone()["id"]
check("activos: un activo de otro cliente no es accesible por la URL del cliente", "no encontrado" in c.get(f"/clientes/otro-cliente/activos/{_aid}").text.lower() or c.get(f"/clientes/otro-cliente/activos/{_aid}").url.path == "/clientes/otro-cliente/activos")
dt_ = c.get(f"/clientes/canal-sa/activos/{_aid}")
check("activos: el detalle muestra datos, asignación, estado e historial", "Asignación" in dt_.text and "Historial" in dt_.text and "Alta" in dt_.text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/editar", data={"tipo": "desktop", "etiqueta": "PC-002", "marca": "HP", "modelo": "ProDesk 400", "serie": "SER-222", "imei": "", "linea": "", "fecha_compra": "", "garantia_hasta": "", "notas": "Recepción", "csrf": csrf_de(dt_.text)})
check("activos: editar los datos", "Datos del activo guardados" in r.text and "ProDesk 400" in r.text and "Recepción" in r.text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/asignar", data={"persona_id": str(_pa), "nota": "Con cargador", "csrf": csrf_de(r.text)})
check("activos: asignar pasa a «En uso» y registra la nota", "Activo asignado" in r.text and "En uso" in r.text and "Con cargador" in r.text)
check("activos: asignar a quien ya lo tiene se rechaza", "Ya está asignado" in c.post(f"/clientes/canal-sa/activos/{_aid}/asignar", data={"persona_id": str(_pa), "csrf": csrf_de(r.text)}).text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/asignar", data={"persona_id": str(_pl), "csrf": csrf_de(r.text)})
check("activos: reasignar deja el historial de la devolución y la nueva asignación", "Luis Gómez" in r.text and "Reasignado a otra persona" in r.text)
check("activos: la persona con activos no se puede desactivar", "devolvelos antes" in c.post(f"/clientes/canal-sa/personas/{_pl}/editar", data={"nombre": "Luis Gómez", "cargo": "", "area": "Ventas", "csrf": csrf_de(c.get("/clientes/canal-sa/personas").text)}).text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/devolver", data={"nota": "", "csrf": csrf_de(r.text)})
check("activos: devolver lo deja en stock sin persona", "Activo devuelto" in r.text and _conn.execute("SELECT estado, persona_id FROM activos WHERE id=?", (_aid,)).fetchone()["estado"] == "en_stock"
      and _conn.execute("SELECT persona_id FROM activos WHERE id=?", (_aid,)).fetchone()["persona_id"] is None)
check("activos: devolver algo sin asignar se rechaza", "no está asignado" in c.post(f"/clientes/canal-sa/activos/{_aid}/devolver", data={"nota": "", "csrf": csrf_de(r.text)}).text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/estado", data={"estado": "en_reparacion", "nota": "Pantalla rota", "csrf": csrf_de(r.text)})
check("activos: pasar a reparación", "Estado actualizado" in r.text and "En reparación" in r.text and "Pantalla rota" in r.text)
r = c.post(f"/clientes/canal-sa/activos/{_aid}/estado", data={"estado": "de_baja", "nota": "", "csrf": csrf_de(r.text)})
check("activos: dar de baja y no poder asignarlo", "De baja" in r.text and "dado de baja" in c.post(f"/clientes/canal-sa/activos/{_aid}/asignar", data={"persona_id": str(_pa), "csrf": csrf_de(r.text)}).text)
check("activos: el resumen no cuenta los dados de baja", _ac.resumen(_conn, _cid_)["total"] == 3 and _ac.resumen(_conn, _cid_)["bajas"] == 1)
# CSV
ex = c.get("/clientes/canal-sa/activos/exportar.csv")
check("activos: exporta a CSV con encabezados y datos", ex.status_code == 200 and "attachment" in ex.headers["content-disposition"] and ex.text.splitlines()[0].lstrip("\ufeff").startswith("tipo;etiqueta") and "NB-001" in ex.text and "Ana Pérez" in ex.text)
check("activos: ofrece una plantilla de ejemplo", "Notebook;NB-001" in c.get("/activos/plantilla.csv").text)
_csv = ("Tipo;Código;Marca;Modelo;Serie;Estado;Asignado a;Garantía\n"
        "Laptop;NB-100;Dell;Latitude;SN100;En uso;Ana Pérez;01/01/2030\n"
        "Pantalla;MON-100;LG;24MK;SN101;;Pedro Nuevo;\n"
        "Notebook;NB-001;Lenovo;E14;SN-DUP;;;\n"
        "Heladera;HEL-1;x;x;;;;\n"
        "Teléfono;CEL-100;Motorola;G42;SN102;En reparación;;\n"
        "Notebook;NB-101;Acer;Aspire;SN103;;;31/31/2030\n").encode("utf-8")
r = c.post("/clientes/canal-sa/activos/importar", data={"csrf": csrf_de(c.get("/clientes/canal-sa/activos").text)}, files={"archivo": ("lista.csv", _csv, "text/csv")})
check("activos: importa CSV (sinónimos, fechas dd/mm/aaaa, estado, asignación) y crea las personas que faltan",
      "Importación terminada" in r.text and "<b>3</b> activo(s) cargado(s)" in r.text and "1 persona(s) nueva(s)" in r.text and "1 omitido(s)" in r.text, r.text[r.text.find("Importación"):][:600])
check("activos: la importación informa las filas con error y no frena el resto", "Fila 5" in r.text and "desconocido" in r.text and "Fila 7" in r.text and "no es válida" in r.text)
check("activos: lo importado quedó correcto", _conn.execute("SELECT tipo, estado, garantia_hasta FROM activos WHERE etiqueta='NB-100'").fetchone()["garantia_hasta"] == "2030-01-01"
      and _conn.execute("SELECT estado FROM activos WHERE etiqueta='CEL-100'").fetchone()["estado"] == "en_reparacion"
      and _conn.execute("SELECT p.nombre FROM activos a JOIN client_contactos p ON p.id=a.persona_id WHERE a.etiqueta='MON-100'").fetchone()["nombre"] == "Pedro Nuevo")
check("activos: importar de nuevo el mismo archivo no duplica", "<b>0</b> activo(s) cargado(s)" in c.post("/clientes/canal-sa/activos/importar", data={"csrf": csrf_de(r.text)}, files={"archivo": ("lista.csv", _csv, "text/csv")}).text)
check("activos: archivo CP1252 (Excel) con acentos se lee", "<b>1</b>" in c.post("/clientes/canal-sa/activos/importar", data={"csrf": csrf_de(r.text)}, files={"archivo": ("x.csv", "tipo,etiqueta,modelo,asignado_a\nNotebook,NB-ÑU,Ñandú 5,Luis Gómez\n".encode("cp1252"), "text/csv")}).text)
check("activos: sin la columna tipo se explica", "Falta la columna" in c.post("/clientes/canal-sa/activos/importar", data={"csrf": csrf_de(r.text)}, files={"archivo": ("x.csv", b"marca;modelo\nA;B\n", "text/csv")}).text)
check("activos: archivo vacío rechazado", "no tiene filas" in c.post("/clientes/canal-sa/activos/importar", data={"csrf": csrf_de(r.text)}, files={"archivo": ("x.csv", b"tipo\n", "text/csv")}).text)
check("activos: CSRF inválido no importa", "Sesión expirada" in c.post("/clientes/canal-sa/activos/importar", data={"csrf": "falso"}, files={"archivo": ("x.csv", _csv, "text/csv")}).text)
# permisos
check("RBAC activos: el técnico ve y da de alta", ttec.get("/clientes/canal-sa/activos").status_code == 200 and "Activo agregado" in ttec.post("/clientes/canal-sa/activos/nuevo", data={"tipo": "monitor", "etiqueta": "MON-TEC", "marca": "", "modelo": "x", "serie": "", "imei": "", "linea": "", "fecha_compra": "", "garantia_hasta": "", "notas": "", "persona_id": "", "csrf": csrf_de(ttec.get("/clientes/canal-sa/activos/nuevo").text)}).text)
_at = _conn.execute("SELECT id FROM activos WHERE etiqueta='MON-TEC'").fetchone()["id"]
ttec.post(f"/clientes/canal-sa/activos/{_at}/eliminar", data={"csrf": csrf_de(ttec.get(f"/clientes/canal-sa/activos/{_at}").text)})
check("RBAC activos: el técnico NO puede eliminar y no ve el botón", _conn.execute("SELECT 1 FROM activos WHERE id=?", (_at,)).fetchone() is not None and "Eliminar activo" not in ttec.get(f"/clientes/canal-sa/activos/{_at}").text)
check("RBAC activos: el administrador elimina y se borra el historial", "Activo eliminado" in c.post(f"/clientes/canal-sa/activos/{_at}/eliminar", data={"csrf": csrf_de(c.get(f"/clientes/canal-sa/activos/{_at}").text)}).text
      and _conn.execute("SELECT 1 FROM activo_historial WHERE activo_id=?", (_at,)).fetchone() is None)
check("activos: la auditoría registra los movimientos", all(x in c.get("/auditoria").text for x in ("activos.creado", "activos.asignado", "activos.devuelto", "activos.estado", "activos.importado", "activos.eliminado", "activos.persona_creada")))
# portal (solo lectura, solo su empresa)
pa = pcs.get("/portal/activos")
check("portal: el cliente ve sus activos (sin los dados de baja) y a quién están asignados", pa.status_code == 200 and "NB-001" in pa.text and "Ana Pérez" in pa.text and "MON-100" in pa.text and "PC-002" not in pa.text, pa.text[-500:])
check("portal: no hay acciones de edición y no entra a la gestión", "Nuevo activo" not in pa.text and pcs.get("/clientes/canal-sa/activos").url.path != "/clientes/canal-sa/activos")
_ac.crear(_conn, _oc, "admin", {"tipo": "notebook", "etiqueta": "NB-AJENA"})
check("portal: no ve activos de otras empresas", "NB-AJENA" not in pcs.get("/portal/activos").text)
check("portal: filtra por tipo", "MON-100" in pcs.get("/portal/activos?tipo=monitor").text and "NB-001" not in pcs.get("/portal/activos?tipo=monitor").text)
check("activos: una persona con activos no se desactiva", "devolvelos antes" in c.post(f"/clientes/canal-sa/personas/{_pl}/editar", data={"nombre": "Luis Gómez", "cargo": "Vendedor", "area": "Ventas", "csrf": csrf_de(c.get("/clientes/canal-sa/personas").text)}).text)
_ps = _ac.crear_persona(_conn, _cid_, "admin", "Sin Activos")
c.post(f"/clientes/canal-sa/personas/{_ps}/editar", data={"nombre": "Sin Activos", "cargo": "", "area": "", "csrf": csrf_de(c.get("/clientes/canal-sa/personas").text)})
check("activos: una persona sin activos se desactiva y deja de ofrecerse para asignar", all(p["id"] != _ps for p in _ac.personas(_conn, _cid_)) and "Sin Activos" not in c.get("/clientes/canal-sa/activos/nuevo").text)

# RBAC: la Bandeja es de PM/admin
check("RBAC: el técnico no entra a la Bandeja", ttec.get("/bandeja").url.path != "/bandeja")
check("RBAC: el PM sí", tpm.get("/bandeja").url.path == "/bandeja")
check("RBAC: técnico no puede cargar dominios", ttec.post("/clientes/canal-sa/dominios", data={"dominio": "x.com.ar", "csrf": csrf_de(ttec.get("/").text)}).url.path != "/clientes/canal-sa" or
      _conn.execute("SELECT 1 FROM client_dominios WHERE dominio='x.com.ar'").fetchone() is None)
r = c.post("/clientes/canal-sa/dominios/%d/quitar" % _conn.execute("SELECT id FROM client_dominios").fetchone()["id"], data={"csrf": csrf_c()})
check("canales: se puede quitar un dominio", "@pruebasrl.com.ar" not in r.text.split("Dominios de correo")[1].split("Personas")[0])
_conn.close()

c.post("/logout", data={"csrf": csrf_de(c.get("/").text)})
check("logout: / vuelve a /login", c.get("/").url.path == "/login")

# 11) Re-login: camino de VERIFICACIÓN de TOTP + prueba negativa
html = c.get("/login").text
r = c.post("/login", data={"username": "admin", "password": "itier-demo-admin", "csrf": csrf_de(html)})
check("re-login va a verificar 2FA", r.url.path == "/login/2fa", r.url.path)
bad = c.post("/login/2fa", data={"code": "000000", "csrf": csrf_de(r.text)})
check("2FA incorrecto NO entra", bad.url.path == "/login/2fa", bad.url.path)
good = c.post("/login/2fa", data={"code": pyotp.TOTP(secret).now(), "csrf": csrf_de(bad.text)})
check("2FA correcto entra al tablero", good.url.path == "/" and "Tablero" in good.text, good.url.path)

# 12) CSRF inválido en una mutación NO crea nada
r = c.post("/clientes/nuevo", data={"nombre": "No Debe Existir", "notas": "", "csrf": "token-falso"})
check("CSRF inválido rechazado (vuelve al form)", r.url.path == "/clientes/nuevo", r.url.path)
check("CSRF inválido no creó el cliente", "No Debe Existir" not in c.get("/clientes").text)

# 13) Contraseña incorrecta no avanza
c.post("/logout", data={"csrf": csrf_de(c.get("/").text)})
html = c.get("/login").text
r = c.post("/login", data={"username": "admin", "password": "mala", "csrf": csrf_de(html)})
check("contraseña incorrecta NO avanza a 2FA", r.url.path == "/login", r.url.path)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
