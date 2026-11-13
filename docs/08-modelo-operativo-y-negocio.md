# 08 · Modelo operativo y de negocio

Cómo se usa iTier en la práctica —quién toca qué— y el modelo comercial que lo
hace viable para PyMEs **sin IT on-site**. Complementa `01-analisis-mercado`,
`05-modelo-de-costos` y `06-roadmap`.

> **Tesis.** iTier **no** es "un GLPI más lindo para el cliente". Es el **cockpit
> del operador** + el **agente de IA** que hace rentable el servicio gestionado,
> y el cliente recibe una **ventana delgada** que genera confianza. Lo que se
> vende es el **resultado gestionado**, no el software.

## El circuito

```
   CLIENTE (PyME sin IT on-site)
     │  abre/sigue tickets · ve salud · recibe reporte · autoriza autonomía
     ▼
   PORTAL DEL CLIENTE            ← acotado a SU empresa (tenant)
     │
     ▼
   iTier CONSOLA (operador)      ← cockpit de tu equipo (vos + técnicos)
     │  opera por API ▼                      ▲ el agente propone / actúa
   ┌───────────────┬───────────────┬─────────┴──────────┐
   │ GLPI (motor)  │ Zabbix (ojos) │ Agente IA (Fase 3+) │
   │ CMDB·tickets  │ métricas·even.│ triage·diagnóstico  │
   └───────────────┴───────────────┴────────────────────┘
```

- **GLPI** — motor y **sistema de registro** (CMDB, tickets, activos). No es la
  cara del producto. Cada cliente es una **entidad** aislada: su CMDB y sus
  tickets quedan separados del resto (base del multi-tenant).
- **Zabbix** — los **ojos** (métricas y eventos).
- **Consola de iTier** — el **tablero del operador**. Hoy: alta de clientes,
  agentes, tickets, salud, auditoría. Con el tiempo absorbe los flujos diarios
  para que el técnico casi no toque GLPI crudo.
- **Agente de IA** (Fase 3+) — triage, diagnóstico, recomendación y, más
  adelante, remediación. Es el **diferencial y el margen**; hoy es andamiaje.

## Roles y accesos

| Quién | Herramienta | Alcance |
|---|---|---|
| **Operador** (vos + técnicos) | **Consola** (principal) + **GLPI** (motor, para lo profundo) | Toda la flota |
| **Cliente** (la PyME) | **Portal del cliente** | **Solo su empresa** (su entidad / sus equipos) |
| **Agente de IA** | Superficie MCP tipada (ADR-0003) | Según nivel de autonomía (ADR-0006) |

Reglas:

- **Al cliente NO se le da la consola de operador** (ve todos los clientes, da de
  alta, etc.): filtraría datos de otros. El cliente va al **portal**, acotado a su
  tenant.
- **Al técnico, la consola como herramienta diaria, no GLPI.** Si GLPI es la
  herramienta del técnico, el producto se vuelve "otra instalación de GLPI" y se
  pierde el diferencial. GLPI queda para lo profundo/raro (catálogo, CMDB
  compleja).
- **El portal y la consola son superficies distintas** sobre el mismo núcleo. El
  portal es de confianza del lado del servidor: scopea por entidad/grupo en
  código; el cliente **nunca** recibe credenciales de GLPI/Zabbix.
- Autenticación fuerte en ambas: contraseña Argon2id + **2FA TOTP obligatorio**.

### Qué hace el portal del cliente

- **Mis tickets** — abrir, seguir y ver *sus* tickets (self-service de mesa de
  ayuda), scopeados a su entidad de GLPI.
- **Mis equipos / salud** — estado de *sus* hosts (desde Zabbix), solo lectura.
- **Reporte mensual** (Fase 5) — lo que justifica la factura.
- **Autonomía** (Fase 4+) — ver y autorizar el nivel que iTier puede ejecutar.

Lo que el portal **no** hace: asignar técnicos, ver otros clientes, configurar
nada del núcleo. Eso es del operador.

## Modelo de negocio

Se vende **"IT gestionado como servicio", remoto**. El cliente no quiere aprender
ITIL ni GLPI; quiere *"que mi IT funcione y, si se rompe, se arregle rápido, y
ver qué pasa y cuánto cuesta"*. El software es el medio; el valor sos **vos + el
agente**.

### Planes por dispositivo/mes, atados a la autonomía

Alineado con `06-roadmap` (la autonomía es un eje de pricing, no solo técnico) y
`05-modelo-de-costos`.

| Plan | Qué incluye | Autonomía |
|---|---|---|
| **Visibilidad** | Inventario + monitoreo + alertas + reporte | L0 — solo observa |
| **Asistido** | + el agente diagnostica y recomienda; el técnico resuelve | L1 |
| **Gestionado** | + el agente remedia con aprobación | L2–L3 |
| **Autónomo** | + acciones probadas sin intervención | L4 |

- **Fee de onboarding** único: instalación del appliance/sonda + limpieza inicial
  de CMDB.
- La rentabilidad sale del ratio **1 técnico : 25+ clientes** que habilita el
  agente (ver criterios de salida de Fase 5 en `06-roadmap`).

### Por qué es fácil de adoptar (sin empleado on-site)

- **Onboarding cero-touch.** El appliance (o el agente + proxy) enchufa y "llama a
  casa". El alta de cliente de la consola + el *bundle de enrolamiento* ya es
  exactamente esto.
- **Humano + IA.** Se vende el resultado, no la herramienta. El agente baja el
  costo por cliente → se puede cobrar poco y escalar.
- **Confianza graduada = eje de precio.** El cliente compra el nivel de autonomía
  que se anima y lo sube cuando lo ve funcionar. Fricción de entrada mínima
  ("empezá con solo mirar").
- **Datos en su infra / tu VPS**, no en una nube ajena — argumento fuerte para
  PyMEs argentinas.
- **Contacto simple.** El cliente abre un ticket (portal / WhatsApp / mail) → entra
  a GLPI → el agente lo tría → el técnico lo cierra → el cliente ve "resuelto" +
  su reporte. Nunca ve la complejidad de GLPI.

## Qué falta para cerrar el modelo

En orden de impacto:

1. **Agente de IA (Fase 3)** — el diferencial y el margen.
2. **Portal del cliente** acotado por tenant — en construcción (`consola/`, rutas
   `/portal`). Lo más visible para vender.
3. **Reporte mensual automático** (Fase 5) — justifica la factura.
4. **Roles en la consola** (operador vs admin) y aislamiento por tenant a medida
   que crecen los clientes.

## Nota de implementación

El portal del cliente vive en el mismo servicio `consola/` que el cockpit del
operador, pero como **superficie separada**: usuarios de tipo *cliente* atados a
un `client_id`, rutas bajo `/portal`, y todo el acceso a datos **scopeado por la
entidad de GLPI y el grupo de Zabbix** de ese cliente. Un usuario cliente no
puede alcanzar las rutas del operador ni los datos de otro cliente.
