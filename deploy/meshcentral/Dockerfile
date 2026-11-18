# iTier — imagen de MeshCentral (soporte remoto y estado de equipos, ADR-0011).
# Capa fina sobre la imagen oficial, con la versión FIJADA: el VPS hace `pull` de
# nuestra imagen (GHCR) y no depende de un `latest` que cambie solo. Para
# actualizar, subí MESH_VERSION y dejá que build-meshcentral.yml publique.
# ≥ 1.1.57: desde esa versión existe `allowedFramingOrigins` (embeber Mesh en la consola solo desde nuestro
# origen). Con la 1.1.53 el ajuste se ignora y Mesh manda X-Frame-Options: sameorigin → recuadro bloqueado.
ARG MESH_VERSION=1.1.60
FROM ghcr.io/ylianst/meshcentral:${MESH_VERSION}

ARG MESH_VERSION

# Entrypoint propio: escribe la config (host, puerto 4430, aliasPort 443, TLS offload
# de Cloudflare, solo WAN, ping 60 s) en cada arranque a partir de MESH_HOSTNAME /
# MESH_NEW_ACCOUNTS / MESH_TLS_OFFLOAD y recién entonces llama al oficial. Así no
# dependemos del modo dinámico de la imagen base, que cambia entre versiones.
COPY itier-entrypoint.sh /opt/meshcentral/itier-entrypoint.sh
RUN chmod 755 /opt/meshcentral/itier-entrypoint.sh
ENTRYPOINT ["/bin/bash", "/opt/meshcentral/itier-entrypoint.sh"]

LABEL org.opencontainers.image.title="itier-meshcentral" \
      org.opencontainers.image.description="MeshCentral para iTier (soporte remoto)" \
      org.opencontainers.image.source="https://github.com/pel-matiasvaldivia/itier" \
      itier.meshcentral.version="${MESH_VERSION}"
