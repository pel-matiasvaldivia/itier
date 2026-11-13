# Esqueleto de referencia

Código que hace **concreta** la arquitectura descrita en [`docs/`](../docs/).
No es una implementación de producción: es la mínima expresión ejecutable de las tres
decisiones que definen el producto.

| Pieza | Qué demuestra | ADR |
|---|---|---|
| [`policy/acciones.yaml`](policy/acciones.yaml) | El catálogo de acciones con niveles base y techos duros | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`policy/tenant-ejemplo.yaml`](policy/tenant-ejemplo.yaml) | Cómo un cliente configura sus propios techos | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`edge-agent/itier_agent/policy.py`](edge-agent/itier_agent/policy.py) | El motor de políticas — **código determinista, fuera del LLM** | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`edge-agent/itier_agent/tools.py`](edge-agent/itier_agent/tools.py) | La superficie de herramientas tipada, con clases de permiso | [ADR-0003](../docs/adr/ADR-0003-mcp-capa-de-herramientas.md) |
| [`edge-agent/itier_agent/audit.py`](edge-agent/itier_agent/audit.py) | Auditoría append-only encadenada por hash | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`edge-agent/itier_agent/agent.py`](edge-agent/itier_agent/agent.py) | El bucle agéntico con el tool runner del SDK | [ADR-0002](../docs/adr/ADR-0002-razonamiento-fuera-del-borde.md) |
| [`edge-agent/itier_agent/events.py`](edge-agent/itier_agent/events.py) | Ingesta de eventos del monitoreo: contrato de fuente, fuente demo y `FuenteZabbix` | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`edge-agent/itier_agent/runner.py`](edge-agent/itier_agent/runner.py) | **El bucle de Fase 3**: ingesta → dedup → despacho al agente (o triage offline) | [ADR-0006](../docs/adr/ADR-0006-autonomia-graduada.md) |
| [`edge-agent/itier_agent/zabbix.py`](edge-agent/itier_agent/zabbix.py) | Cliente de eventos de Zabbix (`problem.get` + `event.get`), la fuente real del bucle | [ADR-0001](../docs/adr/ADR-0001-nucleo-itsm-glpi.md) |

## Lo que hay que mirar

**1. El motor de políticas no está en el prompt.** `policy.py` es código que corre antes
de cualquier efecto. Aunque el modelo sea secuestrado por una inyección en el texto de un
ticket, no puede cambiar su propio nivel de autonomía.

**2. Las tools de clase `act` no ejecutan: piden permiso.** Mirá
`remediate_run_playbook` en `tools.py` — llama al motor de políticas y, si el nivel
efectivo es L2, devuelve al modelo un resultado que dice *"encolado para aprobación"*.
El modelo recibe esa respuesta como cualquier otro resultado de herramienta y sigue
razonando con ella.

**3. No hay `run_shell`.** Ni lo hay ni lo va a haber. La superficie completa está en
`TOOLS` al final de `tools.py`, y se puede leer entera en un minuto.

**4. El prefijo se cachea y se mantiene byte-idéntico.** El system prompt no lleva
timestamps, ni nombre de cliente, ni IDs de sesión — todo eso va después del último punto
de caché, en el turno del usuario.

## Ejecutar

```bash
cd edge-agent
pip install -e .

export ANTHROPIC_API_KEY=...        # en producción esto NO vive en el appliance:
                                    # la llamada pasa por el gateway del plano de control
python -m itier_agent.demo
```

La demo simula un evento de disco lleno sobre un CI crítico y muestra el bucle completo:
recolección de evidencia → diagnóstico → propuesta de remediación → decisión de política
→ entrada de auditoría.

**Sin clave** la demo igual corre la parte determinista (motor de políticas, auditoría
y el **bucle de eventos de Fase 3**: ingesta, deduplicación y triage). Esa parte es la
que se testea en CI (`tests/test_fase3.py`).

### El bucle como servicio (Fase 3)

`itier_agent.serve` levanta el health check y, con `ITIER_AGENT_LOOP=1`, arranca el
**bucle de eventos** (`itier_agent.runner`) en un hilo. Sin esa variable queda en modo
esqueleto, como en Fase 1/2. Variables:

```bash
ITIER_AGENT_LOOP=1                 # activa el bucle (opt-in)
ITIER_AGENT_INTERVALO_S=60         # cada cuánto consulta la fuente
ITIER_POLICY_CATALOG / _TENANT     # rutas del catálogo firmado y la política del tenant
ITIER_ZBX_URL / ITIER_ZBX_TOKEN    # servidor Zabbix (API) + token → usa la fuente real
ITIER_ZBX_GROUP                    # host group del tenant, para acotar los problemas
```

**Fuente de eventos:** si están `ITIER_ZBX_URL` + `ITIER_ZBX_TOKEN`, el bucle lee los
problemas activos del servidor Zabbix (`zabbix.py`: `problem.get` + `event.get`, acotado
al `ITIER_ZBX_GROUP` del tenant) y los mapea a eventos. Si no, usa la **fuente demo**.
Sin clave de LLM corre en **OFFLINE** (tria y deja el evento en auditoría, sin inventar
diagnóstico).

> La correlación host→CI (`DatosDemo.ci_por_host`) es hoy un mapeo por nombre; en
> producción es una consulta a GLPI. Un host sin correspondencia deja su nombre como
> pista para que el agente lo busque en la CMDB.

## Lo que falta para producción

- **Correlación de CMDB y tickets:** `zabbix.py` ya lee los problemas del servidor; falta
  la resolución host→CI real contra GLPI (hoy es un mapeo por nombre) y la apertura/cierre
  de tickets de GLPI por evento
- Servidores MCP reales (acá las tools son funciones locales; en producción son procesos
  MCP separados con su propio límite de privilegio)
- Adaptador de GLPI contra la API v2 con OAuth2
- Ejecutor de Ansible con verificación de firma
- Túnel mTLS y cliente del plano de control
- Capa de minimización y redacción de PII previa a cada llamada
- Motor de promoción por evidencia (acá los niveles son estáticos)
- Persistencia (acá todo es en memoria)
