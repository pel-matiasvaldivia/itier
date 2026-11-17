# ADR-0011 — Agentes por HTTPS: GLPI Agent + MeshCentral (Zabbix queda como opción avanzada)

- Estado: aceptada · Reemplaza el enfoque «Zabbix para todos» de ADR-0009 / ADR-0010 para equipos de escritorio.

## Contexto
Zabbix Agent habla TCP crudo (10051). Detrás de Cloudflare eso exigió `cloudflared` en el cliente, Access, service tokens,
un equipo central y hasta un appliance. En la práctica (Windows Server 2012 R2 sin soporte de `cloudflared`, LXC, redes
de Docker) el despliegue pasó a ser lo más frágil del producto.

## Decisión
Cada equipo lleva **dos agentes, ambos por HTTPS saliente (443)**:
- **GLPI Agent** → inventario en GLPI (ya funcionaba por el túnel HTTP).
- **Mesh Agent** (MeshCentral) → soporte remoto y estado en línea, por WebSocket (`/agent.ashx`).

La consola crea un grupo de MeshCentral por cliente (`itier-<slug>`) y el instalador de Windows (único, silencioso,
con UAC una vez) instala ambos y reporta el resultado. **Zabbix** queda como opción avanzada para clientes que contratan
monitoreo de servidores/redes/impresoras, con el appliance Linux (ADR-0010).

## Consecuencias
- Sin túneles, sin equipo central ni appliance para el caso normal: un instalador por cliente.
- MeshCentral aporta control remoto y estado; **no** aporta métricas/alertas finas (disco, servicios): si se necesitan,
  se evalúa un agente propio (ver conversación de producto) o Zabbix avanzado.
- Superficie nueva a operar: el servidor MeshCentral (`MESH_*`, cuenta de servicio sin 2FA) y su actualización.
- No verificado contra un MeshCentral real detrás de Cloudflare: ver pruebas pendientes en `deploy/vps/README.md`.
