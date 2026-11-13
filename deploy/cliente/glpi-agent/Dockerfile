# iTier — GLPI Agent (inventario + descubrimiento de red por SNMP).
#
# No existe imagen oficial del agente de GLPI (el repo oficial publica solo el
# SERVER). Construimos la nuestra desde el paquete de Debian, para tener una
# imagen fijable por digest, reproducible y multi-arch (amd64 para el appliance,
# arm64 para la sonda Raspberry Pi — ADR-0007), con la interfaz estándar del
# agente. El paquete es Perl ("Architecture: all"): la misma receta sirve para
# amd64 y arm64; apt resuelve las deps por arquitectura.
#
# Nota: la versión del agente la fija la release de Debian (bookworm). El
# protocolo de inventario es estable; el endpoint (GlpiInventory de GLPI 11) lo
# fija la config, no la versión del agente.

FROM debian:bookworm-slim

# glpi-agent + dependencias de SNMP para netdiscovery/netinventory:
#   libnet-snmp-perl        → Net::SNMP (consultas SNMP v1/v2c/v3)
#   libcrypt-des-perl       → Crypt::DES      (privacidad DES en SNMPv3)
#   libcrypt-rijndael-perl  → Crypt::Rijndael (privacidad AES en SNMPv3)
#   libdigest-hmac-perl     → Digest::HMAC    (autenticación SNMPv3)
# El paquete trae glpi-agent, glpi-netdiscovery y glpi-netinventory.
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      glpi-agent \
      libnet-snmp-perl libcrypt-des-perl libcrypt-rijndael-perl libdigest-hmac-perl \
      ca-certificates; \
    rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/itier-glpi-agent
RUN chmod +x /usr/local/bin/itier-glpi-agent

# Sin puerto entrante (ADR-0005): el agente corre sin su httpd embebido.
ENTRYPOINT ["/usr/local/bin/itier-glpi-agent"]
