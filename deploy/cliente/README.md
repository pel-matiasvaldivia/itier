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

| Servicio       | Red              | Sentido del tráfico | Rol |
|----------------|------------------|---------------------|-----|
| `itier-agent`  | puente, solo salida | → API de GLPI (VPS), → LLM | Capa agéntica (imagen propia). Sin entrada. |
| `zabbix-proxy` | `host`           | → servidor Zabbix del VPS (10051) | Recolecta la LAN y empuja al VPS. |
| `glpi-agent`   | `host`           | → GLPI del VPS      | Inventario y descubrimiento de la LAN. |

`zabbix-proxy` y `glpi-agent` usan `network_mode: host` porque necesitan ver la
LAN para monitorear e inventariar. El `itier-agent` **no**: queda aislado en una
red puente de solo salida y no expone nada.

## Requisitos de hardware

- **Appliance estándar:** mini-PC x86 (N100, 16 GB RAM, NVMe). Corre las tres
  piezas cómodo.
- **Perfil sonda (Raspberry Pi 5):** solo recolección (proxy + inventario +
  túnel). La imagen del agente es multi-arch (amd64/arm64), pero el ITSM pesado
  no vive acá. Ver ADR-0007.

## Puesta en marcha

```sh
cd deploy/cliente
cp .env.example .env
nano .env      # ITIER_TENANT, ITIER_GLPI_URL, credenciales, ZBX_SERVER_HOST

docker compose config
docker compose up -d
docker compose ps
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

El `zabbix-proxy` se conecta de forma saliente al VPS por 10051. En internet,
**no dependas solo del firewall**: activá PSK/TLS entre proxy y servidor.

1. Generá una clave:
   ```sh
   openssl rand -hex 32 > proxy.psk
   ```
2. Montala en el contenedor (agregá un volumen que la deje en
   `/var/lib/zabbix/enc/proxy.psk`) y completá en `.env`:
   ```
   ZBX_TLSCONNECT=psk
   ZBX_TLSPSKIDENTITY=itier-<tenant>
   ZBX_TLSPSKFILE=/var/lib/zabbix/enc/proxy.psk
   ```
3. Registrá el proxy y su PSK en el servidor Zabbix del VPS con la misma
   identidad y clave.

## ⚠ Puntos a verificar

- **glpi-agent.** Confirmá el nombre de la imagen y sus variables de entorno
  contra la documentación oficial de glpi-agent; ajustá `GLPI_AGENT_IMAGE` y las
  claves `GLPI_AGENT_*` según corresponda. Si preferís, reemplazalo por un
  agente instalado directamente en cada equipo del cliente.
- **Reachability para remediación (Fase 3+).** Cuando llegue el bucle agéntico
  con Ansible, el `itier-agent` necesitará alcanzar hosts de la LAN. Ahí se
  revisita la red del servicio (hoy es solo-salida a propósito). Ese cambio se
  documentará como su propio ADR.
- **Fijá imágenes por digest** para despliegues reproducibles.
