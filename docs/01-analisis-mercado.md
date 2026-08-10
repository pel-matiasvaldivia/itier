# 01 · Análisis de mercado

Objetivo: decidir **qué comprar, qué integrar y qué construir** para iTier, en lugar de
reinventar piezas que ya son commodities.

---

## 1. El panorama de ITSM con IA (2026)

El mercado se partió en tres bloques, y ninguno atiende bien a la PyME con
infraestructura propia.

### 1.1 Suites enterprise con capa agéntica

| Producto | Capa de IA | Precio de lista | Por qué no sirve para PyME |
|---|---|---|---|
| **ServiceNow** + Now Assist | Agentes de resumen, next-best-action, razonamiento en tiers Advanced/Prime | Enterprise, cotización | Costo y complejidad de implementación fuera de escala. Pensado para consolidar IT+RRHH+CX |
| **Jira Service Management** + Rovo | Triage, sugerencia de resolución, AIOps de alertas (desde Premium) | USD 20 / agente / mes (Standard), USD 51,42 (Premium) | Modelo por agente. Fuerte si ya vivís en Atlassian; sin descubrimiento de red ni ITAM serio |
| **Freshservice** + Freddy AI | Freddy Agent (empleado), Copilot (técnico), Insights (dirección) | USD 19 / 49 / 95 / 119 por agente/mes | El tier con IA útil arranca en Pro (USD 95/agente). Multi-tenancy para MSP es débil |

**Lectura:** todos cobran **por agente/técnico**, que es exactamente el eje equivocado
para una PyME: la PyME tiene 0–2 técnicos y 40 dispositivos. El valor está en los
dispositivos, no en los asientos. Además, ninguno resuelve la restricción de que
los datos de inventario y los eventos se queden en la red del cliente.

### 1.2 RMM open source (mundo MSP)

| Producto | Fortaleza | Límite |
|---|---|---|
| **Tactical RMM** | Lo más completo del open source para el caso MSP: monitoreo, scripting, parches, agente en Go, integra MeshCentral | La jerarquía cliente/sitio existe pero no fue diseñada para decenas de tenants con reporting propio |
| **MeshCentral** (Apache 2.0) | Acceso remoto sólido: escritorio, terminal, transferencia, sin cliente del lado técnico. Corre en 1–2 GB de RAM | Es acceso remoto, no ITSM: no tiene CMDB, ni tickets, ni catálogo |

**Lectura:** RMM resuelve *ejecutar acciones en endpoints*, no *gestionar servicios*.
Es un complemento (capa de ejecución), no el núcleo.

### 1.3 ITSM/ITAM open source

| Producto | Rol natural |
|---|---|
| **GLPI** | El más denso en funcionalidad: helpdesk + CMDB + ITAM + catálogo de servicios + finanzas (contratos, compras, garantías) en una sola instalación. 20+ años de madurez. **Multi-entidad nativo** — multi-cliente sin hacks |
| **iTop** | Muy fuerte en CMDB y modelado ITIL; más rígido de personalizar, comunidad menor |
| **Zammad** | Excelente ticketing con UX moderna, pero es ticketing — no tiene CMDB ni ITAM |
| **NetBox** (Apache 2.0) | *Source of truth* de red: IPAM, DCIM, racks, cableado. Complementario, no sustituto |
| **Snipe-IT** | Solo ITAM. Bueno y simple, pero sin service desk ni CMDB relacional |

---

## 2. Piezas de descubrimiento y observabilidad

| Herramienta | Uso en iTier |
|---|---|
| **GLPI Agent** | Inventario nativo. Soporta remoto **sin agente** vía WinRM (Windows) y SSH (Linux/Unix), y descubrimiento SNMP barriendo rangos IP: switches, routers, impresoras, APs, teléfonos IP, UPS |
| **Zabbix** | Eventos y métricas: SNMP, agente, IPMI, JMX, checks HTTP, low-level discovery, templates, triggers. Modelo proxy encaja perfecto con el appliance |
| **NetBox** | Opcional en fase 2, para clientes con red compleja. Existe integración NetBox↔Zabbix madura |

**Lectura:** el descubrimiento es un problema resuelto. No construir nada acá.

---

## 3. Herramientas agénticas para operaciones

| Proyecto | Qué hace | Aplicabilidad |
|---|---|---|
| **HolmesGPT** (Robusta + Microsoft, CNCF Sandbox) | Agente de investigación de incidentes: bucle ReAct que recolecta contexto del stack de observabilidad y produce análisis de causa raíz. 30+ integraciones. Modo operador autónomo 24/7 | **Referencia de diseño muy fuerte.** Es lo más cercano a lo que iTier necesita, pero está centrado en Kubernetes/cloud-native. La PyME argentina tiene Windows Server, NAS y un firewall, no un cluster |
| **K8sGPT** (CNCF Sandbox) | Escáner basado en reglas que usa un LLM solo para explicar hallazgos | Patrón útil: **reglas deterministas primero, LLM para explicar y decidir** |
| **Aurora (Arvo)** | AI SRE open source | Mismo sesgo cloud-native |

**Lectura:** no hay un equivalente a HolmesGPT para el stack típico de PyME (Windows/
SMB/on-prem). **Ahí está el hueco de mercado.** El patrón arquitectónico sí es
reutilizable: bucle agéntico + herramientas de observabilidad + RCA.

---

## 4. Estándares de interoperabilidad

| Estándar | Estado (2026) | Rol en iTier |
|---|---|---|
| **MCP** (Model Context Protocol) | Creado por Anthropic (nov-2024), hoy de facto. >97M descargas mensuales de SDK, >10.000 servidores en producción. Spec 2026-07-28 agrega core stateless, Extensions, Tasks, MCP Apps, hardening de autorización | **Capa de herramientas del agente.** Toda capacidad se expone como tool MCP tipada |
| **A2A** (Agent2Agent) | Donado por Google a la Linux Foundation (jun-2025). +150 organizaciones, integrado en Azure AI Foundry / Copilot Studio y las tres nubes grandes. Spec 1.0 con Signed Agent Cards, multi-tenancy | **Fase 3.** Para que el agente de iTier hable con agentes de proveedores (ISP, fabricante de hardware, otro MSP) |
| **OpenTelemetry** | Estándar | Telemetría del propio agente (trazas del bucle, costo por decisión) |

---

## 5. Conclusión: comprar / integrar / construir

```
┌─ INTEGRAR (open source maduro, no tocar) ──────────────────────┐
│  GLPI 11 ......... CMDB, ITAM, tickets, catálogo, contratos    │
│  GLPI Agent ...... inventario con y sin agente, SNMP           │
│  Zabbix .......... eventos y métricas                          │
│  Ansible ......... ejecución idempotente de remediaciones      │
│  MeshCentral ..... acceso remoto asistido (opcional)           │
└────────────────────────────────────────────────────────────────┘
┌─ COMPRAR (servicio gestionado) ────────────────────────────────┐
│  Claude API ...... razonamiento (claude-opus-5 por defecto)    │
└────────────────────────────────────────────────────────────────┘
┌─ CONSTRUIR (acá está el producto y la ventaja) ────────────────┐
│  1. Edge Agent ........ bucle agéntico ITIL-aware sobre MCP    │
│  2. Motor de políticas  autonomía graduada por acción/CI/hora  │
│  3. Gateway MCP ....... superficie de herramientas tipada      │
│  4. Plano de control .. flota multi-tenant, aprobaciones, KB   │
│  5. Playbooks ......... catálogo curado de remediaciones PyME  │
└────────────────────────────────────────────────────────────────┘
```

**La ventaja defendible no es el LLM ni el ITSM** — ambos son commodities. Es:

1. **El catálogo de playbooks curado para el stack real de la PyME** (Windows Server,
   AD, M365, NAS, firewall, backup) con evidencia de éxito acumulada.
2. **El motor de autonomía graduada**: la capacidad de demostrarle a un cliente que el
   agente hace exactamente lo que se le permitió, ni más ni menos, con auditoría.
3. **El modelo de despliegue on-prem**: los datos no salen. Para el segmento
   PyME regulado (estudios contables, salud, legales) esto es el argumento de venta.

---

## Fuentes

- [AI-powered ITSM in 2026: vendor lineup, pricing, and a buyer's lens — eesel AI](https://www.eesel.ai/blog/ai-powered-itsm)
- [Comprehensive Open-Source IT Asset Management Solution — GLPI](https://www.glpi-project.org/en/cmdb-software/)
- [GLPI ITSM: Open Source IT Management with ITIL — Priceless Consulting](https://pricelessconsulting.com/en/solutions/glpi-itsm/)
- [High-Level API — GLPI Developer Documentation](https://glpi-developer-documentation.readthedocs.io/en/master/devapi/hlapi/index.html)
- [RESTful API (V2) — GLPI Help Center](https://help.glpi-project.org/documentation/modules/configuration/general/api/restful-api-v2)
- [Discover native GLPI inventory — GLPI Project](https://www.glpi-project.org/en/discover-native-glpi-inventory/)
- [SNMP inventory in GLPI: automatic network device discovery — ITčko](https://itcko.sk/en/enhance-asset-visibility-with-glpis-snmp-inventory-feature/)
- [NetBox and Zabbix – An Integration that Just Fits — Zabbix Blog](https://blog.zabbix.com/netbox-and-zabbix-an-integration-that-just-fits/31404/)
- [Open Source RMM Software: 12 Tools for MSPs in 2026 — OpenMSP](https://www.openmsp.ai/blog/open-source-rmm-software-for-msps)
- [MeshCentral: Open Source Remote Access in 2026 — OpenMSP](https://www.openmsp.ai/blog/meshcentral-guide)
- [Open-Source AI SRE: Aurora vs HolmesGPT vs K8sGPT (2026) — DEV](https://dev.to/siddharth_singh_409bd5267/open-source-ai-sre-aurora-vs-holmesgpt-vs-k8sgpt-2026-5g26)
- [From Alert to Root Cause: HolmesGPT in Production — Polarpoint](https://www.polarpoint.io/blog/2026/04/07/from-alert-to-root-cause-holmesgpt-in-production/)
- [The 2026-07-28 Specification — Model Context Protocol Blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [A2A Protocol Surpasses 150 Organizations… — Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)
- [Run GLPI with Docker — GLPI Project](https://www.glpi-project.org/en/run-glpi-with-docker/)
