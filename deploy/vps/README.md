# iTiers en el VPS (Fase 1 y 2)

Núcleo ITSM y observabilidad de iTiers sobre tu propio VPS, servido detrás de un
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
| `consola`       | `itier-console` (GHCR, construida en CI)  | `${CONSOLE_PORT:-8083}/tcp` (LAN) | Plano de control: opera GLPI+Zabbix por API (login 2FA). |

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
docker compose pull        # baja las imágenes (consola desde GHCR; el VPS NO compila)
docker compose up -d
docker compose ps
```

> La imagen de la consola se construye en CI y se publica en GHCR
> (`.github/workflows/build-console.yml`); el VPS solo hace `pull`. Si el paquete
> de GHCR es privado, autenticate una vez: `docker login ghcr.io` con un token con
> `read:packages`. Pineá el tag/digest en `CONSOLE_IMAGE` del `.env` (en la rama
> de trabajo, `:latest` no existe: usá el tag de la rama, ej.
> `…/itier-console:claude-agentic-ai-itsm-tipcii`).

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
#   ITIER_CONSOLE_PUBLIC_URL  → URL pública de la consola (para los instaladores)
#   ITIER_ZBX_TUNNEL_HOSTNAME + ITIER_CF_ACCESS_CLIENT_ID/SECRET → ver «Túnel a Zabbix» abajo
nano .env

docker compose pull consola && docker compose up -d consola   # baja la imagen de GHCR
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

## Soporte remoto: MeshCentral (una sola vez; sirve para todos los clientes)

**¿Ya tenés un MeshCentral (p. ej. `remoto.pymesenlinea.com.ar`)?** Usalo: no levantes el contenedor de este compose.
Saltá al paso 3 y cargá en el `.env` `ITIER_MESH_URL=https://remoto.pymesenlinea.com.ar` (si usás un *dominio* de MeshCentral
con ruta, ej. `/cuyopack`, ponelo en la URL), una cuenta de servicio **sin 2FA** con permiso para crear grupos, y verificá que
no tengas `lockAgentDownload` activado. Cada cliente de iTiers tendrá su grupo `itier-<slug>` en ese servidor.
Si no tenés uno, seguí los pasos 1-5: el compose levanta el servicio `meshcentral` con la imagen que publica
`build-meshcentral.yml` (GHCR; si es privada, `docker login ghcr.io`). Si querés NO levantarlo, `docker compose up -d` con
`--scale meshcentral=0`.

Cada equipo de un cliente lleva **GLPI Agent** (inventario) y **Mesh Agent** (soporte remoto y estado). Los dos hablan
HTTPS/443 hacia el VPS: no hace falta túnel, VPN ni equipo central en el cliente (ADR-0011).

1. **`.env`**: `MESH_HOSTNAME=itier-mesh.pymesenlinea.com.ar` (un solo nivel), `MESH_NEW_ACCOUNTS=true` (solo hoy).
   `docker compose pull meshcentral && docker compose up -d meshcentral`.
2. **Cloudflare** → tu túnel → *Public Hostname*: `itier-mesh.pymesenlinea.com.ar` → `HTTP` · `localhost:8886`.
   ⚠ **Sin** aplicación de Access: los agentes no pueden iniciar sesión. (Los WebSockets ya vienen habilitados.)
3. Entrá a `https://itier-mesh.pymesenlinea.com.ar` y creá tu cuenta (la primera es administradora; activale 2FA).
   Creá además una cuenta **de servicio** `itier-api` **sin 2FA** (la API por contraseña no soporta TOTP).
4. `.env`: `MESH_NEW_ACCOUNTS=false`, `ITIER_MESH_URL`, `ITIER_MESH_USER=itier-api`, `ITIER_MESH_PASSWORD`
   y `ITIER_MESH_SUPPORT_GROUP=SOPORTE` (ver «Técnicos de soporte» abajo).
   `docker compose up -d` (recrea la consola y MeshCentral).
   La config de MeshCentral se reescribe sola en cada arranque desde el `.env` (no hace falta borrar volúmenes).
   En el log tiene que aparecer `[iTiers] config escrita: host=itier-mesh… puerto=4430`.
5. En la consola, ficha del cliente → **Reintentar aprovisionamiento**: crea el grupo `itier-<slug>` en MeshCentral.
   Desde ahí, **Instaladores** ya incluye el Mesh Agent en el instalador de Windows.

### Técnicos de soporte (acceso a los equipos de todos los clientes)

Los grupos de equipos los crea la cuenta de servicio `itier-api` y en MeshCentral solo los ve su dueño y quien tenga acceso.
Por eso los técnicos se manejan con un **grupo de usuarios** (`SOPORTE`):

1. **Una vez**, dejá que la consola cree el grupo de usuarios `SOPORTE` (necesita que `itier-api` tenga el permiso de sitio
   «Administrar grupos de usuarios»), o creálo vos en Mesh → *Mis usuarios* → *Grupos de usuarios* → Nuevo: `SOPORTE`.
2. Cada vez que la consola crea o reprovisiona un cliente, le da al grupo `SOPORTE` acceso a su grupo de equipos
   (gestionar equipos, control remoto, terminal/consola, archivos, wake-on-lan y notas; **no** editar el grupo ni administrar usuarios).
3. **Alta de un técnico:** en Mesh, *Mis usuarios* → *Crear cuenta* (con 2FA) → abrí el grupo `SOPORTE` → *Agregar usuario*.
   Desde ese momento ve los equipos de todos los clientes, presentes y futuros. Para quitarle el acceso, sacalo del grupo.
4. Para los clientes que ya existían: «Reintentar aprovisionamiento» en su ficha vuelve a compartirlos con `SOPORTE`.

### Usuarios de la plataforma (técnicos, PM, administradores)

La consola tiene **Usuarios** (solo el rol Administrador). Un alta crea la **misma cuenta con la misma clave inicial** en la
consola, GLPI, Zabbix y MeshCentral, con el perfil equivalente al rol; la clave se genera, se muestra **una sola vez** y no se guarda.

| Rol | Consola | GLPI | Zabbix | MeshCentral |
|---|---|---|---|---|
| Administrador (full) | todo, incl. este ABM | Super-Admin | Super admin | administrador del sitio + grupo SOPORTE |
| PM | clientes, instaladores, descubrimiento, curación, auditoría | Supervisor | Admin (+ grupo «iTier Soporte») | sin crear grupos + SOPORTE |
| Técnico | tickets, equipos y conexión remota, ver clientes | Technician | User (+ grupo «iTier Soporte») | sin crear grupos + SOPORTE |

Permisos que necesitan las cuentas de servicio que usa la consola:
- **GLPI**: el usuario de la API (el del `ITIER_GLPI_USER_TOKEN`) con perfil **Super-Admin** para crear usuarios y asignar perfiles.
- **Zabbix**: el usuario/token de la API con rol **Super admin** (crea usuarios, roles y el grupo de usuarios).
- **Mesh**: `itier-api` con los permisos de sitio **«Gestionar usuarios»** y **«Grupos de usuarios»**; para crear otros *administradores* tiene que ser administrador completo.

Qué hace cada acción: **Reiniciar clave** pone una clave nueva en los tres (en Mesh la API no permite fijar la clave de otro usuario: se
**recrea la cuenta** y se vuelve a sumar a SOPORTE). **Dar de baja** deshabilita en la consola, GLPI (inactivo), Zabbix (grupo «Disabled») y
Mesh (cuenta bloqueada + fuera de SOPORTE); la baja y los cambios de rol rigen **al instante**, aunque tenga la sesión abierta.
**Eliminar** borra la cuenta en los cuatro. Cada sistema es independiente: si uno falla, el estado queda a la vista por sistema en la lista.

**Correo de bienvenida y manual.** Al dar de alta un usuario, con «Enviar por correo» tildado (requiere `ITIER_SMTP_HOST` y `ITIER_SMTP_FROM`)
el usuario recibe en el correo del formulario: sus accesos a cada sistema, el usuario y la clave temporal, su misión y responsabilidades según el
rol, las funciones que tiene en la consola y el **manual de operaciones de su perfil** (el técnico no recibe procedimientos de PM ni de
administrador). «Reiniciar clave» manda solo las credenciales nuevas. Si el SMTP falla, el alta igual se hace y la clave se muestra en pantalla.
El mismo manual está en la consola, menú **Manual** (el administrador puede ver el de cualquier rol). Se edita en
`consola/itier_console/services/manual.py`. ⚠ El correo lleva la clave inicial en claro: por eso es temporal, el correo le pide al usuario
cambiarla y borrarlo, y conviene un SMTP con TLS (`ITIER_SMTP_TLS=starttls|ssl`).

> ⚠ La clave solo es la misma **al principio**: la consola y Mesh piden cambiarla en el primer ingreso y cada sistema la maneja después por su cuenta.
> Cada usuario debería activar 2FA en las cuatro. Para un único inicio de sesión real habría que pasar a SSO (OIDC/SAML).

### Panel de equipos, alertas y conexión remota embebida

La consola tiene **Equipos** (menú): todos los equipos de todos los clientes con su conexión, **discos** (alerta desde
`ITIER_ALERTA_DISCO_PCT`, 90 % por defecto, por cada unidad), **antivirus ON/OFF/ninguno** y un botón **Conectar** que abre
el control remoto de MeshCentral dentro del panel (iframe).

- Discos y antivirus salen del **último inventario del GLPI Agent** (normalmente diario), no son en tiempo real; la
  conexión sale de Mesh en vivo. Hace falta que GLPI tenga los datos (`Item_Disk` e `ItemAntivirus` en la API REST).
- Para embeber, Mesh necesita permiso de la consola: el entrypoint lo escribe en `allowedFramingOrigins` desde
  `MESH_FRAME_ORIGINS` (por defecto `ITIER_CONSOLE_PUBLIC_URL`). Después del primer despliegue: `docker compose up -d meshcentral`.
- La primera vez cada técnico inicia sesión en Mesh dentro del recuadro; como consola y Mesh comparten dominio
  (`*.pymesenlinea.com.ar`) la sesión queda guardada. Cada técnico ve solo lo que el grupo `SOPORTE` le asigna.

> El instalador baja el agente de `/meshagents?id=4&meshid=…&installflags=2` sin login (mientras no actives
> `lockAgentDownload` en MeshCentral). El enlace lleva el id del grupo del cliente, que no es un secreto fuerte: solo
> permite instalar un agente en ese grupo. Para revocarlo, borrá el grupo.

## Canales de entrada: correo y WhatsApp → tickets

> **Todo se configura desde la consola, en _Canales_ (solo administrador):** la cuenta IMAP (servidor, puerto, cifrado,
> usuario, contraseña, carpeta) con botón «Guardar y probar conexión», y el **QR de WhatsApp** para vincular el número.
> Lo que se cargue ahí pisa al `.env` y se aplica sin reiniciar; la contraseña se guarda cifrada. Las variables `ITIER_IMAP_*`
> quedan solo como valores iniciales. Si el QR no aparece en la pantalla, también sale en `docker compose logs -f openwa`.

Varias personas de un cliente piden soporte; ya no hace falta que entren a la consola. Todo lo que llega queda en la
**Bandeja** (PM/administrador) y, si se reconoce al cliente, se convierte solo en un ticket de su entidad de GLPI.
Después el técnico lo toma («Tomar este caso») o el PM lo asigna.

**Cómo se reconoce al cliente** (ficha del cliente → *Canales de contacto*), en este orden:
1. **Persona** cargada (correo o WhatsApp exactos) → ese cliente.
2. **Dominio** de correo registrado (`@cuyopack.com.ar`) → ese cliente. No se aceptan dominios públicos (Gmail, Hotmail…):
   para ellos se carga a cada persona.
3. Si no coincide: queda en la Bandeja «sin cliente» y el PM lo asigna (con «Recordar a esta persona» la próxima vez entra directo).

**Correo con Microsoft 365 (incluye buzones compartidos).** Microsoft ya no permite IMAP con usuario y contraseña, y un buzón compartido no tiene contraseña.
En **Canales → Correo de soporte** elegí «Microsoft 365 (aplicación)»: la consola se autentica como aplicación (OAuth2/XOAUTH2) y lee el buzón. Preparación, una vez:
1. Entra ID → Registros de aplicaciones → nuevo registro (solo tu directorio); anotá el **ID de aplicación** y el **ID de directorio**.
2. Permisos de API → «Office 365 Exchange Online» → permisos de aplicación → `IMAP.AccessAsApp` → conceder consentimiento de administrador.
3. Certificados y secretos → nuevo secreto (vence: anotá la fecha).
4. Aplicaciones empresariales → tu app → copiá el **ID de objeto**; en PowerShell de Exchange Online:
   `New-ServicePrincipal -AppId <ID de aplicación> -ObjectId <ID de objeto>` y
   `Add-MailboxPermission -Identity soporte@tudominio -User <ID de objeto> -AccessRights FullAccess`.
5. En la consola: servidor `outlook.office365.com`, puerto 993, buzón `soporte@tudominio` y los tres datos de la aplicación → «Guardar y probar conexión».
Para **enviar** (confirmaciones y respuestas) la consola usa el SMTP configurado (`ITIER_SMTP_*`); en Microsoft 365 el envío como el buzón compartido necesita
SMTP AUTH habilitado y permiso «Enviar como» para el usuario que autentica: si Microsoft lo bloquea, se puede pasar el envío a Microsoft Graph.

**Correo (otros proveedores).** Creá una casilla (ej. `soporte@…`), completá `ITIER_IMAP_*` y `ITIER_SOPORTE_EMAIL`, y `docker compose up -d consola`.
La consola lee los no leídos cada minuto. Responder al correo de confirmación (el asunto lleva `[#número]`) suma el mensaje
al mismo ticket, pero solo si quien responde es del mismo cliente. Se ignoran autorrespuestas y `noreply`, y los correos que
fallan SPF/DKIM/DMARC van a «Revisar» en lugar de abrir ticket (pueden ser suplantación). Para que el SMTP de salida confirme
al cliente, configurá también `ITIER_SMTP_*`.

**WhatsApp (OpenWA v5).** Viene en el stack con una **imagen propia** (`deploy/openwa`, publicada en GHCR por GitHub Actions:
workflow `build-openwa`): la imagen oficial de OpenWA está abandonada y no arranca. Usa una API **no oficial**: WhatsApp puede
bloquear el número y viola sus términos. Usá un número **dedicado** de la empresa. Para operar sin ese riesgo, la alternativa es
la API oficial (WhatsApp Business Cloud API).
1. En el `.env`: `ITIER_WA_HOOK_TOKEN` y `ITIER_WA_API_KEY` (`openssl rand -hex 24` para cada una).
2. `git pull && docker compose pull && docker compose up -d`. Esperá ~1 minuto (arranca Chrome y carga WhatsApp Web).
3. En la consola → **Canales**: aparece el **QR**; escanealo con el celular del número de soporte
   (WhatsApp → Dispositivos vinculados). La sesión queda guardada en el volumen `openwa_data`.
4. El webhook va solo a `http://consola:8080/hooks/whatsapp/<token>`, y la consola contesta al cliente («abrimos el caso #N»)
   por `ITIER_WA_API_URL` (ya apunta a `openwa`). Es de mejor esfuerzo: si la respuesta falla, el ticket igual se crea.
   Si algo falla: `docker compose logs --tail=50 openwa` y `curl -s localhost:8002/health`.

Cada mensaje de un número conocido abre un ticket; los siguientes (hasta 24 h) se suman al mismo. Los grupos, mensajes
propios, estados y adjuntos se ignoran. Hay un tope de mensajes por remitente y hora (`ITIER_CANAL_LIMITE_HORA`).

## Microsoft 365: usuarios del tenant de cada cliente

En la **ficha del cliente → Microsoft 365** se conecta su tenant y se administran sus usuarios (alta, edición, licencias, reinicio de
clave, bloqueo y baja) por Microsoft Graph, sin entrar al Centro de administración. El correo en sí sigue pagándolo el cliente.

**Una sola vez (tu app, no la del cliente)** — en `entra.microsoft.com` con tu cuenta:
1. Aplicaciones → Registros de aplicaciones → **Nuevo registro**, tipo **«Cuentas en cualquier directorio de la organización» (multiempresa)**.
2. URI de redirección (Web): `https://<tu-consola>/m365/consentimiento`.
3. Permisos de API → Microsoft Graph → **permisos de aplicación**: `User.ReadWrite.All`, `User-PasswordProfile.ReadWrite.All`,
   `Organization.Read.All`. (Si un tenant exige más para reiniciar claves, sumá `Directory.ReadWrite.All`.)
4. Certificados y secretos → nuevo secreto (vence: anotá la fecha). En la consola, como administrador: **Aplicación de Microsoft 365**
   (`/m365/aplicacion`) → pegá el ID de aplicación y el secreto (se guarda cifrado).

**Por cada cliente:** ficha del cliente → **Conectar tenant**. Se abre Microsoft y el **administrador global del cliente** acepta
una vez. Si el tenant no se conecta enseguida, esperá unos minutos (Microsoft propaga el consentimiento) y usá «Verificar conexión».
El cliente puede revocar el acceso cuando quiera quitando la aplicación en su Entra ID.

Permisos en la consola: todos los operadores ven y gestionan usuarios (alta, editar, licencias, clave, bloqueo); **eliminar** es de
PM/administrador; conectar un tenant, de PM/administrador; la aplicación, solo del administrador. Las claves temporales se muestran
una sola vez y no se guardan ni se auditan. Los usuarios eliminados quedan 30 días en la papelera de Microsoft.

## Google Workspace: usuarios del dominio de cada cliente

Igual que Microsoft 365, en la **ficha del cliente → Google Workspace**: alta, edición, suspensión, reinicio de clave y baja de usuarios
(Admin SDK · Directory API). Usa **delegación en todo el dominio** con una cuenta de servicio tuya: es lo habitual para proveedores
de servicios y evita la verificación de app que Google exige para el OAuth de usuario con estos permisos.

**Una sola vez (tu Google Cloud):** proyecto → habilitar **Admin SDK API** → cuenta de servicio (sin roles) → clave **JSON**. En la consola,
como administrador: **Cuenta de servicio de Google** (`/gws/aplicacion`) → pegá el JSON (se guarda cifrado). La pantalla te muestra el
**ID de cliente** y los **permisos** que hay que autorizar en cada cliente (`admin.directory.user`, `admin.directory.domain.readonly`).

**Por cada cliente** (lo hace un super administrador suyo): `admin.google.com` → Seguridad → Controles de API → **Administrar la delegación
en todo el dominio** → Agregar el ID de cliente y los permisos. Después, en la consola: ficha del cliente → **Conectar dominio** con el correo
de un super administrador del cliente (conviene una cuenta dedicada, p. ej. `soporte-itier@…`); la consola actúa como esa cuenta.

La consola **no reinicia la clave, suspende ni elimina a administradores del dominio** (es la vía típica de toma de cuenta): eso se hace en la
Admin console. Las licencias se asignan según la configuración de licencias automáticas del dominio. Las claves temporales se muestran una
sola vez y no se guardan ni auditan. Permisos: ver/alta/editar/clave/suspender, todos los operadores; eliminar y conectar, PM y administrador;
la cuenta de servicio, solo el administrador.

## Túnel a Zabbix (una sola vez; sirve para todos los clientes)

Los `zabbix-proxy` de los clientes llegan al server por el Cloudflare Tunnel de
este VPS, sin exponer IP ni puerto (ADR-0010). Se configura una vez:

1. **Publicar el server en el túnel.** Zero Trust → Networks → Tunnels → (tu túnel)
   → *Public Hostnames* → Add: hostname `itier-zbx.<tu-dominio>` (un solo nivel),
   *Service* = `TCP` → `localhost:10051`. (Con túnel por `config.yml`: una regla
   `service: tcp://localhost:10051`.)
2. **Protegerlo con Access.** Zero Trust → Access → *Service Auth* → Create Service
   Token (guardá Client ID y Client Secret). Después Access → Applications → Add
   → *Self-hosted* con ese hostname, y una política con acción **Service Auth**
   que incluya ese service token. Sin esto el hostname quedaría abierto.
3. **Cerrar el 10051 público.** En `.env`: `ZBX_SERVER_BIND=127.0.0.1` y
   `docker compose up -d zabbix-server`.
4. **Cargarlo en la consola.** En `.env`: `ITIER_ZBX_TUNNEL_HOSTNAME`,
   `ITIER_CF_ACCESS_CLIENT_ID`, `ITIER_CF_ACCESS_CLIENT_SECRET`, y
   `docker compose up -d consola`.

Desde ahí, cada instalador de appliance que genere la consola ya trae el túnel
configurado. El nodo de malla `zabbix-proxy-gw` deja de ser necesario.
