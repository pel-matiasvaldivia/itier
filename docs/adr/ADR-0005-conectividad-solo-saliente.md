# ADR-0005 · Conectividad solo saliente

**Estado:** aceptada · **Fecha:** 2026-08-10

## Contexto

El plano de control necesita gestionar appliances desplegados en decenas de redes de
clientes: enviar configuración, recibir telemetría, entregar aprobaciones y desplegar
actualizaciones. Las opciones habituales son VPN, port-forwarding, o un túnel iniciado
desde el appliance.

## Decisión

**El appliance no expone ningún puerto entrante.** Mantiene un túnel **saliente** con
mTLS hacia el plano de control, por el que viaja todo el tráfico bidireccional de
gestión.

Egress con default-deny y allowlist explícita: plano de control, repositorios de
paquetes firmados y NTP. Nada más.

## Fundamentos

- **Elimina la clase de ataque completa.** Sin puerto entrante no hay superficie que
  escanear, ni credencial de VPN que filtrar, ni regla de NAT que quede mal configurada.
- **Instalación sin tocar el firewall del cliente.** Diferencia comercial real: el
  appliance funciona detrás de NAT, de CGNAT y de conexiones domésticas sin que nadie
  tenga que pedirle nada al proveedor de internet.
- **Menos soporte.** La causa número uno de tickets de instalación en productos
  comparables es la configuración de red. Acá desaparece.
- **Identidad criptográfica.** El certificado de cliente emitido en el enrolamiento es
  la identidad del appliance, con rotación automática y revocación desde el plano de
  control — que es también el control contra ASI10 (Rogue Agents).
- **Argumento de venta.** *"No hay que abrir nada en tu firewall"* cierra una objeción
  de seguridad antes de que aparezca.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **VPN site-to-site** | Requiere coordinación con el proveedor de red del cliente, es frágil ante cambios de IP y crea una ruta bidireccional persistente que amplía el radio de explosión |
| **Port-forwarding + IP fija** | Superficie entrante expuesta. Inviable tras CGNAT. Descartado |
| **Polling HTTP puro** | Simple, pero con latencia alta para aprobaciones interactivas y desperdicio de peticiones vacías |
| **Túnel de terceros (ngrok, Cloudflare Tunnel)** | Dependencia operativa y de seguridad sobre un tercero, en la ruta crítica de todos los clientes |

## Implementación

- Transporte: WebSocket sobre TLS con autenticación mutua, o gRPC bidireccional
- Puerto: 443 saliente (atraviesa cualquier firewall corporativo razonable)
- Reconexión con backoff exponencial y jitter
- Heartbeat con detección de appliance silencioso en el plano de control
- Cola local durable: nada se pierde durante una desconexión
- Todo lo que baja del plano de control viene firmado y se verifica antes de aplicar

## Consecuencias

**Positivas**
- Instalación de minutos, sin intervención del área de redes del cliente
- Superficie de ataque entrante nula
- Funciona en cualquier topología de red de PyME

**Negativas**
- El plano de control debe sostener conexiones persistentes a escala (costo de infra
  modesto, pero real)
- Sin conectividad no hay gestión remota → mitigado por el modo degradado (ADR-0002)
- Acceso de emergencia a un appliance sin red requiere presencia física o acceso
  fuera de banda, que se documenta como procedimiento

## Nota sobre acceso remoto a endpoints

MeshCentral (acceso remoto asistido al equipo del usuario final) es una capacidad
**separada**, expuesta como la tool `access.request_remote_session`. Requiere testigo
humano obligatorio, tiene techo duro L2 y su tráfico también viaja por el túnel
saliente. El agente nunca abre una sesión remota por su cuenta.
