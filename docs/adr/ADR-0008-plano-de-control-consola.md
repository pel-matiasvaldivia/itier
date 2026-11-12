# ADR-0008 · Plano de control: una consola única sobre GLPI y Zabbix

**Estado:** aceptada · **Fecha:** 2026-11-12

## Contexto

El stack de Fases 1 y 2 deja dos interfaces separadas de cara al operador: el
dashboard de GLPI (ITSM/CMDB) y el de Zabbix (eventos/métricas). Operar la
plataforma implica saltar entre ambos, con sus propios modelos de usuario, y
ejecutar a mano la secuencia de pasos que supone **dar de alta un cliente**:
crear su entidad en GLPI, su grupo y su proxy en Zabbix, y transcribir la PSK y
los nombres al `.env` del appliance. Es tedioso, propenso a errores y no deja un
rastro unificado de quién hizo qué.

El roadmap (`docs/06`) ya nombra un **plano de control** en todas las fases
(registro de appliances y salud de flota en F1; panel de eventos en F2; UI de
propuestas en F3; cola de aprobaciones en F4; panel de autonomía y onboarding
automatizado en F5), pero nunca se materializó como producto. Hace falta su
primera encarnación: una web de operación.

## Decisión

Construir la **Consola de iTier**: una web de operación que es la **cara única**
de la plataforma y un **backend-for-frontend (BFF)** sobre GLPI y Zabbix. No
reemplaza a esos sistemas —siguen siendo la fuente de verdad— sino que los
**orquesta por sus APIs** detrás de flujos de alto nivel.

Cinco decisiones de diseño la definen:

1. **Identidad propia con 2FA obligatorio.** La consola gestiona sus propios
   operadores: contraseña con Argon2id y segundo factor **TOTP** obligatorio,
   con bloqueo por intentos, cookies de sesión firmadas (httponly + secure +
   samesite) y token CSRF en cada mutación. Sin depender de un proveedor externo.
2. **Server-rendered, un solo contenedor.** FastAPI + Jinja2, todo en Python
   (igual que el agente de borde), sin toolchain de Node ni paso de build.
3. **Estado propio mínimo en SQLite.** Solo lo de la consola: operadores (y su
   2FA), el registro de clientes dados de alta y la bitácora. La CMDB y el
   monitoreo no se duplican.
4. **Adaptadores que aíslan a GLPI y Zabbix.** Todo el acoplamiento con cada API
   vive en un único archivo por sistema (`adapters/glpi.py`, `adapters/zabbix.py`),
   con un modo demo de fixtures. Sustituir un sistema afecta a su adaptador, no al
   resto (coherente con ADR-0001 y ADR-0003).
5. **Auditoría encadenada por hash.** Toda acción que cambia estado queda sellada
   en una cadena verificable (igual que el agente — OWASP ASI10).

El flujo central, **alta de cliente**, encadena entidad GLPI + grupo/proxy Zabbix
con PSK y genera el **bundle de enrolamiento** (fragmento de `.env` + PSK) cuyos
nombres calzan exactamente con `deploy/cliente/`. Así el operador hace en un
formulario lo que antes eran pasos manuales en dos sistemas.

## Fundamentos

- **Un producto necesita una cara.** Vender "GLPI + Zabbix" no es vender iTier.
  La consola es donde la propuesta de valor se vuelve tangible y donde, en fases
  próximas, aterrizan la UI de propuestas (F3) y la cola de aprobaciones (F4).
- **La identidad y la auditoría son parte del producto de seguridad.** Un MSP que
  revende la plataforma quiere dueño end-to-end de quién opera y qué hace, sin
  que el login dependa de que el operador tenga configurado un IdP externo. El
  2FA obligatorio es piso, no opción.
- **Mínima superficie.** Server-rendered + SQLite + pocas dependencias auditables
  es más fácil de hardenear y revisar que una SPA con su cadena de build, en línea
  con el resto del repo (la landing es cero-dependencias).
- **El BFF desacopla del núcleo.** Operar vía adaptadores —no contra las tablas de
  GLPI/Zabbix— deja la puerta abierta a sustituir un componente sin reescribir la
  consola, igual que la capa MCP hace para el agente.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Seguir usando los dashboards nativos** | Fragmenta la operación en dos UIs y deja el onboarding como pasos manuales sin rastro unificado. Es el problema que originó este ADR |
| **OAuth/OIDC con un proveedor externo (Google/Microsoft)** | Menos código sensible, pero agrega una dependencia externa y configuración por despliegue. Para un producto self-hosted que se revende, poseer la identidad pesa más. Queda como opción futura, no como base |
| **IdP self-hosted (Keycloak/Authentik)** | SSO para toda la plataforma a futuro, pero suma un servicio más para hardenear y respaldar; sobredimensionado para el puñado de operadores de hoy |
| **SPA (React) + API** | Interactividad más rica a costa de toolchain de Node, build y más piezas móviles. No se justifica para una consola de formularios y tablas |
| **Plugin dentro de GLPI** | Acoplaría la consola al ciclo de vida y al modelo de plugins de GLPI, justo lo que ADR-0001 busca evitar, y no cubriría Zabbix |

## Consecuencias

**Positivas**
- Una sola interfaz y un solo login (con 2FA) para operar la plataforma.
- El alta de cliente deja de ser un procedimiento manual propenso a errores.
- Rastro de auditoría unificado y verificable de la operación.
- Base lista para colgar las UIs agénticas de F3/F4.

**Negativas**
- Es código de seguridad propio (login, 2FA, sesiones): hay que mantenerlo con
  disciplina y revisarlo.
- Introduce un almacén con secretos en reposo (PSK, secretos TOTP): el volumen
  debe protegerse y respaldarse con cuidado.
- Acopla la consola a las APIs de GLPI 11 y Zabbix 7.0, cuyas particularidades
  (sobre todo GLPI 11, reciente) hay que verificar; el riesgo queda confinado a
  los adaptadores.
- Sin compensación automática entre sistemas: un alta que falla a mitad de camino
  queda marcada para reintento/limpieza manual (aceptable en v1).

## Implementación

El componente vive en [`consola/`](../../consola/): `itier_console/` (app FastAPI,
seguridad, adaptadores, servicios, rutas y plantillas), `Dockerfile`,
`docker-compose.yml` y `.env.example`. Se despliega detrás de NPM como el resto
de los frontends del VPS (puerto publicado, cross-host). El camino crítico se
verifica en modo demo con `consola/tests/smoke_demo.py` (incluido en CI). Las
decisiones de autonomía (ADR-0006) y la capa MCP (ADR-0003) seguirán expresándose
*a través* de esta consola a medida que avancen las fases.
