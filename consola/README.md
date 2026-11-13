# Consola de iTier (plano de control)

La web desde donde se **opera toda la plataforma** sin entrar a los dashboards de
GLPI ni de Zabbix. Es un **backend-for-frontend (BFF)**: no reemplaza a esos
sistemas, los orquesta por sus APIs. Corresponde al *plano de control* que el
roadmap (`docs/06`) menciona en todas las fases. Diseño en
[`docs/adr/ADR-0008`](../docs/adr/ADR-0008-plano-de-control-consola.md).

> Andamiaje de referencia, no producto llave en mano. Repasá los puntos ⚠ (sobre
> todo la API de GLPI 11) antes de exponer nada.

## Qué hace (v1)

- **Login con 2FA.** Usuarios propios (contraseña Argon2id) + segundo factor TOTP
  obligatorio (Google/Microsoft Authenticator, Aegis). Bloqueo por intentos,
  cookies firmadas httponly+secure+samesite, tokens CSRF en cada formulario.
- **Tablero.** Salud de GLPI y Zabbix y resumen de la flota de clientes.
- **Alta de cliente** (el flujo central). En un paso crea: la **entidad en GLPI**,
  el **grupo de hosts** y el **proxy con PSK** en Zabbix, y genera el **bundle de
  enrolamiento** (fragmento de `.env` + PSK) que se pega en `deploy/cliente/` del
  appliance. Los nombres calzan con `deploy/cliente/docker-compose.yml`.
- **Agentes de Zabbix.** Estado del proxy del cliente y de sus hosts; **vincular**
  un host al proxy y grupo del cliente.
- **Tickets de GLPI.** Listar, ver, crear y asignar. El Tablero muestra los
  recientes con técnico asignado.
- **Portal del cliente** (`/portal`). Superficie **separada y acotada por tenant**:
  un usuario de portal ve **solo su empresa** (sus tickets y equipos), nunca las
  rutas del operador ni datos de otros clientes. El operador crea los accesos
  desde el detalle del cliente. Ver [`docs/08`](../docs/08-modelo-operativo-y-negocio.md).
- **Auditoría.** Bitácora append-only encadenada por hash (toda acción que cambia
  estado queda sellada; una edición del pasado rompe la cadena — OWASP ASI10).

## Stack

FastAPI + Jinja2, renderizado en el servidor. Todo en Python (igual que el agente
de borde), un solo contenedor, sin build de Node. Estado en SQLite. Dependencias
mínimas y auditables: ver `pyproject.toml`.

## Probar ya, en modo demo (sin GLPI/Zabbix)

```sh
cd consola
python -m venv .venv && . .venv/bin/activate
pip install -e .

export ITIER_CONSOLE_DEMO=1
export ITIER_CONSOLE_COOKIE_SECURE=0      # para http://localhost
export ITIER_CONSOLE_DB=./consola.sqlite3
itier-console                              # http://127.0.0.1:8080
```

En demo se crea `admin` / `itier-demo-admin`. Al primer login vas a **enrolar el
2FA** (escaneás el QR y ponés el código). Los datos de GLPI/Zabbix son de ejemplo.

## Desplegar en producción (detrás de NPM)

> **Camino recomendado: integrada al stack del VPS.** La consola ya está cableada
> como el servicio `consola` en [`deploy/vps/`](../deploy/vps/): habla con GLPI y
> Zabbix por la red interna (`http://glpi`, `http://zabbix-web:8080`), sin exponer
> sus APIs. Seguí [`deploy/vps/README.md` → "Consola de operación"](../deploy/vps/README.md)
> (incluye cómo generar los tokens de API). Lo de abajo es para correrla
> **sola**, en un host aparte o en desarrollo.

NPM corre en **otro VPS de la LAN** (misma topología que el resto): la consola
publica su puerto y NPM lo alcanza por `IP-LAN:puerto`.

```sh
cd consola
cp .env.example .env
# Completá .env. Imprescindibles:
#   ITIER_CONSOLE_SECRET  → openssl rand -hex 32
#   ITIER_CONSOLE_ADMIN_PASSWORD
#   ITIER_GLPI_URL / ITIER_GLPI_APP_TOKEN / ITIER_GLPI_USER_TOKEN
#   ITIER_ZBX_URL / ITIER_ZBX_TOKEN  (o ITIER_ZBX_USER + ITIER_ZBX_PASSWORD)
#   ITIER_ZBX_SERVER_HOST, ITIER_PUBLIC_GLPI_URL  (para el bundle)
nano .env

docker compose up -d --build
```

En NPM (en el otro VPS): *Proxy Host* → `http://<IP-LAN-de-este-host>:8083`
(o el `CONSOLE_PORT` que fijes), con dominio y certificado. Pestaña **SSL**:
*Force SSL* + *HTTP/2*. **Firewall:** limitá ese puerto a la IP del VPS de NPM.

### Cómo llega la consola a GLPI y Zabbix

- **Opción A — puertos publicados (por defecto).** La consola los alcanza por el
  host: `ITIER_GLPI_URL=http://host.docker.internal:8884`,
  `ITIER_ZBX_URL=http://host.docker.internal:8885` (el compose ya mapea
  `host.docker.internal`).
- **Opción B — red interna del stack.** Descomentá el bloque `networks` del
  `docker-compose.yml` para unir la consola a `itier-vps_interna` y usá los
  nombres de servicio: `ITIER_GLPI_URL=http://glpi`,
  `ITIER_ZBX_URL=http://zabbix-web:8080`. Más limpio si está en el mismo VPS.

## Administrar operadores (CLI)

```sh
# En el contenedor:  docker compose exec consola itier-console-admin <cmd>
itier-console-admin add --user ana --admin     # operador; pide la clave; enrola 2FA al entrar
itier-console-admin add-cliente --user contacto@acme --client acme   # usuario de PORTAL
itier-console-admin passwd --user ana          # restablecer contraseña
itier-console-admin reset-2fa --user ana       # si perdió el segundo factor
itier-console-admin disable --user ana
itier-console-admin list
```

Los usuarios de **portal** también se crean desde la UI: en el detalle del
cliente, sección **"Usuarios del portal"**. Entran por `/portal` (ven solo su
empresa) y, como cualquier usuario, cambian la clave y enrolan 2FA al primer
ingreso.

## Seguridad

- **Secretos en reposo.** El SQLite (`/data`) guarda hashes de contraseña, secretos
  TOTP y **las PSK de los proxies**. Protegé el volumen (dueño del proceso, sin
  lectura de terceros) y respaldalo cifrado.
- **2FA obligatorio.** Ningún usuario opera sin enrolar su segundo factor.
- **Auditoría.** Revisá `/auditoria`; la cadena de hash se verifica en cada carga.
- **Hardening del contenedor.** `no-new-privileges`, FS de solo lectura + tmpfs,
  usuario no-root. Mantené la consola **solo** detrás de NPM, nunca expuesta
  directo a internet.

## ⚠ A verificar antes de producción

- **API de GLPI 11.** `adapters/glpi.py` usa el API REST clásico (`apirest.php`,
  App-Token + user_token). GLPI 11 es reciente: confirmá ruta, autenticación y
  campos de `Entity`/`Ticket`. Todo el acoplamiento está en ese archivo.
- **API de Zabbix 7.0.** `adapters/zabbix.py` usa `proxy.create` con `name` +
  `operating_mode` y PSK, y `host.get` con disponibilidad a nivel de interfaz.
  Confirmá contra tu versión exacta.
- **Rollback del alta.** Si un paso externo falla, el cliente queda con
  `estado=error` y lo ya creado, para reintentar/limpiar a mano. No hay
  compensación automática entre sistemas (es deliberado en v1).

## Estructura

```
consola/
├── itier_console/
│   ├── main.py            app factory, middleware, seed de admin
│   ├── config.py          configuración desde entorno
│   ├── db.py              esquema SQLite (usuarios, clientes, auditoría)
│   ├── security.py        Argon2id, TOTP, CSRF, lockout
│   ├── auth.py            rutas login / 2FA / enrolamiento / logout
│   ├── audit.py           bitácora encadenada por hash
│   ├── cli.py             administración de operadores
│   ├── adapters/          glpi.py, zabbix.py (API + modo demo)
│   ├── services/          onboarding, agents, tickets
│   ├── routes/            dashboard, clients, agents, tickets, audit
│   ├── templates/         Jinja2 (tema claro)
│   └── static/            app.css, app.js
├── Dockerfile  ·  docker-compose.yml  ·  .env.example  ·  pyproject.toml
```
