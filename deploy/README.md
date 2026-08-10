# Despliegue de iTier

Manifiestos de despliegue para la topología real de este proyecto: el núcleo en
un **VPS propio**, con **Nginx Proxy Manager (NPM) en otro VPS de la misma LAN**,
y el **agente en modo local** en la LAN de cada cliente.

```
                         INTERNET
                    ┌───────┴────────┐
             (dominios TLS)     (10051 de clientes)
                    │                │
   ┌────────────────▼─────┐   LAN    │
   │  VPS de NPM          │ privada  │
   │  Nginx Proxy Manager │────────┐ │
   │  (TLS Let's Encrypt)  │        │ │
   └──────────────────────┘        ▼ ▼
                        ┌───────────────────────────────────┐
                        │  VPS de iTier  (deploy/vps/)       │
        IP-LAN:8884 ───▶│  glpi         (frontend)           │
        IP-LAN:8885 ───▶│  zabbix-web   (frontend)           │
                        │  [db postgres]  red interna, sin    │
                        │                 puertos publicados  │
                        │  zabbix-server ──── 10051/tcp ──────┼──┐
                        └─────────────────────────────────────┘  │ saliente
                                   ▲                              │ (del cliente
                         API GLPI  │ (HTTPS, vía NPM)             │  al VPS)
                                   │                              │
                   ┌───────────────┴──────────────────┐          │
                   │  Cliente, modo local (deploy/cliente/)       │
                   │  itier-agent  ──▶ API GLPI + LLM  │          │
                   │                  (solo salida)    │          │
                   │  zabbix-proxy ────────────────────┼──────────┘
                   │  glpi-agent   ──▶ inventario GLPI │
                   │     (host network: ven la LAN)    │
                   └────────── LAN del cliente ────────┘
```

## Los dos lados

| Carpeta            | Dónde corre           | Qué levanta | Fase |
|--------------------|-----------------------|-------------|------|
| [`vps/`](vps/)     | Tu VPS (NPM en otro VPS de la LAN) | PostgreSQL + GLPI + Zabbix (server y web) | 1 y 2 |
| [`cliente/`](cliente/) | LAN de cada cliente | itier-agent (GHCR) + zabbix-proxy + glpi-agent | local |

Cada carpeta tiene su propio `README.md`, `docker-compose.yml` y `.env.example`.

## Principios que fija esta topología

- **El cliente no expone nada.** Todo el tráfico del lado del cliente es
  saliente: la API de GLPI (a través de NPM), el LLM y el servidor Zabbix. Cero
  puertos entrantes (ADR-0005).
- **NPM termina TLS, desde otro VPS.** Como NPM no comparte la red de Docker con
  el stack, los frontends **publican su puerto** en la LAN (atados a `WEB_BIND`) y
  NPM los alcanza por `IP-LAN:puerto`. Limitá esos puertos por firewall a la IP de
  NPM (o tunelizalos con WireGuard si la LAN no es de confianza).
- **La base de datos no ve internet ni se publica.** Vive en una red
  `internal: true` y no expone puertos: solo la alcanzan los contenedores del stack.
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

1. **VPS** primero: levantá GLPI y Zabbix, apuntá los *Proxy Host* de NPM (en el
   otro VPS) a `IP-LAN-del-VPS:8884` y `:8885`, hacé el asistente inicial de GLPI.
   Ver [`vps/README.md`](vps/README.md).
2. **Cliente** después: apuntá `ITIER_GLPI_URL` y `ZBX_SERVER_HOST` al VPS,
   levantá el proxy y el agente esqueleto, y verificá `/readyz`. Ver
   [`cliente/README.md`](cliente/README.md).

> Todo esto es andamiaje de referencia. Repasá los puntos ⚠ de cada README
> (imagen de GLPI 11, glpi-agent, hardening, secretos) antes de exponer nada.
