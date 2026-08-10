#!/bin/sh
# Crea el rol y la base de datos de Zabbix junto a los de GLPI.
#
# El entrypoint oficial de Postgres ejecuta los .sh de
# /docker-entrypoint-initdb.d SOLO en la primera inicialización (cuando el
# directorio de datos está vacío). GLPI ya queda creado por POSTGRES_DB /
# POSTGRES_USER; aquí sumamos la base de Zabbix sobre el mismo motor.
#
# ZABBIX_DB_PASSWORD llega desde el environment del servicio `db`.
# Evitá comillas simples en esa contraseña: se interpola en SQL literal.
set -e

if [ -z "${ZABBIX_DB_PASSWORD}" ]; then
  echo "ERROR: ZABBIX_DB_PASSWORD no está definida; no puedo crear la base de Zabbix." >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-EOSQL
    CREATE ROLE zabbix WITH LOGIN PASSWORD '${ZABBIX_DB_PASSWORD}';
    CREATE DATABASE zabbix OWNER zabbix;
EOSQL

echo "Base y rol 'zabbix' creados. El servidor Zabbix importará su esquema al primer arranque."
