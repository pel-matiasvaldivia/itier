"""Adaptador de Zabbix (eventos y métricas).

Habla con la API JSON-RPC 2.0 de Zabbix (api_jsonrpc.php). Crea el grupo de hosts
y el proxy de cada cliente, y lista/vincula sus hosts. En modo demo devuelve
fixtures.

Autenticación: Zabbix 7.0 usa el header `Authorization: Bearer <token>` (la
propiedad "auth" del cuerpo fue removida). Si configurás ITIER_ZBX_TOKEN se usa
directo; si no, se hace user.login con usuario/clave para obtener el token.

⚠ VERIFICAR CONTRA ZABBIX 7.0:
  - proxy.create cambió en 7.0: usa `name` (antes `host`) y `operating_mode`
    (antes `status`). Para un proxy ACTIVO (el del cliente marca al server),
    operating_mode=0 y en el server tls_accept incluye PSK (2).
  - host.get: disponibilidad vive a nivel de interfaz (interface.available), no
    del host. Grupos vía selectHostGroups.
  Todo el acoplamiento con Zabbix vive en ESTE archivo.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import AdapterError

# ── Fixtures de demo ────────────────────────────────────────────────────────

_DEMO_PROXIES = {
    "itier-proxy-ferreteria-san-martin": {"proxyid": "10", "name": "itier-proxy-ferreteria-san-martin",
                                           "operating_mode": "0", "lastaccess": "1762850040"},
    "itier-proxy-estudio-contable-paz": {"proxyid": "11", "name": "itier-proxy-estudio-contable-paz",
                                         "operating_mode": "0", "lastaccess": "0"},
}

_DEMO_HOSTS = [
    {"hostid": "501", "host": "SRV-DC01", "name": "Controlador de dominio", "status": "0",
     "proxyid": "10", "available": "1", "grupo": "iTier/Ferretería San Martín"},
    {"hostid": "502", "host": "NAS-01", "name": "NAS respaldo", "status": "0",
     "proxyid": "10", "available": "2", "grupo": "iTier/Ferretería San Martín"},
    {"hostid": "503", "host": "FW-EDGE", "name": "Firewall perimetral", "status": "0",
     "proxyid": "10", "available": "1", "grupo": "iTier/Ferretería San Martín"},
]

DISPONIBILIDAD = {"0": "Desconocida", "1": "Disponible", "2": "No disponible"}
ESTADO_HOST = {"0": "Monitoreado", "1": "Sin monitorear"}


class ZabbixClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.demo = cfg.demo or not (cfg.zbx_url and (cfg.zbx_token or (cfg.zbx_user and cfg.zbx_password)))
        self._endpoint = f"{cfg.zbx_url}/api_jsonrpc.php" if cfg.zbx_url else ""
        self._token: str | None = cfg.zbx_token
        self._http: httpx.Client | None = None
        self._id = 0

    def __enter__(self) -> "ZabbixClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self.cfg.http_timeout_s)
        return self._http

    def _login(self) -> str:
        if self._token:
            return self._token
        self._token = self._rpc("user.login", {"username": self.cfg.zbx_user, "password": self.cfg.zbx_password}, auth=False)
        return self._token

    def _rpc(self, metodo: str, params: Any, auth: bool = True) -> Any:
        self._id += 1
        headers = {"Content-Type": "application/json-rpc"}
        if auth and metodo != "apiinfo.version":
            headers["Authorization"] = f"Bearer {self._login()}"
        cuerpo = {"jsonrpc": "2.0", "method": metodo, "params": params, "id": self._id}
        try:
            r = self._client().post(self._endpoint, headers=headers, json=cuerpo)
        except httpx.HTTPError as e:
            raise AdapterError("Zabbix", f"no se pudo conectar ({metodo}): {e}") from e
        if r.status_code != 200:
            raise AdapterError("Zabbix", f"{metodo} → HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise AdapterError("Zabbix", f"{metodo}: {err.get('message')} {err.get('data')}")
        return data["result"]

    # -- salud ---------------------------------------------------------------

    def health(self) -> dict:
        if self.demo:
            return {"ok": True, "detalle": "demo", "version": "7.0 (demo)"}
        try:
            ver = self._rpc("apiinfo.version", {}, auth=False)
            self._login()
            return {"ok": True, "detalle": "autenticado", "version": ver}
        except AdapterError as e:
            return {"ok": False, "detalle": e.detalle}

    # -- grupos de host ------------------------------------------------------

    def grupo_por_nombre(self, nombre: str) -> dict | None:
        if self.demo:
            return None
        res = self._rpc("hostgroup.get", {"filter": {"name": [nombre]}, "output": ["groupid", "name"]})
        return res[0] if res else None

    def crear_grupo(self, nombre: str) -> str:
        if self.demo:
            return str(700 + (abs(hash(nombre)) % 99))
        existente = self.grupo_por_nombre(nombre)
        if existente:
            return existente["groupid"]
        res = self._rpc("hostgroup.create", {"name": nombre})
        return res["groupids"][0]

    # -- proxy ---------------------------------------------------------------

    def proxy_por_nombre(self, nombre: str) -> dict | None:
        if self.demo:
            return _DEMO_PROXIES.get(nombre)
        # ⚠ 7.0: el proxy se identifica por `name`.
        res = self._rpc("proxy.get", {"filter": {"name": [nombre]},
                                       "output": ["proxyid", "name", "operating_mode", "lastaccess"]})
        return res[0] if res else None

    def crear_proxy(self, nombre: str, psk_identity: str, psk_key: str) -> str:
        """Crea un proxy ACTIVO con PSK. Devuelve el proxyid."""
        if self.demo:
            return str(800 + (abs(hash(nombre)) % 99))
        existente = self.proxy_por_nombre(nombre)
        if existente:
            return existente["proxyid"]
        # ⚠ 7.0: operating_mode=0 (activo); tls_accept=2 (PSK) para que el server
        #   acepte la conexión entrante del proxy del cliente.
        params = {
            "name": nombre,
            "operating_mode": 0,
            "tls_accept": 2,
            "tls_psk_identity": psk_identity,
            "tls_psk": psk_key,
        }
        res = self._rpc("proxy.create", params)
        return res["proxyids"][0]

    def estado_proxy(self, nombre: str) -> dict | None:
        p = self.proxy_por_nombre(nombre)
        if not p:
            return None
        last = p.get("lastaccess", "0")
        return {
            "proxyid": p.get("proxyid"),
            "name": p.get("name"),
            "lastaccess": last,
            "visto": last not in (None, "0", 0),
        }

    # -- hosts ---------------------------------------------------------------

    def listar_hosts(self, groupid: str | None) -> list[dict]:
        if self.demo:
            return list(_DEMO_HOSTS)
        if not groupid:
            return []
        res = self._rpc("host.get", {
            "groupids": [groupid],
            "output": ["hostid", "host", "name", "status", "proxyid"],
            "selectInterfaces": ["available"],
            "selectHostGroups": ["name"],
        })
        salida = []
        for h in res:
            disp = "0"
            for itf in h.get("interfaces", []):
                if itf.get("available") in ("1", "2"):
                    disp = itf["available"]
                    break
            grupos = ", ".join(g.get("name", "") for g in h.get("hostgroups", []))
            salida.append({
                "hostid": h["hostid"], "host": h.get("host"), "name": h.get("name"),
                "status": h.get("status"), "proxyid": h.get("proxyid"), "available": disp,
                "grupo": grupos,
            })
        return salida

    def vincular_host(self, hostid: str, proxyid: str, groupid: str) -> None:
        """Asigna un host al proxy del cliente y lo suma a su grupo."""
        if self.demo:
            return
        # ⚠ 7.0: en host.update el campo es proxyid; los grupos se reemplazan con
        #   la lista completa, así que primero leemos los actuales y agregamos.
        actuales = self._rpc("host.get", {"hostids": [hostid], "selectHostGroups": ["groupid"]})
        grupos = {g["groupid"] for g in (actuales[0].get("hostgroups", []) if actuales else [])}
        grupos.add(groupid)
        self._rpc("host.update", {
            "hostid": hostid,
            "proxyid": proxyid,
            "groups": [{"groupid": g} for g in grupos],
        })


def cliente(cfg) -> ZabbixClient:
    return ZabbixClient(cfg)
