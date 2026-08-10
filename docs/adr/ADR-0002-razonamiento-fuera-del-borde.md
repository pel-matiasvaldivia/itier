# ADR-0002 · El razonamiento ocurre fuera del borde

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

El appliance vive dentro de la red del cliente. La pregunta natural es si el LLM también
debería vivir ahí: sería la privacidad máxima y la independencia total de internet.

## Decisión

**El appliance no ejecuta ningún LLM.** El razonamiento se hace con la API de Claude,
a través de un gateway del plano de control. El bucle del agente **sí** corre en el
appliance; solo la inferencia sale.

## Fundamentos

### El hardware de borde no da

Mediciones públicas de 2026 sobre Raspberry Pi 5:

| Modelo | Rendimiento |
|---|---|
| 1,5 B (4-bit) | 5–15 tok/s |
| 3 B | 2–5 tok/s (Llama 3.2 3B ≈ 8,8 tok/s, Phi-3.5 Mini ≈ 7,4 tok/s) |
| AI HAT+ 2 (Hailo-10H, 40 TOPS INT4, USD 130) | soporta modelos de ~1,5 B al lanzamiento |

Un bucle agéntico con herramientas consume decenas de miles de tokens por incidente en
varios turnos. A 8 tok/s, un solo diagnóstico tarda **horas**. Y un modelo de 1,5–3 B no
tiene la capacidad de tool-calling ni de razonamiento multi-paso que el caso exige.

Un mini-PC tampoco cambia el resultado: sin GPU dedicada, sigue sin haber presupuesto de
cómputo. Poner GPU en cada cliente destruye la economía del appliance.

### La calidad importa más de lo que ahorra

El costo de un diagnóstico equivocado que dispara una remediación equivocada es órdenes
de magnitud mayor que el costo de tokens. Ver [modelo de costos](../05-modelo-de-costos.md):
el gasto de API es ~USD 42/mes por cliente. No es donde se optimiza.

### El bucle sí se queda en el borde

Es una decisión separada y deliberada: el **bucle del agente** (orquestación, invocación
de herramientas, aplicación de políticas) corre en el appliance, aunque la inferencia
salga. Así:

- El contexto operativo detallado nunca se persiste fuera del cliente
- La latencia contra las herramientas locales es de milisegundos
- El motor de políticas actúa dentro de la red, no aguas arriba
- La degradación sin WAN es limpia: se pierde el razonamiento, no la operación

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **LLM local en el appliance** | Ni el hardware ni la calidad de modelos de 1–3 B alcanzan para tool-calling agéntico |
| **LLM local + GPU** | Destruye el costo del appliance y su perfil de consumo y mantenimiento |
| **Bucle completo en la nube** | El plano de control necesitaría acceso profundo y en tiempo real a la red del cliente. Contradice ADR-0005 y multiplica la superficie de ataque |
| **Modelo local pequeño para pre-filtrado** | Evaluado y descartado para fase 1: la redacción de PII y la clasificación se resuelven mejor con regex + Presidio, que son deterministas y auditables |

## Consecuencias

**Positivas**
- Calidad de razonamiento de frontera desde el día uno
- Appliance barato, silencioso, de bajo consumo
- Custodia de la clave de API centralizada; ningún appliance la tiene

**Negativas**
- Dependencia de conectividad para el razonamiento → mitigada por el modo degradado
- Contexto de operación sale de la red del cliente → mitigado por minimización y
  redacción previas (ver [seguridad](../04-seguridad-y-autonomia.md))
- Dependencia de un proveedor externo → confinada al gateway; MCP y políticas son agnósticos

## Nota sobre el modo degradado

Sin WAN, el appliance mantiene: ingesta, inventario, reglas deterministas y ejecución de
playbooks ya aprobados en L3/L4. Encola: bucle del agente y aprobaciones L2.
**Un corte de internet degrada capacidades, no rompe la operación.**
