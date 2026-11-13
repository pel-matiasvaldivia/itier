# iTier — GLPI Agent (inventario + descubrimiento de red por SNMP).
#
# No existe imagen oficial del agente de GLPI (el repo oficial publica solo el
# SERVER, y el paquete tampoco está en los repos de Debian). La construimos desde
# los .deb OFICIALES (releases de GitHub), para una imagen fijable por digest,
# reproducible y multi-arch. Los .deb son "all" (Perl, sin binarios por arch), así
# que la misma receta sirve para amd64 (appliance) y arm64 (sonda Pi, ADR-0007);
# apt resuelve las dependencias de Perl por arquitectura.
#
# ⚠ netdiscovery/netinventory NO están en el paquete core: viven en
#   glpi-agent-task-network. Por eso instalamos AMBOS.

FROM debian:bookworm-slim

# Versión del agente (release oficial en GitHub). Fijala para builds reproducibles.
ARG GLPI_AGENT_VERSION=1.21
ARG REL=https://github.com/glpi-project/glpi-agent/releases/download/${GLPI_AGENT_VERSION}

# Paquetes:
#   glpi-agent               → core (tarea inventory; CLIs glpi-netdiscovery/-netinventory)
#   glpi-agent-task-network  → tareas netdiscovery + netinventory (SNMP)
# Deps SNMP explícitas (además de las que declaran los .deb):
#   libnet-snmp-perl        → Net::SNMP (consultas SNMP v1/v2c/v3)
#   libcrypt-des-perl       → Crypt::DES      (privacidad DES en SNMPv3)
#   libcrypt-rijndael-perl  → Crypt::Rijndael (privacidad AES en SNMPv3)
#   libdigest-hmac-perl     → Digest::HMAC    (autenticación SNMPv3)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    curl -fsSL -o /tmp/glpi-agent.deb "${REL}/glpi-agent_${GLPI_AGENT_VERSION}-1_all.deb"; \
    curl -fsSL -o /tmp/glpi-agent-task-network.deb "${REL}/glpi-agent-task-network_${GLPI_AGENT_VERSION}-1_all.deb"; \
    apt-get install -y --no-install-recommends \
      /tmp/glpi-agent.deb /tmp/glpi-agent-task-network.deb \
      libnet-snmp-perl libcrypt-des-perl libcrypt-rijndael-perl libdigest-hmac-perl; \
    rm -rf /var/lib/apt/lists/* /tmp/*.deb

COPY entrypoint.sh /usr/local/bin/itier-glpi-agent
RUN chmod +x /usr/local/bin/itier-glpi-agent

# Sin puerto entrante (ADR-0005): el agente corre sin su httpd embebido.
ENTRYPOINT ["/usr/local/bin/itier-glpi-agent"]
