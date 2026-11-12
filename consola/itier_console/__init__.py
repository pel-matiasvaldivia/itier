"""Consola de iTier — el plano de control.

Una web server-rendered (FastAPI + Jinja2) que NO reemplaza a GLPI ni a Zabbix:
los orquesta por detrás vía sus APIs (patrón backend-for-frontend). Desde un solo
panel, el operador de iTier da de alta clientes, vincula los agentes de Zabbix y
opera tickets, sin entrar a los dashboards nativos.

Ver docs/adr/ADR-0008-plano-de-control-consola.md y consola/README.md.
"""

__version__ = "0.1.0"
