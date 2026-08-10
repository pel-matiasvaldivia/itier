# iTier en el VPS (Fase 1 y 2)

Núcleo ITSM y observabilidad de iTier sobre tu propio VPS, detrás de **Nginx
Proxy Manager (NPM)**. Corresponde a las Fases 1 y 2 del roadmap
(`docs/06-roadmap.md`): poner en marcha GLPI y Zabbix y validar el flujo antes
de sumar la capa agéntica.

> **Alcance.** Esto es andamiaje de referencia, no un producto llave en mano.
> Repasá los puntos marcados con ⚠ antes de exponer nada a internet.

## Qué se levanta

| Servicio        | Imagen                                   | Publicado                    | Rol |
|-----------------|------------------------------------------|------------------------------|-----|
| `db`            | `postgres:16-alpine`                     | nada (solo red interna)      | Un motor, dos bases: `glpi` y `zabbix`. |
| `glpi`          | `glpi/glpi:11.0` (parametrizable)        | vía NPM                      | CMDB, ITAM, tickets, catálogo. |
| `zabbix-server` | `zabbix/zabbix-server-pgsql`             | `10051/tcp` al host          | Recibe datos de los proxies de cada cliente. |
| `zabbix-web`    | `zabbix/zabbix-web-nginx-pgsql`          | vía NPM                      | Frontend de Zabbix. |

Los frontends (`glpi`, `zabbix-web`) **no publican 80/443**: se unen a la red
externa de NPM y este los alcanza por nombre. La base de datos vive en una red
`interna` marcada como `internal: true`, así que **nunca sale a internet**.

## Requisitos

- Docker Engine + Compose v2.
- NPM ya funcionando en el mismo host, con su red de Docker creada. Por defecto
  se asume que se llama `npm`:
  ```sh
  docker network ls | grep npm    # ¿existe?
  docker network create npm       # si no existe (y apuntá NPM a esa red)
  ```

## Puesta en marcha

```sh
cd deploy/vps
cp .env.example .env
# editá .env: contraseñas robustas (openssl rand -base64 24), imagen de GLPI, TZ
nano .env

docker compose config      # valida el YAML y la interpolación de variables
docker compose up -d
docker compose ps
```

En el primer arranque, `init/10-init-zabbix.sh` crea el rol y la base
`zabbix`. **Solo corre con el volumen de datos vacío**: si ya inicializaste
Postgres sin ese script, creá la base a mano o recreá el volumen.

### Publicar en NPM

En la UI de NPM, creá un *Proxy Host* por cada frontend (con su dominio y
certificado Let's Encrypt):

- GLPI → `http://glpi:80`
- Zabbix → `http://zabbix-web:8080`

Como comparten la red `npm`, NPM resuelve esos nombres directamente. No hace
falta publicar puertos en el host.

### El puerto 10051 (proxies de los clientes)

El `zabbix-proxy` que corre en la LAN de cada cliente abre una conexión
**saliente** hacia `tu-vps:10051`. Por eso es el único puerto publicado al host.
Protegelo:

- **Firewall del VPS:** permití 10051/tcp solo desde las IP públicas de tus
  clientes, o
- **VPN / red overlay:** atá `ZBX_SERVER_BIND` a la IP de la VPN y hacé que los
  proxies se conecten por ahí, o
- **Stream de NPM:** NPM puede proxyar TCP; publicá 10051 como *stream* en lugar
  de exponerlo directo.

Zabbix admite PSK/TLS entre proxy y servidor: configuralo para no depender solo
del firewall (ver README del cliente).

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
