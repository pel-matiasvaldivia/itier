#!/bin/bash
# Sustituto mínimo de `ps -A -o ppid,pid,stat,comm` (lo único que usa OpenWA), leyendo /proc.
echo "PPID PID STAT COMMAND"
for d in /proc/[0-9]*; do
  read -r linea < "$d/stat" 2>/dev/null || continue
  pid=${linea%% *}
  comm=${linea#*(}; comm=${comm%)*}
  resto=${linea#*) }
  set -- $resto
  echo "$2 $pid $1 $comm"
done
