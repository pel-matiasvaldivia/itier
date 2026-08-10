# ADR-0001 · GLPI 11 como núcleo ITSM

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

iTier necesita una CMDB, un sistema de tickets, gestión de activos y un catálogo de
servicios, corriendo dentro de la red del cliente, con multi-tenancy para el proveedor
y una API programable para que el agente lea y escriba.

Construirlo desde cero sería reimplementar veinte años de trabajo ajeno.

## Decisión

Adoptar **GLPI 11** como núcleo ITSM del appliance, con PostgreSQL.

## Fundamentos

- **Densidad funcional.** Es el único open source que trae helpdesk, CMDB, ITAM,
  catálogo de servicios y gestión financiera (contratos, compras, garantías) en una sola
  instalación. iTop tiene mejor CMDB pero peor todo lo demás; Zammad es solo ticketing;
  Snipe-IT es solo ITAM.
- **Multi-entidad nativo.** Maneja múltiples clientes sin hacks — crítico para el modelo
  de negocio.
- **API v2 con OAuth2.** GLPI 11 introdujo la High-Level API con OAuth2 y RSQL,
  reemplazando los parámetros de búsqueda de la API legada. Es una base decente para el
  adaptador MCP.
- **Colector propio.** GLPI Agent hace inventario con y sin agente (WinRM/SSH) y
  descubrimiento SNMP. Una integración menos que mantener.
- **Alineado a ITIL.** El modelo de datos ya trae incidentes, problemas, cambios y
  configuración.
- **Estable desde 2025-10-01** (GLPI 11.0.0), con imágenes Docker por release.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **iTop** | Mejor modelado CMDB, pero más rígido de personalizar, comunidad menor y sin colector propio |
| **Zammad** | Ticketing excelente, pero sin CMDB ni ITAM. Habría que sumar dos productos más |
| **Snipe-IT + Zammad** | Dos sistemas con dos modelos de datos que reconciliar en el borde. Complejidad sin beneficio |
| **CMDB propia** | Meses de trabajo para llegar a un subconjunto pobre de GLPI. El diferencial de iTier no está acá |
| **ServiceNow / Freshservice** | Cloud-first, precio por asiento, y los datos salen de la red del cliente. Contradice la premisa del producto |

## Consecuencias

**Positivas**
- Fase 1 se acorta a meses en lugar de años
- El cliente puede auditar y, en el peor caso, quedarse con su GLPI

**Negativas**
- Dependencia de la hoja de ruta y las decisiones de licencia de un tercero
- La API v2 es nueva; hay riesgo de cambios y de huecos funcionales
- PHP en el stack, con su propio perfil de mantenimiento y parches

**Mitigación del acoplamiento:** el agente **nunca** habla con GLPI directamente. Toda
lectura y escritura pasa por el gateway MCP. Sustituir el núcleo ITSM afecta un
adaptador, no el agente ni las políticas.
