# ADR-0004 · Ansible para la ejecución de remediaciones

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

Cuando el agente decide que hay que hacer algo (limpiar temporales, reiniciar un
servicio, aplicar un parche, extender un volumen), alguien tiene que ejecutarlo en la
infraestructura del cliente. La pregunta es qué ejecuta y quién lo escribe.

## Decisión

Toda remediación es un **playbook de Ansible del catálogo firmado**. El agente elige un
playbook **por nombre** y provee parámetros tipados; nunca genera el contenido del
playbook ni comandos.

**Requisitos obligatorios de todo playbook del catálogo:**
1. **Idempotente** — ejecutarlo dos veces produce el mismo estado
2. **Soporta `--check`** — expuesto al agente como `remediate.dry_run`, siempre permitido
3. **Reversible** — trae su procedimiento de reversión, o está marcado como irreversible
   y queda con techo duro L2
4. **Verificable** — declara la condición que debe cumplirse después para considerarse exitoso
5. **Firmado** — firmado en el plano de control, verificado en el borde antes de ejecutar
6. **Con radio de explosión declarado** — cuántos CIs puede afectar como máximo

## Fundamentos

- **Sin agente en el endpoint.** Ansible trabaja sobre SSH y WinRM, que es exactamente lo
  que ya se usa para el inventario remoto. No hay un agente más que desplegar y parchear
  en cada máquina del cliente.
- **La idempotencia hace segura la reejecución.** Un reintento no empeora el estado.
- **`--check` da un simulacro real y gratuito.** El agente puede evaluar el efecto de una
  acción antes de proponerla, y eso convierte una propuesta en evidencia.
- **Es texto revisable.** Un playbook se lee, se versiona, se revisa en pull request y se
  audita. Un comando generado por un LLM en tiempo de ejecución no.
- **Ecosistema enorme.** Colecciones para Windows, AD, VMware, redes y nube ya existen.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Comandos generados por el LLM** | Es ASI05 (RCE) por diseño. Descartado sin discusión |
| **Scripts de PowerShell/Bash a medida** | Sin idempotencia ni dry-run por defecto. Cada script reinventa el manejo de errores y la reversión |
| **Salt / Puppet / Chef** | Modelo de agente permanente o de estado deseado continuo. Más pesado que lo que el caso pide |
| **Tactical RMM como ejecutor** | Buen producto, pero acopla la ejecución a su modelo de scripting y su servidor. Ansible es una librería, no una plataforma |
| **API nativa de cada sistema** | Es lo correcto para algunos casos; se implementa como módulo de Ansible o tool MCP específica, no como excepción al modelo |

## Consecuencias

**Positivas**
- El catálogo de playbooks curados se vuelve un activo del negocio que compone valor con
  cada cliente
- Auditoría y revisión son triviales: es código en un repositorio
- La reversión es una capacidad de primera clase, no un parche

**Negativas**
- Escribir y validar cada playbook cuesta tiempo — es el trabajo real de la Fase 4
- Ansible tiene su propia curva y sus propias rarezas en Windows
- El catálogo limita al agente a lo previsto: casos nuevos requieren un playbook nuevo,
  que requiere revisión humana

**La última "desventaja" es en realidad el punto.** El agente no debería poder resolver
un problema que nadie revisó antes. Los casos nuevos se resuelven en L1 (recomendación
al técnico) y, si se repiten, se convierten en un playbook.

## Flujo de incorporación de un playbook

```
incidente nuevo → L1: el agente recomienda, el técnico resuelve a mano
                → se repite 3+ veces
                → se escribe el playbook, con reversión y verificación
                → revisión en PR + prueba en laboratorio
                → firma en el plano de control
                → entra al catálogo en L2 (requiere aprobación)
                → promoción por evidencia según ADR-0006
```
