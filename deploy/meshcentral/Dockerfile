# iTier — imagen de MeshCentral (soporte remoto y estado de equipos, ADR-0011).
# Capa fina sobre la imagen oficial, con la versión FIJADA: el VPS hace `pull` de
# nuestra imagen (GHCR) y no depende de un `latest` que cambie solo. Para
# actualizar, subí MESH_VERSION y dejá que build-meshcentral.yml publique.
ARG MESH_VERSION=1.1.53
FROM ghcr.io/ylianst/meshcentral:${MESH_VERSION}

ARG MESH_VERSION
LABEL org.opencontainers.image.title="itier-meshcentral" \
      org.opencontainers.image.description="MeshCentral para iTier (soporte remoto)" \
      org.opencontainers.image.source="https://github.com/pel-matiasvaldivia/itier" \
      itier.meshcentral.version="${MESH_VERSION}"
