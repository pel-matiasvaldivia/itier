# ADR-0009 · Transporte del proxy Zabbix por malla WARP de Cloudflare

**Estado:** aceptada · **Fecha:** 2026-11-13

## Contexto

El stack del VPS está publicado detrás de un **Cloudflare Tunnel** (hostnames
públicos para la consola, GLPI y la web de Zabbix). Eso resuelve el tráfico
**HTTP(S)**, pero el `zabbix-proxy` de cada cliente necesita hablar con el
servidor Zabbix por **TCP crudo en 10051**, y un túnel HTTP no transporta TCP
arbitrario.

Quedaban dos caminos para ese 10051:

1. **Exponer el 10051 del VPS a internet** (IP/puerto público + firewall por IP
   de origen). Simple, pero abre una superficie entrante en el servidor y obliga
   a mantener allowlists de IP de clientes que suelen tener IP dinámica.
2. **Una red overlay privada** entre los clientes y el VPS, por la que el 10051
   viaje sin tocar la internet pública.

El operador ya montó una **malla WARP de Cloudflare** (WARP Connector / "mesh")
y unió el VPS a ella, dándole al servidor Zabbix una IP privada de la malla
(p. ej. `172.16.0.4`).

## Decisión

**El `zabbix-proxy` del cliente sale por un nodo de la malla WARP, no por un
10051 público.** En `deploy/cliente/` se agrega un sidecar `cloudflare-mesh`
(imagen `cloudflare/mesh`) que levanta la interfaz WARP, y el proxy **comparte su
red** (`network_mode: service:cloudflare-mesh`). `ZBX_SERVER_HOST` apunta a la IP
privada del server dentro de la malla.

El tráfico HTTP(S) (GLPI, LLM) **sigue** por el Cloudflare Tunnel con hostname
público: no cambia. La malla es **solo** para el TCP del proxy.

La **PSK** del proxy se mantiene como segunda capa: la malla cifra y aísla la
red; la PSK **autentica la identidad** del proxy ante el servidor.

## Fundamentos

- **No se expone el servidor Zabbix.** El 10051 del VPS puede quedar cerrado a
  internet; deja de ser superficie de ataque y no hay allowlist de IP que
  mantener.
- **Un token por cliente, revocable.** Cada nodo de la malla usa su
  `MESH_NODE_TOKEN`; se rota o revoca desde Cloudflare sin tocar a los demás.
- **Reusa infraestructura que ya existe.** El operador ya depende de Cloudflare
  para el camino HTTP; no se suma un proveedor nuevo.
- **Despliegue turnkey.** El cliente arranca con `docker compose up -d`: el
  bundle de la consola trae todo el `.env` (falta solo pegar el token del nodo y
  la PSK en `./enc/proxy.psk`).

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **10051 público + firewall por IP** | Superficie entrante en el VPS; allowlists frágiles con IP de cliente dinámica; expone la versión de Zabbix a escaneo |
| **VPN site-to-site (WireGuard/IPsec) propia** | Más control, pero otra pieza que operar y monitorear en cada cliente; la malla ya está montada y cumple |
| **Reverse-tunnel casero del 10051 sobre el túnel HTTP** | Frágil y fuera de soporte; mete un hop HTTP en una ruta que es TCP por naturaleza |

## Implementación

- `cloudflare-mesh`: `cap_add: [NET_ADMIN, NET_RAW]`, `/dev/net/tun`,
  `ip_forward=1`, `SRCNAT_ENABLED=true`, volumen `mesh_data`. Aislado en su propio
  contenedor por las capacidades de red que necesita.
- `zabbix-proxy`: `network_mode: service:cloudflare-mesh`,
  `depends_on: [cloudflare-mesh]`, `ZBX_SERVER_HOST=<IP-mesh>`, PSK por `./enc`.
- La consola emite en el bundle `MESH_NODE_TOKEN=` (placeholder) y el
  `ZBX_SERVER_HOST` con la IP de la malla (`ITIER_ZBX_SERVER_HOST` del lado del
  VPS). El token del nodo lo genera el operador en Cloudflare, por cliente.

## Consecuencias

**Positivas**
- El servidor Zabbix no expone puerto a internet.
- Transporte cifrado y aislado, con identidad revocable por cliente.
- El cliente despliega con un solo `docker compose up -d`.

**Negativas / a vigilar**
- **Dependencia de Cloudflare en la ruta del monitoreo.** Si la malla cae, el
  proxy no reporta (los datos se encolan local y se reenvían al volver).
- **Split-tunnel obligatorio.** El perfil WARP debe enrutar **solo** la subred del
  VPS por el túnel y dejar salir local la LAN privada del cliente (RFC1918), o el
  proxy no podrá sondear la LAN.
- **Agentes activos de Zabbix.** Con el proxy fuera de `network_mode: host`, el
  sondeo saliente (SNMP, chequeos pasivos) va por NAT, pero los *agentes activos*
  que se conectan hacia el proxy requieren exponer el 10051 del proxy en la LAN.
  Se revisita cuando haga falta.

## Relación con ADR-0005

[ADR-0005](ADR-0005-conectividad-solo-saliente.md) descartó los "túneles de
terceros" como ruta de gestión del agente. Este ADR **no** reabre eso: el canal
de control del `itier-agent` sigue siendo saliente y propio. Lo que se acota es
el **transporte del proxy Zabbix** (telemetría), donde la malla reemplaza a un
10051 público — estrictamente mejor que la alternativa que de hecho competía. El
appliance sigue **sin puertos entrantes** desde internet.
