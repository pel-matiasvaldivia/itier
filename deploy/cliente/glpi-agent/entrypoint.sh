#!/bin/sh
# Arranca el GLPI Agent como daemon (solo saliente, sin httpd) apuntando al
# endpoint del plugin GlpiInventory del server de iTier. Los RANGOS de IP y las
# CREDENCIALES SNMP se definen del lado del server (GlpiInventory), no acá: el
# agente pide su trabajo de netdiscovery/netinventory al contactar al server.
set -eu

: "${ITIER_TENANT:?falta ITIER_TENANT}"

# Endpoint del plugin GlpiInventory (GLPI 11). Si no se pasa explícito, se deriva
# de ITIER_GLPI_URL (la URL pública de GLPI por el túnel).
URL="${ITIER_GLPI_INVENTORY_URL:-}"
if [ -z "$URL" ]; then
  : "${ITIER_GLPI_URL:?falta ITIER_GLPI_URL o ITIER_GLPI_INVENTORY_URL}"
  URL="${ITIER_GLPI_URL%/}/plugins/glpiinventory/"
fi

set -- glpi-agent \
  --daemon --no-fork --no-httpd \
  --server="$URL" \
  --tag="itier-${ITIER_TENANT}" \
  --tasks="${GLPI_AGENT_TASKS:-inventory,netdiscovery,netinventory}" \
  --delaytime="${GLPI_AGENT_DELAYTIME:-60}" \
  --logger=stderr

# Escape hatch para opciones extra del agente (una sola cadena, separada por
# espacios). Útil, p. ej., para afinar SNMP con una conf montada o flags extra.
if [ -n "${GLPI_AGENT_EXTRA:-}" ]; then
  # shellcheck disable=SC2086
  set -- "$@" ${GLPI_AGENT_EXTRA}
fi

echo "itier-glpi-agent: server=$URL tag=itier-${ITIER_TENANT} tasks=${GLPI_AGENT_TASKS:-inventory,netdiscovery,netinventory}" >&2
exec "$@"
