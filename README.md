# iTier — Agentic AI para ITSM en PyMEs

**iTier** es una plataforma de gestión de servicios de IT (ITSM) asistida por agentes de IA,
diseñada para desplegarse **dentro de la infraestructura de cada cliente** (appliance físico o
máquina virtual) y operada de forma centralizada por el proveedor de servicios.

El objetivo: darle a una PyME de 10–250 puestos el nivel de control de IT que hoy solo
tienen las empresas con un equipo de IT dedicado y una suite ITSM cara — sin pedirle a la PyME
que aprenda ITIL, ni que contrate un ingeniero, ni que exponga sus datos a una nube ajena.

---

## Resumen ejecutivo

| Dimensión | Decisión |
|---|---|
| **Núcleo ITSM** | GLPI 11 (CMDB + ticketing + ITAM + catálogo de servicios), open source, API REST v2 con OAuth2 |
| **Descubrimiento** | GLPI Agent (inventario nativo) + SNMP/WinRM/SSH sin agente + Zabbix para eventos |
| **Razonamiento** | Claude API (`claude-opus-5` por defecto), fuera del borde — el edge no ejecuta LLM |
| **Superficie de herramientas** | MCP (Model Context Protocol) — cada capacidad del agente es una tool tipada con clase de permiso |
| **Remediación** | Ansible con playbooks versionados y firmados. **Nunca** shell libre generado por el modelo |
| **Conectividad** | Solo saliente (mTLS). El appliance no expone ningún puerto entrante |
| **Seguridad del agente** | OWASP Top 10 for Agentic Applications 2026 (ASI01–ASI10) + niveles de autonomía graduales |
| **Hardware** | Mini-PC x86 (appliance estándar) · VM (4 vCPU/8 GB) · Raspberry Pi 5 (**solo** como sonda remota) |

> ⚠️ **Corrección a la premisa inicial.** La Raspberry Pi 5 **no** es adecuada como appliance
> completo: el stack (GLPI + PostgreSQL + colectores + agente) supera cómodamente su presupuesto
> de RAM e I/O, y el ecosistema Docker ARM64 tiene huecos en imágenes de terceros. La Pi sí es
> excelente en un rol acotado: **sonda de sitio remoto** (descubrimiento + túnel, sin ITSM local).
> Ver [ADR-0007](docs/adr/ADR-0007-perfiles-de-hardware.md).

---

## Documentación

| Documento | Contenido |
|---|---|
| [01 · Análisis de mercado](docs/01-analisis-mercado.md) | Qué existe hoy, qué comprar, qué construir y por qué |
| [02 · Arquitectura](docs/02-arquitectura.md) | Planos, componentes, flujos de datos, degradación |
| [03 · Mapeo ITIL 4](docs/03-mapeo-itil4.md) | Prácticas ITIL 4 → capacidades concretas del agente |
| [04 · Seguridad y autonomía](docs/04-seguridad-y-autonomia.md) | Modelo de amenazas, ASI Top 10, niveles L0–L4 |
| [05 · Modelo de costos](docs/05-modelo-de-costos.md) | Costo real por cliente/mes y pricing sugerido |
| [06 · Roadmap](docs/06-roadmap.md) | Fases, hitos y criterios de salida |
| [ADRs](docs/adr/) | Decisiones de arquitectura, con alternativas descartadas |

## Estructura del repositorio

```
itier/
├── docs/               Arquitectura y decisiones (fuente de verdad del diseño)
│   └── adr/            Architecture Decision Records
└── reference/          Esqueleto de referencia — ilustra la arquitectura, no es producción
    ├── edge-agent/     Bucle del agente, cliente MCP, motor de políticas
    ├── mcp-servers/    Servidor MCP de GLPI (lectura + acción tipada)
    └── policy/         Catálogo de acciones y niveles de autonomía
```

## Estado

Fase de diseño. `reference/` contiene un esqueleto ejecutable que hace concreta la
arquitectura (bucle del agente, política de autonomía, superficie MCP), **no** una
implementación de producción. Ver [Roadmap](docs/06-roadmap.md).
