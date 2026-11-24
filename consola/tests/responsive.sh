#!/usr/bin/env bash
# Falla si alguna pantalla desborda horizontalmente en celular, tablet, laptop o monitor ancho.
# Uso: cd consola && tests/responsive.sh   (requiere node + playwright global y el venv)
set -u
OUT=$(mktemp -d); fallo=0
for W in 390 820 1366 1900; do
  salida=$(NODE_PATH=$(npm root -g) node tests/responsive_check.js "$OUT" $W 800 2>&1)
  echo "$salida" | grep -E "OVERFLOW-X|anchos:" && { echo "  ^ ancho $W"; fallo=1; }
done
[ $fallo = 0 ] && echo "RESPONSIVE OK (390, 820, 1366, 1900)" || { echo "RESPONSIVE: hay desbordes"; exit 1; }
