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
check("alta de cliente → detalle", r.url.path == "/clientes/prueba-srl", r.url.path)
det = r.text
check("bundle tiene ITIER_TENANT", "ITIER_TENANT=prueba-srl" in det)
check("bundle tiene PSK identity", "ZBX_TLSPSKIDENTITY=itier-prueba-srl" in det)
check("bundle muestra PSK (64 hex)", bool(re.search(r'id="bundle-psk">[0-9a-f]{64}<', det)))

# 7) Agentes: panel + vincular
ag = c.get("/clientes/prueba-srl/agentes").text
check("agentes: lista hosts de demo", "SRV-DC01" in ag)
r = c.post("/clientes/prueba-srl/agentes/vincular",
           data={"hostid": "501", "host_label": "SRV-DC01", "csrf": csrf_de(ag)})
check("vincular host → ok", r.status_code == 200 and "vinculado" in r.text.lower())

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
