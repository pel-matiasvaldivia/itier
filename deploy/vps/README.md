# iTier en el VPS (Fase 1 y 2)

Núcleo ITSM y observabilidad de iTier sobre tu propio VPS. **Nginx Proxy Manager
(NPM) corre en OTRO VPS de la misma LAN** y termina TLS. Corresponde a las Fases
1 y 2 del roadmap (`docs/06-roadmap.md`): poner en marcha GLPI y Zabbix y validar
el flujo antes de sumar la capa agéntica.

> **Alcance.** Esto es andamiaje de referencia, no un producto llave en mano.
> Repasá los puntos marcados con ⚠ antes de exponer nada a internet.

## Qué se levanta

| Servicio        | Imagen                                   | Publicado en el host              | Rol |
|-----------------|------------------------------------------|-----------------------------------|-----|
| `db`            | `postgres:16-alpine`                     | **nada** (solo red interna)       | Un motor, dos bases: `glpi` y `zabbix`. |
| `glpi`          | `glpi/glpi:11.0` (parametrizable)        | `${GLPI_HTTP_PORT:-8884}/tcp` (LAN) | CMDB, ITAM, tickets, catálogo. |
| `zabbix-web`    | `zabbix/zabbix-web-nginx-pgsql`          | `${ZBX_WEB_PORT:-8885}/tcp` (LAN)   | Frontend de Zabbix. |
| `zabbix-server` | `zabbix/zabbix-server-pgsql`             | `10051/tcp` (público)             | Recibe datos de los proxies de cada cliente. |
| `consola`       | `itier-console` (build local)            | `${CONSOLE_PORT:-8083}/tcp` (LAN) | Plano de control: opera GLPI+Zabbix por API (login 2FA). |

Como NPM está en otro VPS, **no comparte la red de Docker** con este stack: los
frontends **publican su puerto** en el host y NPM los alcanza por
`IP-LAN-de-este-VPS:puerto`. Atá esos puertos a la IP de la LAN con `WEB_BIND`.
La base de datos vive en una red `interna` (`internal: true`) y **no publica
ningún puerto**: nadie fuera del stack la alcanza.

## Requisitos

- Docker Engine + Compose v2 en este VPS.
- NPM funcionando en el otro VPS, con conectividad de red (LAN) hacia este.
- La IP de la LAN de **este** VPS (la que verá NPM), para `WEB_BIND`. Averiguala
  con `ip -4 addr` (buscá la interfaz de la red privada, ej. `10.0.0.5`).

## Puesta en marcha

```sh
cd deploy/vps
cp .env.example .env
# editá .env: contraseñas (openssl rand -base64 24), WEB_BIND (IP LAN de este
# VPS), puertos web, imagen de GLPI, TZ
nano .env

docker compose config      # valida el YAML y la interpolación de variables
docker compose up -d
docker compose ps
```

En el primer arranque, `init/10-init-zabbix.sh` crea el rol y la base
`zabbix`. **Solo corre con el volumen de datos vacío**: si ya inicializaste
Postgres sin ese script, creá la base a mano o recreá el volumen.

### Publicar en NPM (que está en el otro VPS)

En la UI de NPM, creá un *Proxy Host* por cada frontend (con su dominio y
certificado Let's Encrypt), apuntando a la **IP de la LAN de este VPS** y el
puerto publicado:

- GLPI → `http://<IP-LAN-de-este-VPS>:8884`
- Zabbix → `http://<IP-LAN-de-este-VPS>:8885`

(Reemplazá los puertos si cambiaste `GLPI_HTTP_PORT` / `ZBX_WEB_PORT`.)

**Firewall — importante.** Como estos puertos quedan escuchando en el host,
limitá el acceso a ellos **solo desde la IP del VPS de NPM**:

- Ideal: poné `WEB_BIND` en la IP privada de este VPS para que ni siquiera
  escuchen en la interfaz pública, **y** en el firewall permití 8884/8885 solo
  desde la IP LAN del VPS de NPM.
- El tráfico NPM→backend viaja por la LAN como HTTP plano. Si esa red no es de
  confianza (ej. red compartida del proveedor), tunelizala: un **WireGuard**
  entre ambos VPS y atá `WEB_BIND` a la IP de WireGuard.

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
a internet. Solo publica su propio puerto (`${CONSOLE_PORT:-8083}`) para que NPM
la alcance. Lo único manual es **generar los tokens de API** dentro de GLPI y
Zabbix y ponerlos en `.env`.

### 1) Token de API de GLPI

Entrá a GLPI (por NPM) como administrador:

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

Entrá a Zabbix (por NPM) como *Super admin*:

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
#   ITIER_PUBLIC_GLPI_URL     → URL pública de GLPI por NPM (para el bundle)
#   ITIER_ZBX_SERVER_HOST     → host público del server Zabbix (proxies de clientes)
nano .env

docker compose up -d --build     # ahora también construye la imagen de la consola
```

En NPM (otro VPS): *Proxy Host* → `http://<IP-LAN-de-este-VPS>:8083`, con su
dominio (ej. `panel.tu-dominio.com`) y SSL. Limitá ese puerto por firewall a la
IP de NPM, igual que los otros frontends.

### 4) Primer login y verificación

1. Entrá al dominio de la consola: usuario `admin` y la `ITIER_CONSOLE_ADMIN_PASSWORD`.
   Te va a pedir **cambiar la clave** y **enrolar el 2FA** (escaneás el QR).
2. En el **Tablero**, GLPI y Zabbix deben figurar **Operativo**. Si alguno dice
   *Sin conexión*, revisá `docker compose logs consola` y el token correspondiente.
3. Probá **Nuevo cliente**: debe crear la entidad en GLPI y el grupo + proxy en
   Zabbix, y mostrarte el **bundle de enrolamiento** para `deploy/cliente/`.

## ⚠ Puntos a verificar

- **Imagen de GLPI 11.** Es reciente (salió 2025-10). Confirmá contra su
  documentación: (1) que la imagen que fijes en `GLPI_IMAGE` soporte
  **PostgreSQL**, y (2) los nombres exactos de sus variables de entorno
  (`GLPI_DB_*`). Si la imagen elegida solo soporta MariaDB/MySQL, cambiá el
  motor o la imagen antes de desplegar. Fijá la imagen por **digest**
  (`glpi/glpi@sha256:...`) para builds reproducibles.
- **Instalación inicial de GLPI.** Puede requerir completar el asistente web la
  primera vez (a través de NPM) o un comando de consola. Revisá la doc de la
  imagen.
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
