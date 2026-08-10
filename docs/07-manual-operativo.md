# 07 · Manual de operaciones y procedimientos

Manual de trabajo para el **equipo que opera iTier** (proveedor de servicio) sobre
la plataforma. Reúne, en forma de procedimientos accionables, cómo poner en marcha
un cliente, gestionar activos, incidentes, solicitudes, problemas y cambios, y cómo
operar la capa agéntica con seguridad.

| | |
|---|---|
| **Audiencia** | Operadores y administradores de plataforma de Pymes en Línea (NOC / mesa de servicio). Una guía breve para el cliente final está en el [§16](#16-procedimientos-de-cara-al-cliente). |
| **Alcance** | Operación diaria de la plataforma iTier: VPS (GLPI + Zabbix) y appliances/sondas en clientes. |
| **No cubre** | Instalación del VPS y del appliance paso a paso → ver [`deploy/`](../deploy/README.md). Diseño y decisiones → ver [ADRs](adr/). |
| **Base normativa** | ITIL 4 (ver [03 · Mapeo ITIL 4](03-mapeo-itil4.md)) · OWASP Agentic AI 2026 (ver [04 · Seguridad y autonomía](04-seguridad-y-autonomia.md)). |
| **Estado** | v1 · 2026. Documento vivo: se versiona en git; los cambios entran por revisión. |

> ⚠️ **Sobre las fases del producto.** Hoy (Fases 1–2) están operativos el
> inventario, el monitoreo y el ticketing asistido. La **remediación automática**
> con niveles L2–L4 (el lazo completo del agente) llega en **Fase 3** — ver
> [06 · Roadmap](06-roadmap.md). Los procedimientos marcados con **`[Fase 3+]`**
> describen el modelo operativo objetivo; los demás se ejecutan ya.

---

## Índice

1. [Cómo usar este manual](#1-cómo-usar-este-manual)
2. [Roles y responsabilidades](#2-roles-y-responsabilidades)
3. [Mapa operativo de la plataforma](#3-mapa-operativo-de-la-plataforma)
4. [Operación de la plataforma](#4-operación-de-la-plataforma)
5. [PROC-ONB · Alta de un cliente (onboarding)](#proc-onb--alta-de-un-cliente-onboarding)
6. [PROC-CMDB · Gestión de activos y configuración](#proc-cmdb--gestión-de-activos-y-configuración)
7. [PROC-EVT · Monitoreo y gestión de eventos](#proc-evt--monitoreo-y-gestión-de-eventos)
8. [PROC-INC · Gestión de incidentes](#proc-inc--gestión-de-incidentes)
9. [PROC-REQ · Gestión de solicitudes](#proc-req--gestión-de-solicitudes)
10. [PROC-PRB · Gestión de problemas](#proc-prb--gestión-de-problemas)
11. [PROC-CHG · Cambios y remediación con playbooks](#proc-chg--cambios-y-remediación-con-playbooks)
12. [PROC-AUT · Niveles de autonomía y aprobaciones](#proc-aut--niveles-de-autonomía-y-aprobaciones)
13. [PROC-SEG · Seguridad, auditoría y cortacircuitos](#proc-seg--seguridad-auditoría-y-cortacircuitos)
14. [PROC-CONT · Continuidad y modo degradado](#proc-cont--continuidad-y-modo-degradado)
15. [Indicadores y reportes (SLA / KPIs)](#15-indicadores-y-reportes-sla--kpis)
16. [Procedimientos de cara al cliente](#16-procedimientos-de-cara-al-cliente)
17. [PROC-OFF · Baja de un cliente (offboarding)](#proc-off--baja-de-un-cliente-offboarding)
- [Apéndices](#apéndices)

---

## 1. Cómo usar este manual

Cada procedimiento sigue la misma plantilla:

- **Objetivo** — qué logra.
- **Cuándo aplica** — el disparador.
- **Responsable** — el rol que lo ejecuta (ver [§2](#2-roles-y-responsabilidades)).
- **Pasos** — numerados, accionables.
- **Criterio de salida** — cómo se sabe que terminó bien.

**Convenciones**

- `CI` = elemento de configuración (activo gestionado en el CMDB).
- `tenant` = un cliente. Cada tenant tiene su appliance/sonda y su política.
- Los niveles de autonomía (L0–L4) se explican en [§12](#proc-aut--niveles-de-autonomía-y-aprobaciones).
- **`[Fase 3+]`** marca lo que aplica cuando esté activo el lazo de remediación.
- 🔒 marca un paso con implicancia de seguridad; ⏱ uno sujeto a SLA.

---

## 2. Roles y responsabilidades

| Rol | Quién | Responsabilidad principal |
|---|---|---|
| **Operador de plataforma** | Mesa de servicio / NOC | Atiende la cola de incidentes y aprobaciones, supervisa el monitoreo, comunica al cliente. |
| **Administrador de plataforma** | Referente técnico | Onboarding/offboarding, salud del VPS, actualizaciones, backups, catálogo de acciones y playbooks. |
| **Aprobador de cambios** | Admin + contacto del cliente | Aprueba playbooks nuevos y cambios de nivel de autonomía por tenant. |
| **Responsable de seguridad** | Rol asignado | Revisa la auditoría, gestiona secretos, responde a comportamiento anómalo del agente. |
| **Contacto técnico del cliente** | Del lado del cliente | Recibe notificaciones, aprueba acciones L2, coordina ventanas y accesos. |

> Matriz RACI resumida por proceso en el [Apéndice A](#apéndice-a--matriz-raci-resumida).

---

## 3. Mapa operativo de la plataforma

```
   VPS (Pymes en Línea)  ── detrás de NPM, TLS ──┐
   ├─ GLPI 11     → CMDB, tickets, ITAM, catálogo │  la operás desde acá
   ├─ Zabbix 7.0  → eventos y métricas            │
   └─ PostgreSQL  → datos (red interna)           │
                                                  │  10051/tcp (saliente del cliente)
   Cliente (uno por tenant) ──── solo salida ─────┘
   ├─ itier-agent  → capa agéntica (consulta GLPI, razona, actúa) [Fase 3+]
   ├─ zabbix-proxy → recolecta la LAN → VPS
   └─ glpi-agent   → inventario de la LAN → GLPI
```

- **Todo lo que hacés como operador** ocurre en GLPI y Zabbix del VPS, más el panel
  de autonomía (política del tenant).
- **El cliente no expone puertos**: el appliance abre conexiones salientes. Ver
  [ADR-0005](adr/ADR-0005-conectividad-solo-saliente.md).
- Detalle de despliegue: [`deploy/README.md`](../deploy/README.md).

---

## 4. Operación de la plataforma

### 4.1 Accesos y credenciales 🔒

| Sistema | Uso | Dónde |
|---|---|---|
| GLPI | Trabajo diario de ITSM | `https://itsm.<tu-dominio>` (vía NPM) |
| Zabbix | Monitoreo y eventos | `https://<zabbix>.<tu-dominio>` (vía NPM) |
| NPM | Reverse proxy / TLS | Panel de NPM |
| GHCR | Imagen del agente | `ghcr.io/pel-matiasvaldivia/itier-agent` |

Reglas: usuarios nominales (no compartidos), 2FA donde el sistema lo permita, y
**secretos fuera de git** (viven en los `.env` de cada host). Rotación mínima
semestral y ante cada baja de personal.

### 4.2 Puesta en marcha y actualización

- Instalación del VPS → [`deploy/vps/README.md`](../deploy/vps/README.md).
- Instalación del cliente → [`deploy/cliente/README.md`](../deploy/cliente/README.md).
- **Actualizar** un componente: `docker compose pull && docker compose up -d` en el
  host correspondiente. Antes de subir versiones mayores de GLPI/Zabbix, leer las
  notas de migración y respaldar la base (§4.3).

### 4.3 Backups y restauración

- **Qué respaldar** en el VPS: volumen `db_data` (o `pg_dump` de las bases `glpi` y
  `zabbix`) y `glpi_files` (adjuntos).
- **Frecuencia**: diaria, con retención mínima 14 días y una copia fuera del VPS.
- **Restauración**: probar la restauración al menos una vez por trimestre (un backup
  no verificado no es un backup).

### 4.4 Autovigilancia (self-monitoring)

Dar de alta en Zabbix el propio VPS y los contenedores del stack. Alertas mínimas:
disco del VPS, disponibilidad de GLPI/Zabbix, y **última conexión de cada
zabbix-proxy** (un proxy que dejó de reportar = un cliente potencialmente a ciegas).

---

## PROC-ONB · Alta de un cliente (onboarding)

**Objetivo.** Dejar un cliente nuevo monitoreado, inventariado y con su política de
autonomía definida.
**Cuándo aplica.** Cliente firmado, con fecha de instalación acordada.
**Responsable.** Administrador de plataforma.

### Pre-requisitos (checklist)

- [ ] Datos del cliente y su contacto técnico (nombre, WhatsApp, email).
- [ ] Alcance acordado: rango(s) de red, cantidad de puestos/servidores, plan
      contratado (`visibilidad` / `gestionado` / `autonomo`).
- [ ] Hardware definido: appliance mini-PC x86 o sonda Raspberry Pi (ver
      [ADR-0007](adr/ADR-0007-perfiles-de-hardware.md)).
- [ ] Ventanas de mantenimiento y lista preliminar de CIs intocables.

### Pasos

1. **Crear el tenant en GLPI.** Alta de la *entidad* del cliente (GLPI es
   multi-entidad nativo). Definir ubicaciones y el/los grupo(s) de soporte.
2. **Registrar el proxy en Zabbix.** Dar de alta un *Proxy* con nombre
   `itier-<tenant>` (debe coincidir con `ZBX_HOSTNAME` del cliente) y su PSK.
   🔒 Usar PSK/TLS, no texto plano — ver [`deploy/cliente/README.md`](../deploy/cliente/README.md).
3. **Preparar la política del tenant.** Partir de
   [`reference/policy/tenant-ejemplo.yaml`](../reference/policy/tenant-ejemplo.yaml) y completar:
   `plan`, `techo_global`, `zona_horaria`, `ventanas_mantenimiento`,
   `cis_intocables`, `notificaciones` y `presupuesto`. **Empezar conservador:**
   `techo_global: L1` (solo recomienda) para las primeras 2–4 semanas.
4. **Desplegar el appliance/sonda** en la LAN del cliente:
   `cd deploy/cliente && cp .env.example .env` → completar `ITIER_TENANT`,
   `ITIER_GLPI_URL`, `ZBX_SERVER_HOST` y credenciales → `docker compose up -d`.
5. **Verificar conectividad.** ⏱
   - `zabbix-proxy` aparece *activo* en Zabbix.
   - Salud del agente: `/readyz` responde `200` (ver README del cliente). Un `503`
     lista qué falta configurar.
6. **Primera sincronización de inventario.** Confirmar que `glpi-agent` cargó los CIs
   y ejecutar **PROC-CMDB §6.2** (curación y criticidad).
7. **Diseñar el monitoreo mínimo** (PROC-EVT §7.1): disponibilidad de hosts, disco,
   CPU/RAM, servicios críticos del cliente.
8. **Entrega.** Reunión con el contacto técnico: mostrar tablero, explicar niveles de
   autonomía, acordar el plan de subida de autonomía y validar CIs intocables.

### Criterio de salida

- Proxy activo, inventario cargado y curado, monitoreo base andando, política del
  tenant cargada y **acta de entrega** firmada por el cliente.

---

## PROC-CMDB · Gestión de activos y configuración

> ITIL 4: *Service Configuration Management* + *IT Asset Management*.

### 6.1 Cómo entra el inventario

`glpi-agent` (y descubrimiento SNMP/WinRM/SSH) alimenta el CMDB de GLPI de forma
automática. Regla: **no se cargan CIs a mano** salvo excepción documentada; la fuente
de verdad es el descubrimiento.

### 6.2 Curación y clasificación de criticidad 🔒 (paso clave)

La **criticidad de cada CI alimenta directamente el techo de autonomía** del agente
(ver [§12](#proc-aut--niveles-de-autonomía-y-aprobaciones)). Por eso este paso es de
seguridad, no cosmético.

1. Revisar los CIs descubiertos y descartar duplicados/fantasmas.
2. Asignar **criticidad** a cada CI según su impacto en el negocio:

   | Criticidad | Techo de autonomía | Ejemplos típicos |
   |---|---|---|
   | `critica` | **L2** (siempre pide aprobación) | Controlador de dominio, ERP, base de datos, firewall |
   | `alta` | L3 | Servidor de archivos, aplicación de línea de negocio |
   | `media` | L4 | Puestos de usuarios clave, impresoras de red |
   | `baja` | L4 | Periféricos, equipos no productivos |

   > Valores definidos en [`reference/policy/acciones.yaml`](../reference/policy/acciones.yaml) → `techos_por_criticidad`.
3. Marcar los **roles sensibles** (`domain_controller`, `firewall_perimetral`,
   `sistema_backup`): quedan sujetos al techo duro `infraestructura_critica` (L2)
   además de su criticidad.
4. Cargar los CIs intocables del tenant en `cis_intocables` (el agente es **L0
   permanente** sobre ellos).

### 6.3 Mantenimiento del CMDB

- Revisión mensual de altas/bajas y de la criticidad ante cambios del negocio.
- Gestión de **licencias y contratos** en GLPI: fechas de vencimiento con alerta
  anticipada (evita cortes por licencia vencida).

**Criterio de salida.** Todo CI productivo tiene criticidad asignada; los sensibles,
su rol; y no hay CIs "sin clasificar" en producción.

---

## PROC-EVT · Monitoreo y gestión de eventos

> ITIL 4: *Monitoring and Event Management*.

### 7.1 Diseño de umbrales

En Zabbix, definir *triggers* por CI/servicio. Clasificar por severidad:

| Severidad Zabbix | Significado | Acción esperada |
|---|---|---|
| Information / Warning | Ruido o tendencia | Se registra, no genera ticket |
| Average / High | Degradación real | Genera incidente (§8) |
| Disaster | Caída con impacto | Incidente P1 + escalamiento inmediato |

### 7.2 De evento a incidente

- Un evento **accionable** abre (o el agente abre, `tickets.create` es L4) un
  incidente en GLPI, evitando duplicados por *correlación* (mismo CI + mismo síntoma
  = un solo ticket).
- Eventos informativos **no** abren tickets.

### 7.3 Ventanas de mantenimiento

Antes de una intervención planificada, poner el CI/host en *maintenance* en Zabbix
para no generar falsos incidentes. Registrar inicio y fin.

**Criterio de salida.** Cada CI crítico tiene al menos un trigger de disponibilidad;
no hay tormentas de alertas duplicadas.

---

## PROC-INC · Gestión de incidentes

> ITIL 4: *Incident Management*. **Objetivo:** restaurar el servicio lo antes posible.

### 8.1 Ciclo de vida

```
Detección → Registro → Clasificación/Priorización → Diagnóstico
   → Resolución (manual o asistida por el agente) → Cierre → Verificación
```

### 8.2 Priorización (impacto × urgencia)

| | Urgencia alta | Urgencia media | Urgencia baja |
|---|---|---|---|
| **Impacto alto** | P1 | P2 | P3 |
| **Impacto medio** | P2 | P3 | P4 |
| **Impacto bajo** | P3 | P4 | P4 |

Asociar cada prioridad a su SLA (ver [Apéndice C](#apéndice-c--matriz-de-sla-sugerida)).

### 8.3 El lazo del agente **`[Fase 3+]`**

Ante un incidente, el `itier-agent`:

1. **Reúne evidencia** (herramientas `read`: métricas, logs acotados, CMDB, tickets
   similares). Sin efectos.
2. **Diagnostica** la causa probable y **propone** una remediación del catálogo.
3. **Verifica con `remediate.dry_run`** (Ansible `--check`) — convierte la propuesta
   en evidencia sin tocar nada.
4. **Actúa según el nivel efectivo** (ver §12): recomienda (L1), pide aprobación (L2),
   ejecuta y notifica (L3) o resuelve solo (L4).
5. Todo queda en la **cadena de auditoría** (§13).

### 8.4 Procedimiento del operador

1. **Tomar** el incidente de la cola (o la aprobación pendiente).
2. **Validar el diagnóstico** del agente contra la evidencia adjunta.
3. Según el nivel:
   - **L1 (recomienda):** decidir y ejecutar el playbook manualmente o aprobar su
     ejecución.
   - **L2 (aprobar-y-actuar):** revisar el `dry_run`, y **aprobar o rechazar** (ver
     PROC-AUT §12.4). ⏱ dentro del SLA de la prioridad.
   - **L3/L4:** el agente ya actuó — **supervisar** el resultado y la verificación.
4. **Comunicar** al cliente según su política de notificaciones.
5. **Cerrar** solo tras confirmar la *verificación* del playbook (ej.
   `uso_disco_menor_a_85_pct`). Registrar causa y solución.

### 8.5 Escalamiento

Escalar cuando: se vence el SLA, el impacto crece, el agente no propone solución con
confianza, o se dispara un **cortacircuito** (§13.3). Ruta: Operador → Administrador →
Responsable de seguridad (si hay sospecha de seguridad) → contacto del cliente.

**Criterio de salida.** Servicio restaurado y verificado, cliente informado, ticket
cerrado con causa y solución documentadas.

---

## PROC-REQ · Gestión de solicitudes

> ITIL 4: *Service Request Management*. Peticiones planificadas, no fallas.

1. La solicitud entra por el **catálogo de servicios** de GLPI (alta de usuario,
   nuevo equipo, instalación de software, acceso).
2. Se aprueba según corresponda (las que **otorgan acceso** caen bajo el techo duro
   `otorga_acceso` → **L2 como mínimo**, siempre con decisión humana).
3. Se cumple (manual o con un playbook estándar) y se cierra con conformidad del
   solicitante.

**Criterio de salida.** Solicitud cumplida, con aprobación registrada cuando aplica.

---

## PROC-PRB · Gestión de problemas

> ITIL 4: *Problem Management*. **Objetivo:** eliminar causas raíz, no solo síntomas.

1. **Detectar** patrones: incidentes recurrentes sobre el mismo CI/síntoma, o un P1
   que amerita análisis.
2. Abrir un **problema** en GLPI y vincular los incidentes relacionados.
3. **Análisis de causa raíz** — el agente ayuda cruzando histórico de tickets,
   métricas y cambios recientes.
4. Registrar la solución de fondo como **error conocido** (KEDB) y, si aplica,
   proponer un **cambio** (PROC-CHG) o un artículo de KB (`kb.propose_article`, que
   siempre requiere revisión humana antes de publicar).

**Criterio de salida.** Causa raíz identificada y documentada; incidentes recurrentes
cerrados o con solución permanente en curso.

---

## PROC-CHG · Cambios y remediación con playbooks

> ITIL 4: *Change Enablement*. En iTier, **toda actuación sobre infraestructura es un
> playbook de Ansible** del catálogo firmado — nunca shell libre generado por el
> modelo (ver [ADR-0004](adr/ADR-0004-ansible-para-remediacion.md) y
> [ADR-0003](adr/ADR-0003-mcp-capa-de-herramientas.md)).

### 11.1 Tipos de cambio

| Tipo | Descripción | Aprobación |
|---|---|---|
| **Estándar** | Playbook probado, reversible, bajo radio de impacto | Pre-aprobado; el nivel de autonomía decide si el agente lo ejecuta |
| **Normal** | Impacto o riesgo mayor | Requiere aprobación del cambio |
| **Urgente** | Ante un incidente en curso | Aprobación acelerada + revisión posterior |

### 11.2 El catálogo de playbooks

Cada playbook (en [`reference/policy/acciones.yaml`](../reference/policy/acciones.yaml) → `playbooks`)
declara: `nivel_base`, `reversible`, `verificacion`, `radio_explosion_max_cis` y
`evidencia` (ejecuciones/reversiones históricas). Reglas duras:

- Un playbook **irreversible nunca supera L2** (ej. `extender_volumen`,
  `reiniciar_servidor`).
- Un playbook que toca **infraestructura crítica** arrastra el techo duro
  correspondiente.

### 11.3 PROC-CHG-NEW · Incorporar un playbook nuevo 🔒

**Responsable.** Administrador + Aprobador de cambios.

1. Escribir el playbook **idempotente y reversible** (o marcarlo irreversible → tope
   L2). Definir su `verificacion` (cómo se prueba el éxito) y `radio_explosion`.
2. Probarlo en un entorno controlado y con `dry_run`.
3. Agregarlo al catálogo con `nivel_base` conservador y su `evidencia` inicial en 0.
4. **Revisión por pull request** (el catálogo se versiona y se firma; no se edita en
   una UI). Merge = firma.
5. Empezar en L1/L2 y **subir el nivel solo con evidencia acumulada** (ejecuciones
   exitosas, 0 reversiones), por PROC-AUT.

**Criterio de salida.** Playbook en el catálogo firmado, probado, con nivel inicial
conservador y verificación definida.

---

## PROC-AUT · Niveles de autonomía y aprobaciones

El corazón operativo de iTier. Detalle de diseño en
[04 · Seguridad y autonomía](04-seguridad-y-autonomia.md) y
[ADR-0006](adr/ADR-0006-autonomia-graduada.md).

### 12.1 Los cinco niveles

| Nivel | Nombre | Qué hace el agente |
|---|---|---|
| **L0** | Observar | Mira e inventaría. No actúa. |
| **L1** | Recomendar | Diagnostica y sugiere; decide el humano. |
| **L2** | Aprobar-y-actuar | Propone y ejecuta **tras aprobación** humana. |
| **L3** | Actuar-y-notificar | Ejecuta lo rutinario y avisa. |
| **L4** | Autónomo | Resuelve solo, para tareas de bajo riesgo. |

### 12.2 Cómo se calcula el nivel efectivo

Para cada acción, el nivel efectivo es el **mínimo** entre todos los topes, y después
se aplican los techos duros:

```
nivel_efectivo = min( nivel_base_de_la_acción,
                      techo_del_plan_del_tenant,   # visibilidad L1 | gestionado L3 | autonomo L4
                      techo_por_criticidad_del_CI, # critica L2 | alta L3 | media/baja L4
                      techo_por_ventana_horaria )  # laboral L2 | fuera_horario L3 | mantenimiento L4
   luego → aplicar techos_duros (nunca suben, solo bajan)
   luego → excepciones del tenant y cis_intocables (L0)
```

**Ejemplo real** (del motor de políticas): el playbook `rotar_logs` tiene
`nivel_base L4`, pero sobre un cliente en plan `gestionado` (techo L3), su nivel
efectivo baja a **L3**. El mismo playbook sobre un controlador de dominio cae a **L2**
por el techo duro `infraestructura_critica`. *La misma acción cambia de resultado
según el CI, la hora y el cliente.*

### 12.3 Techos duros (nunca se levantan)

`otorga_acceso`, `destruccion_de_datos`, `infraestructura_critica`,
`autoconfiguracion`, `impacto_financiero` → **todos topan en L2**. Ninguna cantidad de
evidencia los sube. Definidos en `acciones.yaml → techos_duros`.

### 12.4 PROC-AUT-APR · Atender una aprobación (L2) ⏱

**Responsable.** Operador (o contacto del cliente, según política).

1. Llega la notificación (WhatsApp/email según `notificaciones` del tenant).
2. Abrir la solicitud: muestra **acción propuesta, CI, diagnóstico, resultado del
   `dry_run` y nivel efectivo con su motivo**.
3. **Validar** que el diagnóstico y el radio de impacto sean razonables.
4. **Aprobar** o **rechazar** (con motivo). Si no se responde en
   `escalar_tras_minutos` (ej. 30), escala automáticamente.
5. La decisión queda en auditoría (§13).

### 12.5 PROC-AUT-LVL · Cambiar el nivel de autonomía de un tenant 🔒

**Responsable.** Administrador + Aprobador (contacto del cliente).

1. Justificar el cambio con evidencia (semanas de operación estable, KPIs).
2. Editar la política del tenant (`plan` / `techo_global` / `excepciones`).
3. Comunicar al cliente **qué implica** (qué pasará a resolverse solo).
4. Aplicar y registrar. **Regla de oro:** subir de a un nivel, con período de
   observación entre saltos.

> **Freno de mano del cliente.** Bajar `techo_global` a **L1** desactiva toda
> actuación automática al instante, sin llamar a soporte. Todo cliente debe saber
> cómo hacerlo.

---

## PROC-SEG · Seguridad, auditoría y cortacircuitos

> Marco: [04 · Seguridad y autonomía](04-seguridad-y-autonomia.md) · OWASP Agentic AI
> 2026 · principio de **mínima agencia**.

### 13.1 Revisión de la auditoría 🔒

Cada acción del agente encadena un hash con la anterior (cadena inviolable: evidencia
→ política → ejecución). **Rutina semanal:**

1. Verificar la **integridad de la cadena** de cada tenant (una alteración se detecta
   al instante).
2. Revisar acciones L3/L4 ejecutadas: ¿coincide lo actuado con lo aprobado por la
   política?
3. Investigar cualquier `❌` de verificación de integridad como incidente de seguridad.

### 13.2 Gestión de secretos 🔒

- Claves y contraseñas **solo en los `.env`** de cada host; nunca en git, tickets,
  prompts ni capturas.
- En producción, el appliance **no** tiene la clave del LLM: usa el gateway del plano
  de control ([ADR-0002](adr/ADR-0002-razonamiento-fuera-del-borde.md)). El
  `ANTHROPIC_API_KEY` directo es solo para el modo local de Fases 1–2.
- Rotación ante baja de personal o sospecha de exposición.

### 13.3 Cortacircuitos (freno automático)

El sistema se autolimita (definido en `acciones.yaml → cortacircuitos`):

| Límite | Valor de referencia |
|---|---|
| Acciones por hora por tenant | 20 |
| Acciones por hora por CI | 3 |
| Deshabilitar un playbook tras fallas consecutivas | 2 |
| Máx. CIs afectados sin aprobación | 5 |
| Abortar si repite la misma tool idéntica | 3 veces |

**Procedimiento ante disparo de un cortacircuito:**

1. El agente se **frena solo** y notifica.
2. El Operador **investiga la causa** (¿falla real, bucle, evento masivo?).
3. Si es seguro, rehabilitar; si no, escalar al Responsable de seguridad y dejar el
   tenant en L0/L1 hasta resolver.

### 13.4 Kill switch

Para detener toda actuación de un tenant de inmediato: bajar `techo_global` a **L1**
(o L0). Para detener el agente por completo: `docker compose stop itier-agent` en el
appliance. El monitoreo (Zabbix) sigue funcionando.

---

## PROC-CONT · Continuidad y modo degradado

**Objetivo.** Que una caída de la nube o del enlace no deje al cliente sin servicio ni
sin registro.

- **Se corta el enlace saliente:** el appliance encola eventos de forma durable y
  reintenta; al reconectar, drena la cola. El monitoreo local sigue.
- **No hay inferencia (nube/LLM caído o sin presupuesto):** el agente **degrada a L0**
  (según `presupuesto.al_alcanzar_tope`) — sigue observando y registrando, no actúa.
- **Se cae el VPS:** restaurar desde backup (§4.3); los proxies de los clientes
  reconectan solos y drenan lo encolado.

**Procedimiento de recuperación.** Confirmar servicio → verificar que cada proxy
reconectó → revisar la cola drenada → validar integridad de auditoría (§13.1) →
comunicar a los clientes afectados.

---

## 15. Indicadores y reportes (SLA / KPIs)

**KPIs mínimos por tenant (mensual):**

| Indicador | Qué mide |
|---|---|
| MTTR | Tiempo medio de resolución de incidentes |
| Cumplimiento de SLA | % de incidentes resueltos en término |
| Disponibilidad | % uptime de CIs críticos |
| Tasa de autorresolución **`[Fase 3+]`** | % de incidentes resueltos por el agente sin humano |
| Acciones por nivel | Distribución L1/L2/L3/L4 |
| Costo de inferencia | USD del mes vs. `tope_mensual_usd` (ver [05 · Costos](05-modelo-de-costos.md)) |

**Informe mensual al cliente:** resumen de incidentes, cambios ejecutados, estado del
inventario, disponibilidad y una recomendación (ej. "listo para subir a L3 en
servidores de archivos"). Plantilla en el [Apéndice D](#apéndice-d--plantilla-de-informe-mensual).

---

## 16. Procedimientos de cara al cliente

Guía breve para entregar al contacto técnico del cliente.

- **Pedir soporte / reportar una falla:** por el canal acordado (email a la casilla de
  soporte o portal de GLPI). Incluir qué, desde cuándo y a quién afecta.
- **Qué esperar:** acuse dentro del SLA, diagnóstico y resolución o plan de acción.
- **Aprobaciones (si el plan lo incluye):** ante una notificación de aprobación,
  revisar la acción propuesta y responder aprobando o rechazando.
- **Freno de mano:** cómo pedir (o hacer) la bajada a L1 para frenar toda automación.
- **Ventanas de mantenimiento:** cómo se avisan y coordinan.

---

## PROC-OFF · Baja de un cliente (offboarding)

**Responsable.** Administrador.

1. Acordar fecha y **exportar los datos** del cliente (inventario, histórico de
   tickets) para entregárselos.
2. `docker compose down` en el appliance/sonda; retirar o reinicializar el hardware.
3. En el VPS: deshabilitar el proxy en Zabbix y **archivar** (no borrar de inmediato)
   la entidad en GLPI, según retención acordada.
4. 🔒 **Revocar credenciales**: PSK del proxy, accesos, y el `ANTHROPIC_API_KEY` o el
   certificado del tenant en el gateway.
5. Conservar la **auditoría** por el período legal/contractual antes de purgar.

**Criterio de salida.** Cliente sin servicios activos, datos entregados, credenciales
revocadas, auditoría conservada.

---

## Apéndices

### Apéndice A · Matriz RACI resumida

`R`=Responsable · `A`=Aprueba · `C`=Consultado · `I`=Informado

| Proceso | Operador | Administrador | Aprob. cambios | Seguridad | Cliente |
|---|---|---|---|---|---|
| Onboarding (ONB) | C | R | A | C | A |
| Incidentes (INC) | R | C | – | I* | I |
| Solicitudes (REQ) | R | C | A** | – | A |
| Cambios/playbooks (CHG) | C | R | A | C | I |
| Autonomía (AUT) | R (aprob. L2) | R (nivel) | A | C | A |
| Seguridad/auditoría (SEG) | C | C | – | R | I |

\* si hay sospecha de seguridad · ** si otorga acceso

### Apéndice B · Glosario

- **CI** — elemento de configuración (activo gestionado).
- **CMDB** — base de datos de configuración (los CIs y sus relaciones).
- **Tenant** — un cliente y su instancia.
- **Playbook** — procedimiento de remediación en Ansible, versionado y firmado.
- **Nivel de autonomía (L0–L4)** — cuánto puede actuar el agente sin humano.
- **Techo duro** — límite de autonomía que ninguna evidencia levanta.
- **Radio de explosión** — cantidad máxima de CIs que una acción puede afectar.
- **Appliance / sonda** — hardware en el cliente (ver [ADR-0007](adr/ADR-0007-perfiles-de-hardware.md)).
- **Cortacircuito** — freno automático ante actividad anómala.

### Apéndice C · Matriz de SLA sugerida

| Prioridad | Respuesta | Resolución objetivo |
|---|---|---|
| P1 | 15 min | 4 h |
| P2 | 1 h | 8 h |
| P3 | 4 h | 2 días hábiles |
| P4 | 1 día hábil | 5 días hábiles |

> Valores de referencia — ajustar por contrato de cada cliente.

### Apéndice D · Plantilla de informe mensual

```
Cliente: ____________________   Período: __/____
1. Resumen ejecutivo (2–3 líneas)
2. Incidentes: total / por prioridad / MTTR / cumplimiento SLA
3. Cambios ejecutados (playbooks) y resultado
4. Estado del inventario (altas/bajas, CIs sin clasificar)
5. Disponibilidad de CIs críticos
6. Autonomía: distribución de acciones por nivel [Fase 3+]
7. Costo de inferencia vs. presupuesto
8. Recomendaciones para el próximo período
```

### Apéndice E · Referencias

- [02 · Arquitectura](02-arquitectura.md) · [03 · Mapeo ITIL 4](03-mapeo-itil4.md) ·
  [04 · Seguridad y autonomía](04-seguridad-y-autonomia.md) ·
  [05 · Modelo de costos](05-modelo-de-costos.md) · [06 · Roadmap](06-roadmap.md)
- [ADRs](adr/) · [Guía de despliegue](../deploy/README.md)
- Políticas: [`acciones.yaml`](../reference/policy/acciones.yaml) ·
  [`tenant-ejemplo.yaml`](../reference/policy/tenant-ejemplo.yaml)
