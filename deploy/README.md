# Despliegue de iTier

Manifiestos de despliegue para la topología real de este proyecto: el núcleo en
un **VPS propio** detrás de **Nginx Proxy Manager (NPM)**, y el **agente en modo
local** en la LAN de cada cliente.

```
                    INTERNET
                        │
        ┌───────────────┴───────────────────────────────┐
        │   VPS propio  (deploy/vps/)                    │
        │                                                │
        │   NPM ──▶ glpi        (Proxy Host, TLS)        │
        │    │  ──▶ zabbix-web  (Proxy Host, TLS)        │
        │    │                                           │
        │   [db postgres]  ← red interna (sin internet)  │
        │   [zabbix-server] ──────── 10051/tcp ──────────┼──┐
        └────────────────────────────────────────────────┘  │  saliente
                        ▲                                    │  (del cliente
              API GLPI  │ (HTTPS, saliente)                  │   al VPS)
                        │                                    │
        ┌───────────────┴────────────────────────────────┐  │
        │   Cliente, modo local  (deploy/cliente/)        │  │
        │                                                 │  │
        │   itier-agent   ──▶ API GLPI (VPS) + LLM        │  │
        │                     (solo salida, sin entrada)  │  │
        │   zabbix-proxy  ─────────────────────────────────┼─┘
        │   glpi-agent    ──▶ inventario a GLPI (VPS)      │
        │        (host network: descubren la LAN)          │
        └──────────────────── LAN del cliente ─────────────┘
```

## Los dos lados

| Carpeta            | Dónde corre           | Qué levanta | Fase |
|--------------------|-----------------------|-------------|------|
| [`vps/`](vps/)     | Tu VPS, detrás de NPM | PostgreSQL + GLPI + Zabbix (server y web) | 1 y 2 |
| [`cliente/`](cliente/) | LAN de cada cliente | itier-agent (GHCR) + zabbix-proxy + glpi-agent | local |

Cada carpeta tiene su propio `README.md`, `docker-compose.yml` y `.env.example`.

## Principios que fija esta topología

- **El cliente no expone nada.** Todo el tráfico del lado del cliente es
  saliente: la API de GLPI (a través de NPM), el LLM y el servidor Zabbix. Cero
  puertos entrantes (ADR-0005).
- **NPM termina TLS.** Los frontends del VPS no publican 80/443; se unen a la red
  de NPM y este los expone con Let's Encrypt.
- **La base de datos no ve internet.** Vive en una red `internal: true` del lado
  del VPS.
- **La imagen del agente sale de CI.** `.github/workflows/build-agent.yml`
  construye y publica `ghcr.io/pel-matiasvaldivia/itier-agent` multi-arch
  (amd64 + arm64). El cliente solo la consume.

## Nota sobre esta fase vs. el diseño de borde

El diseño de referencia (ADR-0002, `reference/docker-compose.yml`) ubica **datos
+ bucle agéntico + política + ejecución juntos en el borde**, dejando salir solo
la inferencia. Esta topología de Fases 1/2 **centraliza los datos en el VPS** y
deja en el cliente un agente que los consulta por API. Es un punto intermedio
deliberado:

- **Ventaja:** valida el producto con clientes reales sin fabricar ni enviar
  hardware; el cliente arranca con la "sonda" (proxy + inventario + agente
  esqueleto).
- **A revisar en Fase 3/4:** cuando el agente empiece a **actuar** (remediación
  con Ansible, niveles L3/L4), conviene reevaluar cuánto estado y ejecución baja
  al borde, por latencia, autonomía offline y superficie de datos. Ese
  movimiento se documentará en su propio ADR.

## Orden sugerido

1. **VPS** primero: levantá GLPI y Zabbix, publicalos en NPM, hacé el asistente
   inicial de GLPI. Ver [`vps/README.md`](vps/README.md).
2. **Cliente** después: apuntá `ITIER_GLPI_URL` y `ZBX_SERVER_HOST` al VPS,
   levantá el proxy y el agente esqueleto, y verificá `/readyz`. Ver
   [`cliente/README.md`](cliente/README.md).

> Todo esto es andamiaje de referencia. Repasá los puntos ⚠ de cada README
> (imagen de GLPI 11, glpi-agent, hardening, secretos) antes de exponer nada.
