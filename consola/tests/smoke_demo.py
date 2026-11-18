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
      pe.status_code == 200 and "PC-VENTAS-01" in pe.text and "Antivirus APAGADO" in pe.text and "<strong>OFF</strong>" in pe.text
      and "Disco C: al 94 %" in pe.text, pe.text[-300:])
check("equipos: botón Conectar lleva al control remoto embebido", "/remoto?n=PC-VENTAS-01" in pe.text and "Conectar" in pe.text)
check("equipos: filtro «solo con alertas» oculta los equipos sanos",
      "PC-OK" not in c.get("/equipos?alertas=1").text and "PC-VENTAS-01" in c.get("/equipos?alertas=1").text)
rr = c.get("/equipos/prueba-srl/NODO-DEMO%40abc%24123/remoto?n=PC-VENTAS-01")
check("remoto: embebe MeshCentral en un iframe dentro del panel",
      rr.status_code == 200 and "<iframe" in rr.text and "gotonode=NODO-DEMO@abc$123" in rr.text.replace("&amp;", "&")
      and "viewmode=11" in rr.text, rr.text[-300:])
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

# 10) Logout
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
