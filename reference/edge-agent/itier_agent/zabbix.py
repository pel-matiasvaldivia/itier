"""Cliente de eventos de Zabbix para el edge (JSON-RPC 7.0).

Lee los **problemas activos** del servidor Zabbix (su API) y los entrega como
dicts que `events.FuenteZabbix` convierte en `Evento`. Solo lee: no crea ni
modifica nada (el alta de grupos/proxy la hace la consola). Mismo estilo
JSON-RPC que el adaptador de la consola: `Authorization: Bearer <token>` contra
`api_jsonrpc.php`.

⚠ VERIFICAR CONTRA ZABBIX 7.0 (todo el acoplamiento vive acá):
  - `problem.get` NO acepta `selectHosts`: el host se trae con un segundo
    `event.get` por `eventids` + `selectHosts`, y se unen por eventid.
  - `problem.get` filtra por `groupids` (acotamos al host group del tenant) y
    `recent`. severity: 0 no_clasificada · 1 info · 2 advertencia · 3 media ·
    4 alta · 5 disaster.
"""

from __future__ import annotations

import httpx

# Problemas de ejemplo para el modo demo (misma forma que devuelve problemas()).
_DEMO_PROBLEMAS = [
    {"eventid": "88213", "severity": "4", "name": "uso de disco en volumen D: superó el 90% (92%)",
     "host": "SRV-FILE01", "hostid": "4471", "host_name": "SRV-FILE01", "clock": "1762850040", "acknowledged": "0"},
    {"eventid": "90011", "severity": "5", "name": "el servicio de directorio no responde",
     "host": "SRV-DC01", "hostid": "0001", "host_name": "Controlador de dominio", "clock": "1762850100", "acknowledged": "0"},
]


class ZabbixError(RuntimeError):
    """Falla al hablar con el API de Zabbix. La atrapa el bucle del runner."""


class ZabbixEventos:
    """Lee problemas activos del servidor Zabbix. Pensado para inyectarse en
    `events.FuenteZabbix`."""

    def __init__(self, *, url: str | None, token: str | None, grupo: str | None = None,
                 timeout: float = 15.0, demo: bool = False) -> None:
        self.demo = demo or not (url and token)
        self._endpoint = f"{url.rstrip('/')}/api_jsonrpc.php" if url else ""
        self._token = token
        self._grupo = grupo          # nombre del host group del tenant (opcional)
        self._grupo_id: str | None = None
        self._timeout = timeout
        self._http: httpx.Client | None = None
        self._id = 0

    def __enter__(self) -> "ZabbixEventos":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _rpc(self, metodo: str, params) -> object:
        self._id += 1
        headers = {"Content-Type": "application/json-rpc", "Authorization": f"Bearer {self._token}"}
        cuerpo = {"jsonrpc": "2.0", "method": metodo, "params": params, "id": self._id}
        try:
            r = self._client().post(self._endpoint, headers=headers, json=cuerpo)
        except httpx.HTTPError as e:
            raise ZabbixError(f"no se pudo conectar ({metodo}): {e}") from e
        if r.status_code != 200:
            raise ZabbixError(f"{metodo} → HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise ZabbixError(f"{metodo}: {err.get('message')} {err.get('data')}")
        return data["result"]

    def _grupo_id_de(self) -> str | None:
        """Resuelve el id del host group del tenant por su nombre (una vez)."""
        if not self._grupo:
            return None
        if self._grupo_id is None:
            res = self._rpc("hostgroup.get", {"filter": {"name": [self._grupo]}, "output": ["groupid"]})
            self._grupo_id = res[0]["groupid"] if res else ""
        return self._grupo_id or None

    def problemas(self) -> list[dict]:
        """Problemas activos, cada uno con su host resuelto. Contrato que consume
        `events.FuenteZabbix`."""
        if self.demo:
            return [dict(p) for p in _DEMO_PROBLEMAS]

        params = {
            "output": ["eventid", "name", "severity", "clock", "acknowledged", "objectid"],
            "recent": True,
            "sortfield": "eventid",
            "sortorder": "DESC",
            "limit": 200,
        }
        gid = self._grupo_id_de()
        if gid:
            params["groupids"] = [gid]
        problemas = self._rpc("problem.get", params)
        if not problemas:
            return []

        # problem.get no trae el host: lo resolvemos con event.get + selectHosts.
        ids = [p["eventid"] for p in problemas]
        eventos = self._rpc("event.get", {
            "eventids": ids, "output": ["eventid"],
            "selectHosts": ["hostid", "host", "name"],
        })
        hosts_por_evento = {e["eventid"]: (e.get("hosts") or []) for e in eventos}

        salida: list[dict] = []
        for p in problemas:
            hs = hosts_por_evento.get(p["eventid"], [])
            h = hs[0] if hs else {}
            salida.append({
                "eventid": p.get("eventid"),
                "severity": p.get("severity"),
                "name": p.get("name"),
                "host": h.get("host"),
                "hostid": h.get("hostid"),
                "host_name": h.get("name"),
                "clock": p.get("clock"),
                "acknowledged": p.get("acknowledged"),
            })
        return salida


def cliente(**kwargs) -> ZabbixEventos:
    return ZabbixEventos(**kwargs)
