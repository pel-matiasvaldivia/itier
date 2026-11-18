# OpenWA (WhatsApp de soporte) para iTier.
#
# Por qué una imagen propia: la oficial `openwa/wa-automate` está abandonada (>1 año) y su
# entrypoint ejecuta `bin/server.js`, que la librería v5 ya no trae; con la v4 la sesión no
# arranca porque WhatsApp Web cambió (`window.Debug` ya no existe). Acá se usa la imagen
# oficial solo por su Chrome y se instala la librería v5 con el webhook ya configurado.
FROM openwa/wa-automate:latest

ARG WA_VERSION=5.10.0

USER root
# `ps` lo usa la librería para limpiar procesos de Chrome (sin él se cae con «spawn ps ENOENT»).
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/itier-openwa /sessions \
 && chown -R owauser:owauser /opt/itier-openwa /sessions

WORKDIR /opt/itier-openwa
RUN npm init -y >/dev/null \
 && PUPPETEER_SKIP_DOWNLOAD=true npm i --ignore-scripts --no-audit --no-fund \
      @open-wa/wa-automate@${WA_VERSION} @open-wa/integration-webhook@${WA_VERSION} \
 && chown -R owauser:owauser /opt/itier-openwa

COPY wa.config.mjs /opt/itier-openwa/wa.config.mjs
COPY itier-entrypoint.sh /usr/local/bin/itier-entrypoint.sh
RUN chmod +x /usr/local/bin/itier-entrypoint.sh

ENV WA_EXECUTABLE_PATH=/usr/bin/google-chrome \
    WA_SESSION_ID=itier-soporte \
    WA_USER_DATA_DIR=/sessions/itier-soporte \
    NODE_ENV=production

USER owauser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/usr/local/bin/itier-entrypoint.sh"]
