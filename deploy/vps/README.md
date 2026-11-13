# iTier en el VPS (Fase 1 y 2)

Núcleo ITSM y observabilidad de iTier sobre tu propio VPS, servido detrás de un
proxy que termina TLS. Camino recomendado: **Cloudflare Tunnel corriendo en este
mismo VPS** (ver [Detrás de Cloudflare Tunnel](#detrás-de-cloudflare-tunnel)).
Alternativa: NPM en otro VPS de la LAN. Corresponde a las Fases 1 y 2 del roadmap
(`docs/06-roadmap.md`): poner en marcha GLPI y Zabbix y validar el flujo antes de
sumar la capa agéntica.

> **Alcance.** Esto es andamiaje de referencia, no un producto llave en mano.
> Repasá los puntos marcados con ⚠ antes de exponer nada a internet.

## Qué se levanta

| Servicio        | Imagen                                   | Publicado en el host              | Rol |
|-----------------|------------------------------------------|-----------------------------------|-----|
| `db`            | `postgres:16-alpine`                     | **nada** (solo red interna)       | PostgreSQL de Zabbix. |
| `mariadb`       | `mariadb:11.4` (parametrizable)          | **nada** (solo red interna)       | MariaDB de GLPI (GLPI **no** soporta Postgres). |
| `glpi`          | `glpi/glpi:11.0` (parametrizable)        | `${GLPI_HTTP_PORT:-8884}/tcp` (LAN) | CMDB, ITAM, tickets, catálogo. |
| `zabbix-web`    | `zabbix/zabbix-web-nginx-pgsql`          | `${ZBX_WEB_PORT:-8885}/tcp` (LAN)   | Frontend de Zabbix. |
| `zabbix-server` | `zabbix/zabbix-server-pgsql`             | `10051/tcp` (público)             | Recibe datos de los proxies de cada cliente. |
| `consola`       | `itier-console` (build local)            | `${CONSOLE_PORT:-8083}/tcp` (LAN) | Plano de control: opera GLPI+Zabbix por API (login 2FA). |

Los frontends **publican su puerto** en el host y el proxy los alcanza por
`WEB_BIND:puerto`. Con Cloudflare Tunnel en este host, `WEB_BIND=127.0.0.1`
(solo loopback: nada queda expuesto en la red ni en internet). Las bases de datos
(Postgres y MariaDB) viven en una red `interna` (`internal: true`) y **no publican
ningún puerto**.
La consola habla con GLPI y Zabbix por esa red interna (nombres de servicio),
no por los puertos publicados.

## Requisitos

- Docker Engine + Compose v2 en este VPS.
- El proxy que termina TLS: **cloudflared** en este VPS (recomendado) o NPM en
  otro VPS. Ver [Detrás de Cloudflare Tunnel](#detrás-de-cloudflare-tunnel).
- Los hostnames (y DNS en Cloudflare) para la landing, la consola y GLPI.

## Puesta en marcha

```sh
cd deploy/vps
cp .env.example .env
# editá .env: contraseñas (openssl rand -base64 24), WEB_BIND (127.0.0.1 con
# cloudflared en el host), tokens de la consola, imagen de GLPI, TZ
nano .env

docker compose config      # valida el YAML y la interpolación de variables
docker compose up -d --build
docker compose ps
```

En el primer arranque, `init/10-init-zabbix.sh` crea el rol y la base
`zabbix`. **Solo corre con el volumen de datos vacío**: si ya inicializaste
Postgres sin ese script, creá la base a mano o recreá el volumen.

## Detrás de Cloudflare Tunnel

`cloudflared` corre en **este mismo VPS** y abre una conexión saliente a
Cloudflare: no hay que abrir ningún puerto entrante. TLS se termina en el borde
de Cloudflare y el túnel entrega el tráfico a los servicios locales por HTTP en
loopback. Por eso `WEB_BIND=127.0.0.1`: nada queda expuesto en la red del VPS.

### Hostnames

| Hostname | Servicio local | Qué sirve |
|---|---|---|
| `itier.pymesenlinea.com.ar` | `http://localhost:8082` | Landing pública (`landing/`) |
| `itier-panel.pymesenlinea.com.ar` | `http://localhost:8083` | **Consola / dashboard** |
| `itier-itsm.pymesenlinea.com.ar` | `http://localhost:8884` | GLPI (lo usan los clientes) |
| `itier-zabbix.pymesenlinea.com.ar` | `http://localhost:8885` | Zabbix web (opcional) |

GLPI necesita hostname propio porque los clientes (glpi-agent y el agente) lo
alcanzan por internet. Zabbix web es opcional: la consola ya opera Zabbix.

> **⚠ Usá subdominios de UN solo nivel.** El certificado gratis de Cloudflare
> (Universal SSL) cubre `pymesenlinea.com.ar` y `*.pymesenlinea.com.ar` — **un
> nivel**. Por eso `itier.pymesenlinea.com.ar` anda, pero un nombre anidado como
> `panel.itier.pymesenlinea.com.ar` (dos niveles) da
> **`ERR_SSL_VERSION_OR_CIPHER_MISMATCH`**: no existe cert para ese host y nunca
> se emite esperando. Por eso la consola/GLPI/Zabbix van como `itier-panel`,
> `itier-itsm`, `itier-zabbix` (un nivel). Si querés los nombres anidados,
> necesitás **Advanced Certificate Manager** (pago) con un cert para
> `*.itier.pymesenlinea.com.ar`.

### Ingress

Con `WEB_BIND=127.0.0.1` (y la landing con `LANDING_BIND=127.0.0.1`), cargá las
reglas del ejemplo [`cloudflared/config.example.yml`](cloudflared/config.example.yml):

- **Túnel por token (desde el panel):** Zero Trust → Networks → Tunnels → tu
  túnel → *Public Hostnames*. Una fila por hostname; *Service* = la URL
  `http://localhost:<puerto>` de la tabla.
- **Túnel local (`config.yml`):** copiá el ejemplo a `/etc/cloudflared/config.yml`
  y creá los DNS con `cloudflared tunnel route dns <túnel> <hostname>`.

La consola funciona bien detrás del túnel: Cloudflare le llega por HTTPS (cookies
`Secure`), uvicorn confía en `X-Forwarded-Proto`. **Accedé solo por el dominio
HTTPS** (`itier-panel.…`); si probás `http://IP:8083` directo, la cookie de sesión no
viaja y el login “no anda”.

> ⚠ El servidor Zabbix (`10051`) **no** pasa por el túnel (ver sección siguiente).

### Alternativa: NPM en otro VPS

Si en vez de Cloudflare usás NPM en otro VPS de la LAN: poné `WEB_BIND` en la IP
privada de este VPS, creá un *Proxy Host* por frontend apuntando a
`http://IP-LAN:puerto`, y limitá el firewall a la IP de NPM (o tunelizá con
WireGuard si la LAN no es de confianza).

### El puerto 10051 (proxies de los clientes)

Distinto de la web: el `zabbix-proxy` de cada cliente se conecta desde
**internet** hacia `tu-vps-público:10051`, así que este bind es **público**
(`ZBX_SERVER_BIND=0.0.0.0`). Protegelo:

- **Firewall del VPS:** permití 10051/tcp solo desde las IP públicas de tus
  clientes, o atalo a una **VPN** y que los proxies entren por ahí.
- **PSK/TLS:** Zabbix cifra proxy↔servidor; configuralo para no depender solo del
  firewall (ver README del cliente).

## Consola de operación (plano de control)

La consola (servicio `consola`) habla con GLPI y Zabbix **por la red interna del
stack** (`http://glpi` y `http://zabbix-web:8080`): no salen sus APIs a la LAN ni
a internet. Solo publica su propio puerto (`${CONSOLE_PORT:-8083}`, en loopback)
para que el túnel la alcance, y se sirve en `itier-panel.pymesenlinea.com.ar`.
Lo único manual es **generar los tokens de API** dentro de GLPI y Zabbix y
ponerlos en `.env`.

### 1) Token de API de GLPI

Entrá a GLPI (por su hostname, ej. `https://itier-itsm.pymesenlinea.com.ar`) como administrador:

1. **Configuración → General → pestaña API:** activá *Habilitar la API REST* y
   anotá la *URL de la API*. Asegurate de que exista un **cliente de API** con
   acceso (por defecto suele venir uno *full access from anywhere*); de ahí sale
   el **App-Token**.
2. Usá (o creá) un **usuario de servicio** con un perfil que pueda **crear
   entidades y tickets** (Super-Admin, o uno acotado equivalente). En sus
   **Preferencias → Tokens de acceso remoto** generá el **token de API personal**
   (el **User-Token**).
3. En `.env`: `ITIER_GLPI_APP_TOKEN=<App-Token>` y `ITIER_GLPI_USER_TOKEN=<User-Token>`.

> ⚠ GLPI 11 es reciente: los nombres de menús/campos pueden variar. Si la API
> nueva difiere, el único archivo a tocar es `consola/itier_console/adapters/glpi.py`.

### 2) Token de API de Zabbix

Entrá a Zabbix (por su hostname, ej. `https://itier-zabbix.pymesenlinea.com.ar`, o por la consola) como *Super admin*:

1. **Usuarios → Tokens de API → Crear token**, asignado a un usuario con permisos
   para **crear grupos de hosts y proxies** (*Super admin*). Copiá el token: se
   muestra **una sola vez**.
2. En `.env`: `ITIER_ZBX_TOKEN=<token>`.
   (Alternativa: `ITIER_ZBX_USER` + `ITIER_ZBX_PASSWORD` de un usuario admin.)

### 3) Resto del `.env` y arranque

```sh
cd deploy/vps
# En .env, además de los tokens:
#   ITIER_CONSOLE_SECRET      → openssl rand -hex 32
#   ITIER_CONSOLE_ADMIN_PASSWORD → clave del admin inicial
#   ITIER_PUBLIC_GLPI_URL     → URL pública de GLPI por el túnel (https://itier-itsm.pymesenlinea.com.ar)
#   ITIER_ZBX_SERVER_HOST     → host DIRECTO del server Zabbix (IP/VPN; NO Cloudflare)
nano .env

docker compose up -d --build     # ahora también construye la imagen de la consola
```

Publicá la consola en el túnel: hostname `itier-panel.pymesenlinea.com.ar` →
`http://localhost:8083` (ver [Detrás de Cloudflare Tunnel](#detrás-de-cloudflare-tunnel)).

### 4) Primer login y verificación

1. Entrá al dominio de la consola: usuario `admin` y la `ITIER_CONSOLE_ADMIN_PASSWORD`.
   Te va a pedir **cambiar la clave** y **enrolar el 2FA** (escaneás el QR).
2. En el **Tablero**, GLPI y Zabbix deben figurar **Operativo**. Si alguno dice
   *Sin conexión*, revisá `docker compose logs consola` y el token correspondiente.
3. Probá **Nuevo cliente**: debe crear la entidad en GLPI y el grupo + proxy en
   Zabbix, y mostrarte el **bundle de enrolamiento** para `deploy/cliente/`.

## ⚠ Puntos a verificar

- **Base de datos de GLPI: MariaDB (no Postgres).** GLPI 11 **no soporta
  PostgreSQL**; requiere MySQL 8.0+ / MariaDB 10.6+
  ([prerequisites](https://glpi-install.readthedocs.io/en/latest/prerequisites.html)).
  Por eso GLPI corre sobre el contenedor `mariadb` y Zabbix sobre `db` (Postgres).
  La imagen oficial `glpi/glpi` auto-instala cuando están las cinco `GLPI_DB_*`.
  Fijá las imágenes por **digest** para builds reproducibles.
- **Instalación inicial de GLPI.** Puede requerir completar el asistente web la
  primera vez (a través de su hostname) o un comando de consola. Revisá la doc de
  la imagen.
- **Hardening.** Este compose aplica `no-new-privileges` y límites de log. Para
  producción sumá backups del volumen `db_data`, rotación de credenciales y
  monitoreo del propio stack.

## Permisos de los volúmenes de GLPI

GLPI corre como usuario **no-root** dentro del contenedor. Los volúmenes
`glpi_files` y `glpi_config` nacen propiedad de `root`, así que GLPI no puede
crear su caché y falla con:

```
mkdir: cannot create directory '/var/glpi/files/_cache': Permission denied
```

El stack lo resuelve solo con el servicio **`glpi-init`**: corre antes que
`glpi`, como root y con la misma imagen, detecta el dueño correcto (el que la
imagen le da a `/var/glpi`) y se lo aplica a los volúmenes. No hay que hacer
nada: `docker compose up -d` ejecuta el arreglo (también sobre volúmenes ya
creados) y recién entonces levanta GLPI.

Si preferís arreglar los volúmenes **ya existentes** sin recrear nada, en una
sola línea:

```sh
docker compose run --rm --no-deps --user 0:0 --entrypoint sh glpi -c \
  'ref=$(stat -c "%u:%g" /var/glpi); [ "$ref" = "0:0" ] && ref=33:33; \
   echo "dueño=$ref"; chown -R "$ref" /var/glpi/files /var/glpi/config'
docker compose up -d
```

## Datos y backups

Todo el estado vive en volúmenes Docker: `db_data`, `glpi_files`,
`glpi_config`. Programá backup de al menos `db_data` (o `pg_dump` de ambas
bases) y de `glpi_files` (documentos adjuntos).

## Actualización

```sh
docker compose pull
docker compose up -d
```

Antes de subir versiones mayores de GLPI o Zabbix, leé sus notas de migración y
respaldá la base.
