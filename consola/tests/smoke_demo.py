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
check("bundle manual tiene PSK identity", "ZBX_TLSPSKIDENTITY=itier-prueba-srl" in det)
check("bundle manual muestra PSK (64 hex)", bool(re.search(r'id="bundle-psk">[0-9a-f]{64}<', det)))

# 6b) Enrolamiento automatizado: instaladores preconfigurados
import subprocess  # noqa: E402

INST = "/clientes/prueba-srl/instaladores"
pag = c.get(INST).text
check("instaladores: sin token mesh avisa qué falta", "falta el token del nodo mesh" in pag)
r = c.post(INST + "/enlace", data={"csrf": csrf_de(pag)})
check("instaladores: sin token mesh NO genera comando", "curl -fsSL" not in r.text)
r = c.post(INST + "/mesh-token", data={"mesh_token": "con espacios malos", "csrf": csrf_de(r.text)})
check("instaladores: token mesh inválido rechazado", "formato inválido" in r.text)
MESH = "eyJhIjoiZGVtbyJ9.tok-DEMO_1234567890"
r = c.post(INST + "/mesh-token", data={"mesh_token": MESH, "csrf": csrf_de(r.text)})
check("instaladores: token mesh guardado", "Token cargado" in r.text)
check("instaladores: la página NO muestra el token mesh", MESH not in r.text)

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
      "ITIER_TENANT=prueba-srl" in sh and f"MESH_NODE_TOKEN={MESH}" in sh and "ZBX_TLSCONNECT=psk" in sh)
check("install.sh: trae la PSK y el compose", bool(re.search(r"printf '%s' '[0-9a-f]{64}'", sh)) and "cloudflare-mesh:" in sh)
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
check("descargas exigen sesión", pub.get(INST + "/appliance.sh", follow_redirects=False).status_code == 303)
aud = c.get("/auditoria").text
check("auditoría registra el enrolamiento", "enrolamiento.instalador.descargado" in aud and "enrolamiento.appliance.reporte" in aud)
check("auditoría NO contiene el token mesh", MESH not in aud)

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
check("cliente: ve sus equipos", pc.get("/portal/equipos").status_code == 200)
check("cliente: /clientes (operador) lo manda al portal", pc.get("/clientes").url.path == "/portal")

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
