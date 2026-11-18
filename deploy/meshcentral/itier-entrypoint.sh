#!/bin/bash
# iTier — escribe la config de MeshCentral en CADA arranque a partir de variables
# propias (no depende del modo dinámico del entrypoint oficial, que cambia entre
# versiones) y luego delega en el entrypoint oficial dejando la config como está.
set -eu

CONFIG_FILE="${CONFIG_FILE:-/opt/meshcentral/meshcentral-data/config.json}"
HOST="${MESH_HOSTNAME:-}"
if ! [[ "$HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || [[ "$HOST" != *.* ]]; then
  echo "[iTier] ERROR: MESH_HOSTNAME vacío o inválido ('$HOST'). Ej.: itier-mesh.pymesenlinea.com.ar" >&2
  exit 1
fi

NUEVAS="${MESH_NEW_ACCOUNTS:-false}"
case "${NUEVAS,,}" in true|false) NUEVAS="${NUEVAS,,}" ;; *) NUEVAS=false ;; esac

# Redes desde las que se confía en el X-Forwarded-* (el túnel llega por Docker/loopback).
TLS="${MESH_TLS_OFFLOAD:-172.16.0.0/12,127.0.0.1}"
if ! [[ "$TLS" =~ ^[0-9A-Fa-f.:/,]+$ ]]; then
  echo "[iTier] ERROR: MESH_TLS_OFFLOAD inválido ('$TLS')" >&2
  exit 1
fi
TLS_JSON="[\"${TLS//,/\",\"}\"]"

# Orígenes que pueden EMBEBER MeshCentral en un iframe (la consola de iTier): lista separada por
# comas o espacios, solo https://host[:puerto]. Vacío = no se permite embeber (X-Frame-Options).
FRAME_JSON=""
FRAME_RAW="${MESH_FRAME_ORIGINS:-}"
for o in ${FRAME_RAW//,/ }; do
  if ! [[ "$o" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
    echo "[iTier] ERROR: MESH_FRAME_ORIGINS inválido ('$o')" >&2
    exit 1
  fi
  FRAME_JSON+="${FRAME_JSON:+, }\"$o\""
done
FRAME_LINE=""
[ -n "$FRAME_JSON" ] && FRAME_LINE="\"allowedFramingOrigins\": [$FRAME_JSON],"

mkdir -p "$(dirname "$CONFIG_FILE")"
cat > "$CONFIG_FILE" <<JSON
{
  "settings": {
    "cert": "$HOST",
    "port": 4430,
    "aliasPort": 443,
    "redirPort": 0,
    "mpsPort": 0,
    "tlsOffload": $TLS_JSON,
    "WANonly": true,
    "agentPing": 60,
    "SelfUpdate": false,
    "AllowFraming": false,
    "WebRTC": false,
    $FRAME_LINE
    "plugins": { "enabled": false }
  },
  "domains": {
    "": {
      "certUrl": "$HOST:443",
      "NewAccounts": $NUEVAS,
      "minify": true,
      "localSessionRecording": true
    }
  }
}
JSON
echo "[iTier] config escrita: host=$HOST puerto=4430 (público 443, TLS lo termina Cloudflare) cuentasNuevas=$NUEVAS embeber=${FRAME_JSON:-no}"

export DYNAMIC_CONFIG=false   # el entrypoint oficial deja esta config tal cual
exec /bin/bash /opt/meshcentral/entrypoint.sh "$@"
