# iTiers lado del cliente (modo local)

Se despliega **una instancia por cliente**, en su propia LAN. Es la parte que,
según el diseño (`docs/02-arquitectura.md`, ADR-0005), vive en el borde y habla
con el VPS **solo de forma saliente**: ningún puerto entrante.

> **Fase actual.** En Fase 1 y 2 el `itier-agent` corre como **esqueleto**:
> valida su configuración y expone un health check, todavía no procesa eventos
> ni ejecuta remediaciones. Sirve para desplegar el contenedor, cablear la red
> contra el VPS y validar conectividad de punta a punta desde ya. El bucle
> agéntico llega en Fase 3 (`docs/06-roadmap.md`).

## Piezas

| Servicio          | Red                         | Sentido del tráfico | Rol |
|-------------------|-----------------------------|---------------------|-----|
| `zbx-tunnel`     | puente, solo salida          | → Cloudflare (HTTPS 443) | Túnel hasta el server Zabbix del VPS (Cloudflare Access). |
| `zabbix-proxy`    | puente; escucha 10051 en la LAN | → `zbx-tunnel` → servidor Zabbix del VPS | Recolecta la LAN y empuja al VPS. |
| `itier-agent`     | puente, solo salida          | → API de GLPI (VPS), → LLM (HTTPS) | Capa agéntica (imagen propia). Sin entrada. |
| `glpi-agent`      | `host`                       | → GLPI del VPS (HTTPS) | Inventario y descubrimiento de la LAN. |

**Todo sale por HTTPS (443) a Cloudflare; nada entra.**

- **GLPI y el LLM** son endpoints HTTPS con hostname público por el Cloudflare
  Tunnel del VPS (p. ej. `https://itier-itsm.tu-dominio`).
- **Zabbix (TCP 10051)** también va por el Cloudflare Tunnel: el VPS publica el
  server como un hostname TCP protegido por **Cloudflare Access**, y acá
  `zbx-tunnel` (`cloudflared access tcp`) abre el extremo local. Solo entra quien
  presenta el *service token*; ni la IP del VPS ni el 10051 quedan en internet.
  No necesita TUN ni privilegios de red, y es **la misma configuración para
  todos los clientes**. Ver [ADR-0010](../../docs/adr/ADR-0010-tunel-zabbix-cloudflare-access.md).

El `itier-agent` queda aislado en una red puente de solo salida y no expone nada.

## Requisitos de hardware

- **Appliance estándar:** mini-PC x86 (N100, 16 GB RAM, NVMe). Corre las cuatro
  piezas cómodo.
- **Perfil sonda (Raspberry Pi 5):** solo recolección (proxy + inventario +
  túnel). La imagen del agente es multi-arch (amd64/arm64), pero el ITSM pesado
  no vive acá. Ver ADR-0007.

## Requisitos del equipo

- **VM o equipo físico** con Debian/Ubuntu (x86_64 o arm64) y salida a internet.
  Es el camino soportado.
- **No recomendado: contenedor LXC** (p. ej. Proxmox). Docker dentro de LXC falla
  con `open sysctl net.ipv4.ip_unprivileged_port_start ... permission denied`.
  Si no hay alternativa, en el host Proxmox agregá a
  `/etc/pve/lxc/<id>.conf` y reiniciá el contenedor (⚠ relaja el aislamiento):

  ```
  features: nesting=1,keyctl=1
  lxc.apparmor.profile: unconfined
  lxc.mount.entry: /dev/null sys/module/apparmor/parameters/enabled none bind 0 0
  ```

## Instalación automática (recomendada)

Todo sale preconfigurado de la consola: **Clientes → \<cliente\> → Instaladores**.

1. **Generar comando de instalación** y ejecutarlo como root en el equipo del cliente:

   ```sh
   curl -fsSL https://<consola>/enrolar/<token>/install.sh | sudo bash
   ```

   El instalador trae embebidos el compose, el `.env` completo (incluidos el
   hostname del túnel y el service token, comunes a todos los clientes) y la PSK; instala
   Docker si falta, deja el stack en `/opt/itier` como servicio (`itier.service`) y
   le avisa a la consola cómo quedó. El enlace vence (24 h), tiene usos acotados y
   es revocable. Si el equipo no llega a la consola, descargá el `.sh` y copialo.
2. **Equipos Windows** — dos caminos:
   - **Lo instala el cliente:** desde la consola se le envía por correo un *enlace*
     (no un adjunto: los correos bloquean ejecutables). Descarga
     `itier-<tenant>-instalar.cmd`, doble clic, acepta el aviso de Windows y el
     resto es silencioso.
   - **Cero interacción:** el `.ps1` por GPO / Intune / RMM (corre como SYSTEM).

   Instala GLPI Agent y Zabbix Agent 2 como servicios. Los equipos caen solos en
   la entidad (regla de GLPI por tag `itier-<tenant>`) y en el grupo de Zabbix
   (autorregistro por metadata) que la consola crea en el alta.

Al terminar, el instalador **muestra el resultado** (servicios corriendo y
conexión con iTiers, en verde/rojo) y la ventana queda abierta hasta que se
presiona una tecla. También lo **reporta a la consola**: en *Instaladores →
Equipos instalados* se ve cada equipo, si instaló bien y si ya está transmitiendo.

### Seguridad de Windows: qué se puede y qué no

- **UAC no se puede saltear** (ni conviene): instalar servicios exige
  administrador. El `.cmd` se autoeleva y muestra **un** aviso de UAC; si el
  usuario no es administrador, Windows pide credenciales de uno. La única forma
  de no ver ningún aviso es desplegar como SYSTEM (GPO/Intune/RMM).
- **SmartScreen / marca de Internet:** al ser un archivo descargado y sin firma,
  Windows puede mostrar «Windows protegió su PC» → *Más información → Ejecutar de
  todas formas* (la página de descarga lo explica). Para eliminar ese aviso hace
  falta un **ejecutable firmado** con certificado de firma de código (p. ej. Azure
  Trusted Signing); es el paso pendiente para una experiencia tipo MeshCentral.
- El instalador no contiene secretos y deja registro en
  `%ProgramData%\iTier\instalador.log`.

Operación: `systemctl status itier` · `cd /opt/itier && docker compose logs -f`.
Para actualizar o reparar, volvé a correr el instalador (es idempotente).

## Puesta en marcha manual (avanzado)


```sh
cd deploy/cliente
cp .env.example .env
nano .env      # ITIER_TENANT, ITIER_GLPI_URL, ZBX_TUNNEL_HOSTNAME,
               # CF_ACCESS_CLIENT_ID/SECRET y las 3 de PSK

# PSK del proxy (de la consola). ./enc ya está montado en el proxy.
mkdir -p enc && printf '%s' '<PSK>' > enc/proxy.psk && chmod 644 enc/proxy.psk

docker compose config          # chequeo: sin "falta ..."
docker compose up -d
docker compose ps
docker compose logs zbx-tunnel zabbix-proxy   # el proxy debe conectar con el server
```

### Verificar el agente

El health del agente se expone en el puerto 8080 **dentro** del contenedor y lo
consume el `HEALTHCHECK` de la imagen. Para inspeccionarlo a mano:

```sh
docker compose exec itier-agent \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/readyz').read().decode())"
```

- `/healthz` → 200 mientras el proceso viva.
- `/readyz` → 200 si la config mínima está completa; 503 con la lista de lo que
  falta si no.

Si necesitás curificarlo desde la máquina, descomentá el bloque `ports:` del
compose y **atalo a `127.0.0.1`** (nunca a la LAN): preserva el "cero entrada".

## Inferencia: gateway vs. clave directa

- **Producción (objetivo):** `ITIER_LLM_GATEWAY` apunta al gateway del plano de
  control, que custodia la clave de Anthropic y aplica el tope de gasto. El
  appliance **nunca** ve la clave (ADR-0002, ADR-0004).
- **Modo local Fase 1/2:** se admite `ANTHROPIC_API_KEY` directa para validar
  rápido. Tratala como secreto: vive solo en el `.env` local, fuera de git.

Definí **al menos una** de las dos. Si faltan ambas, `/readyz` devuelve 503.

## Seguridad del proxy Zabbix

Dos capas independientes:

1. **Cloudflare Access (quién puede llegar al server).** El hostname TCP del
   server Zabbix solo acepta conexiones con el *service token*. Es común a los
   clientes; si se filtra, se rota en Cloudflare y se reinstala.
2. **PSK de Zabbix (quién es cada proxy).** Cada cliente tiene su PSK propia,
   generada por la consola en el alta. Un proxy sin su PSK no puede hablar con el
   server aunque atraviese el túnel; dar de baja un cliente es borrar su proxy.

## Descubrimiento de red (SNMP) e inventario

El `glpi-agent` es una **imagen propia** (`glpi-agent/`, publicada en GHCR; no hay
imagen oficial del agente). Corre como **daemon, solo saliente, sin httpd**, y hace
tres cosas que alimentan el CMDB de GLPI:

- **Inventario local** del appliance (y, con WinRM/SSH, de servidores).
- **Netdiscovery**: barre rangos de IP y encuentra lo que hay en la LAN.
- **Netinventory (SNMP)**: inventaría lo no gestionable — switches, routers,
  impresoras, APs, UPS.

**Dónde se configura qué:**

| Qué | Dónde | Por qué |
|---|---|---|
| Que el agente **corra** esas tareas y a qué endpoint enviar | **Acá** (`.env`: `GLPI_AGENT_TASKS`, `ITIER_GLPI_URL`) | Es config del appliance |
| **Rangos de IP** a barrer y **credenciales SNMP** | **En el server** (GLPI → GlpiInventory) | El agente pide su trabajo al contactar al server (encaja con "solo saliente") |

> ⚠ Los **rangos y las credenciales SNMP no van en el appliance**. El agente las
> recibe del server al contactarlo. Así no hay secretos SNMP regados por cada LAN y
> el scope de descubrimiento se maneja central.

**Paso del operador, una vez por cliente — desde la consola de iTiers:** en
*Clientes → \<cliente\> → Descubrimiento de red* cargás los rangos de IP y la
credencial SNMP (v1/v2c o v3); la consola los crea en GLPI, en la entidad del
cliente, y los deja listos para el barrido. (También se puede a mano en GLPI →
Administración → Inventario / GlpiInventory.) Ver `docs/07` §6.1.

El endpoint por defecto es `ITIER_GLPI_URL` + `/plugins/glpiinventory/`; si tu ruta
difiere, fijá `ITIER_GLPI_INVENTORY_URL` explícito.

## ⚠ Puntos a verificar

- **Túnel a Zabbix sin probar de punta a punta.** `cloudflared access tcp` con
  service token (`TUNNEL_SERVICE_TOKEN_ID/SECRET`) es el mecanismo estándar para
  TCP arbitrario, pero no se validó contra tu cuenta de Cloudflare. Si el proxy no
  conecta: `docker compose logs zbx-tunnel` y revisá la aplicación de Access.
- **El service token es secreto.** Vive solo en el `.env` (600) del appliance.
- **Plugin GlpiInventory en GLPI 11.** El descubrimiento SNMP de red se apoya en
  el plugin **GlpiInventory** del server. GLPI 11 es reciente: confirmá que el
  plugin esté instalado y sea compatible, y que el endpoint
  `…/plugins/glpiinventory/` responda, antes de depender de esta vía. El
  inventario local (del appliance y por WinRM/SSH) **no** necesita el plugin.
- **Alcance SNMP del agente.** El `glpi-agent` va en `network_mode: host`, así que
  ve la LAN para barrer por SNMP. Si querés que el appliance descubra una subred
  que no es la suya directa, revisá ruteo/VLAN. Para descubrir equipos detrás de
  WinRM/SSH, cargá esas credenciales también en GlpiInventory.
- **Reachability para remediación (Fase 3+).** Cuando llegue el bucle agéntico
  con Ansible, el `itier-agent` necesitará alcanzar hosts de la LAN. Ahí se
  revisita la red del servicio (hoy es solo-salida a propósito). Ese cambio se
  documentará como su propio ADR.
- **Fijá imágenes por digest** (incluida `CLOUDFLARED_IMAGE`) para despliegues reproducibles.

## Windows sin appliance Linux: «equipo central»

Zabbix **no tiene proxy para Windows** (solo agentes). Para clientes que no quieren un appliance Linux, la consola
genera un instalador con rol **central** (Instaladores → «Equipo central»), uno por cliente, en un equipo siempre
encendido:

- instala GLPI Agent (inventario + contacto con el plugin de inventario para el escaneo de red) y Zabbix Agent 2;
- instala `cloudflared` como tarea de arranque (SYSTEM) que abre el túnel `access tcp` a Zabbix (ADR-0010) y lo
  ofrece a la LAN en el puerto 10051 (regla de firewall Dominio/Privado);
- reporta su IP: los instaladores de los equipos comunes apuntan ahí (`SERVERACTIVE=<IP central>`).

Es un relay, no un proxy: los agentes reportan al servidor a través del central (sin PSK de proxy; el tramo LAN va
sin cifrar). Para monitoreo SNMP desde la LAN y buffer ante cortes, usar el appliance Linux.
El instalador central **contiene el service token**: solo se descarga desde la consola con sesión.

## Appliance en una VM de Proxmox (recomendado)

En el host Proxmox, con el enlace generado en la consola (Instaladores → «Generar comando de instalación»):

```sh
bash crear-vm-proxmox.sh --nombre <slug-del-cliente> \
     --url https://<consola>/enrolar/<TOKEN>/install.sh --ssh-key ~/.ssh/id_ed25519.pub
```

Crea una VM Debian 12 (2 vCPU, 2 GB, 16 GB) con cloud-init que corre el instalador en el primer arranque.
Pensado para correrse en el host Proxmox del cliente (o en uno propio y migrar la VM).

### Host KVM / libvirt

```sh
bash crear-vm-kvm.sh --nombre <slug-del-cliente> --bridge br0 \
     --url https://<consola>/enrolar/<TOKEN>/install.sh --ssh-key ~/.ssh/id_ed25519.pub
```

La VM debe ir en un bridge de la LAN del cliente (no en la red NAT `virbr0`): los equipos Windows llegan al proxy por
la IP LAN de la VM.
