# iTier lado del cliente (modo local)

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
| `cloudflare-mesh` | puente + interfaz WARP       | → malla de Cloudflare | Nodo de la malla: ruta privada hasta el server Zabbix del VPS. |
| `zabbix-proxy`    | comparte la del nodo mesh    | → servidor Zabbix del VPS (10051) **por la malla** | Recolecta la LAN y empuja al VPS. |
| `itier-agent`     | puente, solo salida          | → API de GLPI (VPS), → LLM (HTTPS) | Capa agéntica (imagen propia). Sin entrada. |
| `glpi-agent`      | `host`                       | → GLPI del VPS (HTTPS) | Inventario y descubrimiento de la LAN. |

**Dos caminos de salida, a propósito:**

- **HTTP(S) → Cloudflare Tunnel.** GLPI y el LLM son endpoints HTTPS con hostname
  público (p. ej. `https://itier-itsm.tu-dominio`); `itier-agent` y `glpi-agent`
  los alcanzan así. No hay IP ni puerto expuestos del lado del cliente.
- **TCP 10051 → malla WARP de Cloudflare.** El `zabbix-proxy` habla TCP crudo con
  el servidor Zabbix, y eso **no** pasa por un túnel HTTP. En vez de abrir el
  10051 público del VPS, el `cloudflare-mesh` da una ruta **privada y cifrada**
  hasta el server (una IP de la malla, p. ej. `172.16.0.4`). El proxy comparte la
  red del nodo mesh (`network_mode: service:cloudflare-mesh`), así sale por WARP.
  Ver [ADR-0009](../../docs/adr/ADR-0009-transporte-proxy-zabbix-cloudflare-mesh.md).

El `itier-agent` queda aislado en una red puente de solo salida y no expone nada.

## Requisitos de hardware

- **Appliance estándar:** mini-PC x86 (N100, 16 GB RAM, NVMe). Corre las cuatro
  piezas cómodo.
- **Perfil sonda (Raspberry Pi 5):** solo recolección (proxy + inventario +
  túnel). La imagen del agente es multi-arch (amd64/arm64), pero el ITSM pesado
  no vive acá. Ver ADR-0007.

## Puesta en marcha

```sh
cd deploy/cliente
cp .env.example .env
nano .env      # pegá el bundle de la consola: ITIER_TENANT, ITIER_GLPI_URL,
               # MESH_NODE_TOKEN, ZBX_SERVER_HOST (IP del mesh) y las 3 de PSK

# PSK del proxy (del bundle). ./enc ya está montado en el proxy.
mkdir -p enc && printf '%s' '<PSK-del-bundle>' > enc/proxy.psk && chmod 600 enc/proxy.psk

docker compose config          # chequeo: sin "falta MESH_NODE_TOKEN / ITIER_TENANT / ..."
docker compose up -d           # las cuatro piezas (el proxy espera al nodo mesh)
docker compose ps
```

> Para levantar **solo** el proxy y su nodo de red: `docker compose up -d zabbix-proxy`
> (arrastra a `cloudflare-mesh` por la dependencia).

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

El `zabbix-proxy` ya **no** sale a un 10051 público: va por la malla WARP, que
cifra y aísla el transporte. Encima de eso, usamos **PSK** para que el servidor
**autentique** al proxy (defensa en profundidad: la malla protege la red, la PSK
la identidad). El 10051 del VPS puede quedar cerrado a internet.

**Camino normal (con la consola de iTier).** Al dar de alta el cliente, la
consola ya creó el proxy en el servidor Zabbix del VPS **con su PSK** y te armó
el *bundle de enrolamiento*. En el appliance solo tenés que:

1. Pegar el fragmento `.env` del bundle (ya trae `ZBX_TLSCONNECT=psk`,
   `ZBX_TLSPSKIDENTITY=itier-<tenant>` y `ZBX_TLSPSKFILE=/var/lib/zabbix/enc/proxy.psk`).
2. Guardar la PSK del bundle en `./enc/proxy.psk` (el compose ya monta `./enc`
   en `/var/lib/zabbix/enc`, en solo-lectura):
   ```sh
   mkdir -p enc && printf '%s' '<PSK-del-bundle>' > enc/proxy.psk && chmod 600 enc/proxy.psk
   ```

No hay que registrar nada más en el servidor: lo hizo la consola en el alta.

**Sin consola (a mano).** Generás la clave vos, la dejás en `./enc/proxy.psk`,
completás esas tres variables en `.env`, y registrás el proxy y su PSK en el
servidor Zabbix del VPS con la **misma identidad y clave**:
```sh
openssl rand -hex 32 > enc/proxy.psk && chmod 600 enc/proxy.psk
```

## ⚠ Puntos a verificar

- **El VPS tiene que estar en la MISMA malla.** `ZBX_SERVER_HOST` es la IP del
  servidor Zabbix **dentro** de la malla de Cloudflare. Si el VPS no está unido a
  la malla (o esa IP es otra), el proxy no llega. Lo configurás del lado de
  Cloudflare; el valor correcto viene en el bundle.
- **Split-tunnel de la malla (clave para monitorear la LAN).** Como el proxy sale
  por la interfaz WARP, la malla debe enrutar **solo** la subred del VPS por el
  túnel y dejar que el resto (la LAN privada del cliente, rangos RFC1918) salga
  **local**. Si no, el proxy intentaría sondear la LAN *a través* de Cloudflare y
  fallaría. Revisá los *Split Tunnels* en el perfil de WARP.
- **Agentes activos de Zabbix.** Con el proxy compartiendo la red del nodo mesh
  (ya no `host`), el sondeo **saliente** (SNMP, chequeos pasivos, ping) funciona
  por NAT; pero los equipos con *Zabbix agent (active)* que se conectan **hacia**
  el proxy no lo alcanzan sin publicar puertos. Para Fase 1/2 (validar
  proxy↔server y sondeo pasivo) no bloquea; si usás agentes activos, revisá la
  exposición del 10051 del proxy en la LAN.
- **`MESH_NODE_TOKEN` es secreto y por cliente.** Uno por nodo/cliente; vive solo
  en el `.env`, nunca en git. Si se filtra, rotalo en el panel de Cloudflare.
- **glpi-agent.** Confirmá el nombre de la imagen y sus variables de entorno
  contra la documentación oficial de glpi-agent; ajustá `GLPI_AGENT_IMAGE` y las
  claves `GLPI_AGENT_*` según corresponda. Si preferís, reemplazalo por un
  agente instalado directamente en cada equipo del cliente.
- **Reachability para remediación (Fase 3+).** Cuando llegue el bucle agéntico
  con Ansible, el `itier-agent` necesitará alcanzar hosts de la LAN. Ahí se
  revisita la red del servicio (hoy es solo-salida a propósito). Ese cambio se
  documentará como su propio ADR.
- **Fijá imágenes por digest** (incluida `MESH_IMAGE`) para despliegues reproducibles.
