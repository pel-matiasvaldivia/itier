# ADR-0006 · Autonomía graduada y ganada por evidencia

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

La pregunta central de un producto agéntico para infraestructura ajena es: **¿cuánto lo
dejamos actuar solo?**

Las dos respuestas simples fallan. Autonomía total: un error del agente destruye la
confianza del cliente y probablemente el negocio. Autonomía cero: el producto es un
sistema de recomendaciones caro y no ahorra horas de técnico, que es de donde sale el
margen.

## Decisión

Implementar **cinco niveles de autonomía** por acción, donde el nivel efectivo es el
mínimo de cuatro techos independientes, y donde **la promoción de nivel se gana con
evidencia y la degradación es automática**.

### Los niveles

| Nivel | Comportamiento |
|---|---|
| **L0 · Observar** | Solo lectura. Ni siquiera escribe tickets |
| **L1 · Recomendar** | Propone al técnico con evidencia; el humano ejecuta |
| **L2 · Aprobar y actuar** | Propone plan; ejecuta tras aprobación humana explícita |
| **L3 · Actuar y notificar** | Ejecuta y notifica; hay ventana de reversión |
| **L4 · Autónomo** | Ejecuta; queda visible en auditoría |

### El nivel efectivo

```
nivel_efectivo = min(
    nivel_base_de_la_acción,      # catálogo de acciones
    techo_del_tenant,             # tolerancia global del cliente
    techo_por_criticidad_del_CI,  # CI crítico baja el techo
    techo_por_ventana_horaria     # fuera de horario baja el techo
)
```

### Promoción por evidencia

| Transición | Requisito |
|---|---|
| L1 → L2 | ≥ 20 propuestas, ≥ 90% aceptadas sin modificar |
| L2 → L3 | ≥ 50 ejecuciones aprobadas, 0 reversiones, 0 incidentes derivados, ≥ 60 días |
| L3 → L4 | ≥ 200 ejecuciones, 0 reversiones, ≥ 180 días, **y aprobación escrita del cliente** |

**La degradación es inmediata y automática** ante cualquier reversión, incidente derivado
o rechazo del técnico. Baja un nivel y reinicia el contador. La promoción nunca es
automática: requiere acción deliberada.

### Techos duros

Ninguna evidencia levanta estos techos. Nunca superan L2:
otorgar acceso · destruir datos sin reversión verificada · tocar el controlador de
dominio, el firewall perimetral o el sistema de backup · modificar la configuración del
propio iTier · cualquier cosa con impacto financiero.

## Fundamentos

- **Es el principio de mínima agencia de OWASP.** El Top 10 for Agentic Applications 2026
  lo formula así: la autonomía es una funcionalidad que debe ganarse, no un default. No
  se trata solo de a qué puede acceder el agente, sino de cuánta libertad tiene para
  actuar sin consultar.
- **Es un producto vendible.** Los planes comerciales se alinean con los niveles: el
  cliente compra la confianza que quiere y la sube cuando la ve funcionar. Convierte una
  restricción técnica en un eje de pricing.
- **Genera los datos de su propia promoción.** Cada propuesta aceptada o rechazada, cada
  ejecución exitosa o revertida, es evidencia. El sistema mide su propia confiabilidad.
- **La política vive fuera del LLM.** Es la propiedad crítica: aunque el modelo sea
  secuestrado por una inyección de prompt (ASI01), no puede alterar su propio nivel de
  autonomía. El motor de políticas es código determinista, versionado y auditado.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Interruptor binario auto/manual** | Demasiado grueso. Limpiar temporales y tocar un controlador de dominio no pueden compartir configuración |
| **Confianza basada en la confianza del modelo** | La confianza declarada por un LLM no es una medida calibrada de riesgo, y es manipulable por el contenido de entrada |
| **Aprobación humana para todo** | No ahorra horas de técnico. Sin ahorro no hay margen y no hay negocio |
| **Autonomía por rol del agente** | Menos granular que por acción, y no captura la criticidad del CI ni el momento del día |

## Consecuencias

**Positivas**
- Camino de adopción gradual que respeta el ritmo de confianza del cliente
- El riesgo es acotado y explicable en una tabla
- El motor de promoción produce, como subproducto, la métrica de madurez del producto

**Negativas**
- Complejidad real en el motor de políticas
- El período de acumulación de evidencia es largo — meses antes de que haya L3 amplio
- Requiere disciplina: la tentación de "subir esta acción a L4 así deja de molestar" hay
  que resistirla por diseño, no por buena voluntad

## Implementación

El catálogo de acciones y las políticas viven en [`reference/policy/`](../../reference/policy/)
como archivos declarativos, versionados en git y firmados en el plano de control.
Un cambio de política es un pull request, no una edición en una UI.
