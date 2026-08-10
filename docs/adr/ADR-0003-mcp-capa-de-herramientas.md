# ADR-0003 · MCP como única capa de herramientas

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

El agente necesita leer la CMDB, consultar métricas, buscar tickets y ejecutar
remediaciones. Hay varias formas de dárselo: llamadas directas a APIs desde el código del
bucle, una tool genérica de HTTP, acceso a shell, o una superficie de herramientas
tipada.

Esta decisión determina el techo de seguridad de todo el producto.

## Decisión

**Toda capacidad del agente se expone exclusivamente como una tool MCP tipada, con una
clase de permiso declarada.** No existe ninguna otra vía por la que el agente afecte al
mundo.

Clases: `read` · `write_itsm` · `act` · `notify`.

**Prohibiciones explícitas y permanentes:**
- No existe `run_shell` ni equivalente
- No existe `execute_sql` ni acceso directo a base de datos
- No existe cliente HTTP genérico
- No existe acceso a filesystem fuera de una allowlist de rutas de logs

Agregar una capacidad significa **agregar una tool nueva**, revisada y clasificada.
Nunca ampliar el alcance de una existente.

## Fundamentos

- **Es el control primario contra ASI02 (Tool Misuse) y ASI05 (RCE).** Un agente
  secuestrado por una inyección de prompt en el texto de un ticket solo puede invocar
  tools del catálogo, con parámetros validados por esquema. No puede componer un comando.
- **Es un punto de control único.** Autorización, auditoría, rate limiting y
  cortacircuitos se implementan una vez, en el gateway, y aplican a todo.
- **MCP es el estándar.** Creado por Anthropic en noviembre de 2024, superó los 97 M de
  descargas mensuales de SDK y más de 10.000 servidores en producción a comienzos de
  2026. La spec 2026-07-28 agrega core stateless, Extensions, Tasks y hardening de
  autorización.
- **Desacopla el sistema subyacente.** Cambiar GLPI por otro ITSM afecta a un servidor
  MCP, no al agente ni a las políticas (ver ADR-0001).
- **Los esquemas son documentación ejecutable.** La descripción de cada tool es lo que
  el modelo usa para decidir cuándo invocarla; el esquema es lo que valida la entrada.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Llamadas directas a API desde el bucle** | Funciona, pero dispersa autorización y auditoría por todo el código. Cada capacidad nueva reabre la discusión de seguridad |
| **Tool genérica de HTTP** | Equivalente a darle internet abierto al agente. Inaceptable |
| **Tool de shell** | Descartada de plano: es exactamente ASI05 |
| **Claude Agent SDK** | Trae Read/Write/Edit/Bash incorporados. Es precisamente la superficie que este ADR busca evitar |
| **Function calling sin MCP** | Es lo mismo sin el estándar. MCP aporta interoperabilidad y ecosistema sin costo adicional |

## Consecuencias

**Positivas**
- La superficie de ataque del agente es enumerable y revisable en una sola tabla
- Se puede responder a un cliente exactamente qué puede hacer el agente
- Cada tool nueva pasa por revisión de seguridad por construcción

**Negativas**
- Fricción deliberada: agregar una capacidad es más lento que llamar a una API
- Los servidores MCP son código propio a mantener
- Algo de latencia por la indirección (irrelevante frente a la latencia de inferencia)

## Regla operativa

> Si alguien propone una tool cuya descripción incluye las palabras *"genérica"*,
> *"flexible"*, *"arbitraria"* o *"para lo que haga falta"*, la respuesta es no.
> Una tool que puede hacer cualquier cosa es una tool de shell con otro nombre.
