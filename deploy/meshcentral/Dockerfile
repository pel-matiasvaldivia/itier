# iTier — imagen de MeshCentral (soporte remoto y estado de equipos, ADR-0011).
# Capa fina sobre la imagen oficial, con la versión FIJADA: el VPS hace `pull` de
# nuestra imagen (GHCR) y no depende de un `latest` que cambie solo. Para
# actualizar, subí MESH_VERSION y dejá que build-meshcentral.yml publique.
ARG MESH_VERSION=1.1.53
FROM ghcr.io/ylianst/meshcentral:${MESH_VERSION}

ARG MESH_VERSION

# Plantilla de config detrás de Cloudflare: los agentes tienen que conectarse al
# puerto 443 PÚBLICO (aliasPort; el server escucha en otro puerto sin TLS), solo
# WAN, y ping cada 60 s para que el túnel no cierre el WebSocket por inactividad.
# El resto (hostname, puerto, proxy, cuentas) lo completa el entrypoint con las
# variables de entorno cuando DYNAMIC_CONFIG=true (ver deploy/vps/docker-compose.yml).
RUN jq '.settings.aliasPort = 443 | .settings.WANonly = true | del(.settings._WANonly, .settings._aliasPort) | .settings.agentPing = 60' \
        /opt/meshcentral/config.json.template > /tmp/config.json.template \
    && mv /tmp/config.json.template /opt/meshcentral/config.json.template

LABEL org.opencontainers.image.title="itier-meshcentral" \
      org.opencontainers.image.description="MeshCentral para iTier (soporte remoto)" \
      org.opencontainers.image.source="https://github.com/pel-matiasvaldivia/itier" \
      itier.meshcentral.version="${MESH_VERSION}"
