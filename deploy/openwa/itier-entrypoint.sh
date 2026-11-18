#!/bin/bash
# Arranca OpenWA v5 como servidor (API en :8080). Requiere WA_API_KEY y ITIER_WEBHOOK_URL.
set -euo pipefail
: "${WA_API_KEY:?falta WA_API_KEY}"
: "${ITIER_WEBHOOK_URL:?falta ITIER_WEBHOOK_URL}"
# El perfil de Chrome (la sesión vinculada) vive en el volumen: si quedó un candado de un cierre brusco, se limpia.
rm -f "${WA_USER_DATA_DIR:-/sessions/itier-soporte}"/Singleton* 2>/dev/null || true
# La imagen base (para v4) define WA_POPUP=true & co.: v5 los lee como config y rechaza el texto
# «true» donde espera un booleano («Failed to start … path: popup»). Se descartan.
unset WA_POPUP WA_DISABLE_SPINS WA_CLI_CONFIG WA_USE_CHROME
cd /opt/itier-openwa
exec node node_modules/@open-wa/wa-automate/dist/cli.cjs \
  --config ./wa.config.mjs \
  --session-id "${WA_SESSION_ID:-itier-soporte}" \
  --host 0.0.0.0 --port 8080 \
  --api-key "${WA_API_KEY}" \
  --use-chrome --no-dashboard
