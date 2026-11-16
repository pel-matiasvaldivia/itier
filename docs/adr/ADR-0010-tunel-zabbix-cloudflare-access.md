# ADR-0010 · Túnel a Zabbix por Cloudflare Tunnel + Access (reemplaza ADR-0009)

**Estado:** aceptado · reemplaza a [ADR-0009](ADR-0009-transporte-proxy-zabbix-cloudflare-mesh.md)

## Contexto

ADR-0009 llevaba el TCP 10051 del `zabbix-proxy` por la malla WARP de Cloudflare.
En el primer despliegue real apareció que ese camino no escala al modelo
"un instalador y listo":

- El token de un nodo de la malla identifica a **ese nodo**. El nodo
  `zabbix-proxy-gw` es la puerta del VPS; no sirve como credencial compartida de
  los clientes, y crear un nodo por cliente a mano contradice el enrolamiento
  automático.
- El nodo necesita `NET_ADMIN`, `/dev/net/tun` y `ip_forward`: no arranca en
  contenedores LXC y agrega superficie privilegiada en la LAN del cliente.
- Enrutar por WARP obliga a cuidar el split-tunnel para no romper el sondeo local.

## Decisión

El servidor Zabbix se publica como **hostname TCP en el Cloudflare Tunnel del
VPS** (el mismo túnel que ya publica GLPI y la consola), protegido por una
**aplicación de Cloudflare Access con política de Service Auth**. En el cliente,
un sidecar `zbx-tunnel` corre `cloudflared access tcp --hostname <host> --url
0.0.0.0:10051` con un *service token*, y el `zabbix-proxy` usa `zbx-tunnel:10051`
como server.

- La configuración del túnel (hostname + service token) es **común a todos los
  clientes**: vive en la consola (`ITIER_ZBX_TUNNEL_HOSTNAME`,
  `ITIER_CF_ACCESS_CLIENT_ID`, `ITIER_CF_ACCESS_CLIENT_SECRET`) y va embebida en
  cada instalador. No hay nada que crear en Cloudflare por cliente.
- La identidad **por cliente** la da la **PSK de Zabbix** (una por proxy,
  generada en el alta), de punta a punta dentro del túnel.

## Consecuencias

- (+) Alta de cliente 100 % automática; el appliance solo necesita salida 443.
- (+) Sin privilegios de red ni TUN: corre en VM, mini-PC o contenedor.
- (+) El 10051 del VPS se ata a loopback (`ZBX_SERVER_BIND=127.0.0.1`).
- (+) El proxy escucha en la LAN (10051) para los agentes de los equipos.
- (−) El service token es compartido: si se filtra hay que rotarlo y reinstalar
  los appliances. Mitigado porque sin la PSK propia no se puede suplantar a un
  cliente. Evolución posible: un service token por cliente vía API de Cloudflare.
- (−) Dependencia de Cloudflare en la ruta del monitoreo (igual que ADR-0009).
- ⚠ No validado todavía de punta a punta contra la cuenta real de Cloudflare.

## Configuración única en el VPS

Ver `deploy/vps/README.md` → «Túnel a Zabbix».
