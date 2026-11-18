# iTier — imagen de MeshCentral (soporte remoto y estado de equipos, ADR-0011).
# Capa fina sobre la imagen oficial, con la versión FIJADA: el VPS hace `pull` de
# nuestra imagen (GHCR) y no depende de un `latest` que cambie solo. Para
# actualizar, subí MESH_VERSION y dejá que build-meshcentral.yml publique.
ARG MESH_VERSION=1.1.53
FROM ghcr.io/ylianst/meshcentral:${MESH_VERSION}

ARG MESH_VERSION

# Plantilla de config detrás de Cloudflare (nuestra copia de la plantilla oficial,
# con aliasPort 443, solo WAN y ping cada 60 s): los agentes tienen que conectarse
# al puerto 443 PÚBLICO (el server escucha en otro puerto sin TLS) y el túnel no
# debe cerrar el WebSocket por inactividad. El resto (hostname, puerto, proxy,
# cuentas) lo completa el entrypoint con variables de entorno cuando
# DYNAMIC_CONFIG=true (ver deploy/vps/docker-compose.yml).
COPY config.json.template /opt/meshcentral/config.json.template

LABEL org.opencontainers.image.title="itier-meshcentral" \
      org.opencontainers.image.description="MeshCentral para iTier (soporte remoto)" \
      org.opencontainers.image.source="https://github.com/pel-matiasvaldivia/itier" \
      itier.meshcentral.version="${MESH_VERSION}"
