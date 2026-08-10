# ADR-0007 · Perfiles de hardware — la Raspberry Pi es sonda, no appliance

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

La idea original del producto contemplaba desplegar el agente en una Raspberry Pi o en
una máquina virtual dentro de la infraestructura de cada cliente. La Pi es atractiva:
barata, silenciosa, de bajísimo consumo (~5–8 W bajo carga, centavos al año) y fácil de
enviar por correo.

Hay que validar si aguanta el stack completo.

## Decisión

**Tres perfiles de hardware, con la Raspberry Pi en un rol acotado:**

| Perfil | Hardware | Corre | Cuándo |
|---|---|---|---|
| **Sonda** | Raspberry Pi 5, 4–8 GB | GLPI Agent + Zabbix Proxy + túnel. **Sin ITSM local** | Sucursal o sitio remoto que reporta al appliance central |
| **Estándar** ⭐ | Mini-PC x86 (Intel N100/N150), 16 GB, NVMe 256 GB. ~USD 150–250 | Stack completo | Caso por defecto: 10–150 dispositivos |
| **Virtual** | VM 4 vCPU / 8–16 GB / 100 GB | Stack completo | Cliente con Proxmox, Hyper-V, VMware o nube propia |
| **Grande** | Mini-PC i5/Ryzen, 32 GB, NVMe 512 GB | Stack completo + retención larga | 150–250+ dispositivos |

## Fundamentos

### Por qué la Pi no sostiene el stack completo

1. **RAM.** El stack son GLPI (PHP-FPM + nginx), PostgreSQL, Zabbix Proxy, el runtime del
   agente y los servidores MCP. La guía de despliegue de GLPI 11 en Docker ya pide 2 GB
   solo para GLPI; sumado el resto, se llega al techo de la Pi sin margen operativo.

2. **Ecosistema Docker ARM64.** Es el problema silencioso. Las imágenes principales
   tienen builds ARM64, pero las imágenes de terceros, de nicho o antiguas frecuentemente
   no. Un x86 garantiza compatibilidad al 100% con el ecosistema Docker; un ARM64
   introduce una clase de problema que aparece en el peor momento — durante una
   actualización, en producción, en el cliente.

3. **I/O.** PostgreSQL con series de eventos sobre almacenamiento de tarjeta SD o USB es
   frágil. Un NVMe en un mini-PC es varios órdenes de magnitud mejor en durabilidad y
   latencia.

4. **El costo no lo justifica.** Un mini-PC N100 con 16 GB y NVMe cuesta USD 150–250 —
   comparable a una Pi 5 de 16 GB con caja, fuente, almacenamiento decente y refrigeración.
   Consume ~10 W. La diferencia de precio no compensa la diferencia de riesgo operativo.

### Por qué la Pi sí sirve como sonda

En el rol de sonda solo corre el colector, el proxy de eventos y el túnel: carga liviana,
sin base de datos, sin PHP. Ahí sus ventajas son reales — barata, sin ventilador,
consumo despreciable, se envía por correo a la sucursal y alguien la enchufa.

Es exactamente el caso de la PyME con casa central y dos o tres sucursales: una sonda por
sitio remoto, un appliance en la central.

### Por qué la VM sigue siendo primera clase

Muchas PyMEs ya tienen un hipervisor. En ese caso, la VM es superior al hardware físico:
sin logística de envío, snapshots, respaldo con la infra existente, provisión en minutos.
Debe ser una opción de despliegue de primer nivel, no un plan B.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Solo Pi** | No sostiene el stack completo con margen razonable |
| **Solo VM** | Excluye a las PyMEs sin virtualización, que son muchas en el segmento objetivo |
| **Servidor rackeable** | Sobredimensionado, caro y ruidoso para una oficina de PyME |
| **Pi con AI HAT+ 2** | El acelerador (Hailo-10H, 40 TOPS INT4, USD 130) soporta modelos de ~1,5 B. Irrelevante: ADR-0002 ya descartó la inferencia local. Agregaría costo sin resolver el cuello de botella real, que es RAM e I/O |

## Consecuencias

**Positivas**
- Compatibilidad total con el ecosistema Docker x86, sin sorpresas en actualizaciones
- Margen de recursos para crecer sin cambiar hardware
- La Pi conserva un rol donde es genuinamente la mejor opción

**Negativas**
- Tres perfiles de despliegue en lugar de uno: más matriz de pruebas
- La imagen del appliance debe construirse para x86 y ARM64 (esta última acotada a la sonda)
- Costo unitario de hardware algo mayor que el escenario Pi-only

## Nota de revisión

Esta decisión se revisa si aparece una Raspberry Pi (o SBC ARM equivalente) con ≥ 16 GB
de RAM y NVMe nativo a un precio sustancialmente menor que un mini-PC x86 comparable.
El criterio de decisión es el margen de RAM y la madurez del ecosistema de contenedores,
no el precio de la placa aislado.

## Fuentes

- [Raspberry Pi 5 vs Mini PC for Home Server in 2026 — HomeNode](https://homenode.tech/raspberry-pi-5-vs-mini-pc-home-server-2026/)
- [Mini PC vs Raspberry Pi for Home Server 2026 — MiniPCLab](https://minipclab.com/blog/mini-pc-vs-raspberry-pi-home-server)
- [How to Install GLPI 11 with Docker — Nextool Solutions](https://nextoolsolutions.com/en/blog/como-instalar-glpi-11-docker)
- [Raspberry Pi 5 LLM Benchmarks (2026) — Local AI Master](https://localaimaster.com/blog/llm-raspberry-pi-5)
