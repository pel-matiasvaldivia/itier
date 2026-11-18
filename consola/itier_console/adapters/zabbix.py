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

from .. import perf
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


_DEMO_USUARIOS: dict[str, dict] = {}

# Token de la API reusado entre requests (user.login por cada pantalla era lento y llenaba el registro de Zabbix).
_TOKENS = perf.Cache()
_TOKEN_TTL_S = 900


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

    def _cacheando(self) -> bool:
        return (not self.demo) and not self.cfg.zbx_token and getattr(self.cfg, "adapter_cache_s", 0) > 0

    def _login(self) -> str:
        if self._token:
            return self._token
        clave = (self._endpoint, self.cfg.zbx_user)
        if self._cacheando():
            tok = _TOKENS.fresco(clave, _TOKEN_TTL_S)
            if tok:
                self._token, self._de_cache = tok, True
                return tok
        self._de_cache = False
        self._token = self._rpc("user.login", {"username": self.cfg.zbx_user, "password": self.cfg.zbx_password}, auth=False)
        if self._cacheando():
            _TOKENS.poner(clave, self._token)
        return self._token

    def _rpc(self, metodo: str, params: Any, auth: bool = True) -> Any:
        self._id += 1
        headers = {"Content-Type": "application/json-rpc"}
        if auth and metodo != "apiinfo.version":
            headers["Authorization"] = f"Bearer {self._login()}"
        cuerpo = {"jsonrpc": "2.0", "method": metodo, "params": params, "id": self._id}
        try:
            with perf.medir("zabbix"):
                r = self._client().post(self._endpoint, headers=headers, json=cuerpo)
        except httpx.HTTPError as e:
            raise AdapterError("Zabbix", f"no se pudo conectar ({metodo}): {e}") from e
        if r.status_code != 200:
            raise AdapterError("Zabbix", f"{metodo} → HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "error" in data:
            err = data["error"]
            texto = f"{err.get('message')} {err.get('data')}"
            if auth and getattr(self, "_de_cache", False) and ("re-login" in texto or "Session terminated" in texto
                                                               or "Not authorized" in texto or "Not authorised" in texto):
                _TOKENS.quitar((self._endpoint, self.cfg.zbx_user))   # token vencido: se vuelve a loguear una vez
                self._token, self._de_cache = None, False
                return self._rpc(metodo, params, auth=auth)
            raise AdapterError("Zabbix", f"{metodo}: {texto}")
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

    # -- autoregistro de agentes ---------------------------------------------

    def asegurar_autoregistro(self, nombre: str, metadata: str, groupid: str,
                              plantilla: str = "Windows by Zabbix agent active") -> str:
        """Crea (si falta) la acción de AUTORREGISTRO: todo agente activo cuyo
        HostMetadata contenga `metadata` se da de alta solo en el grupo del
        cliente (y con la plantilla, si existe). Idempotente por nombre.

        ⚠ 7.0: action.create con eventsource=2; conditiontype 24 = host metadata,
          operator 2 = contiene; operationtype 4 = agregar a grupo, 6 = plantilla.
        """
        if self.demo:
            return str(600 + (abs(hash(nombre)) % 99))
        res = self._rpc("action.get", {"filter": {"name": [nombre], "eventsource": 2}, "output": ["actionid"]})
        if res:
            return res[0]["actionid"]
        operaciones: list[dict] = [{"operationtype": 4, "opgroup": [{"groupid": groupid}]}]
        try:
            t = self._rpc("template.get", {"filter": {"host": [plantilla]}, "output": ["templateid"]})
            if t:
                operaciones.append({"operationtype": 6, "optemplate": [{"templateid": t[0]["templateid"]}]})
        except AdapterError:
            pass
        res = self._rpc("action.create", {
            "name": nombre, "eventsource": 2, "status": 0,
            "filter": {"evaltype": 0, "conditions": [{"conditiontype": 24, "operator": 2, "value": metadata}]},
            "operations": operaciones,
        })
        return res["actionids"][0]

    # -- proxy ---------------------------------------------------------------

    def proxy_por_nombre(self, nombre: str) -> dict | None:
        if self.demo:
            return _DEMO_PROXIES.get(nombre)
        # ⚠ 7.0: el proxy se identifica por `name`.
        res = self._rpc("proxy.get", {"filter": {"name": [nombre]},
                                       "output": ["proxyid", "name", "operating_mode", "lastaccess"]})
        return res[0] if res else None

    # -- usuarios de plataforma (técnicos / PM / administradores) ---------------

    # rol de iTier → tipo de rol de Zabbix (1 User · 2 Admin · 3 Super admin)
    TIPOS = {"admin": 3, "pm": 2, "tecnico": 1}

    def _rol_id(self, rol: str) -> str:
        tipo = self.TIPOS[rol]
        for r in self._rpc("role.get", {"output": ["roleid", "name", "type"]}):
            if str(r.get("type")) == str(tipo):
                return str(r["roleid"])
        raise AdapterError("Zabbix", f"no existe un rol de tipo {tipo} para «{rol}»")

    def grupo_usuarios(self, nombre: str) -> str:
        """Id del grupo de usuarios de soporte; lo crea (sin permisos) si no existe."""
        if self.demo:
            return "90"
        r = self._rpc("usergroup.get", {"filter": {"name": [nombre]}, "output": ["usrgrpid"]})
        if r:
            return str(r[0]["usrgrpid"])
        return str(self._rpc("usergroup.create", {"name": nombre})["usrgrpids"][0])

    def dar_acceso_grupo(self, nombre_grupo_usuarios: str, hostgroup_id: str, permiso: int = 3) -> None:
        """Da al grupo de usuarios de soporte acceso al grupo de hosts de un cliente (3 = lectura y
        escritura: permite reconocer problemas; el ROL del usuario limita lo que puede editar)."""
        if self.demo or not hostgroup_id:
            return
        gid = self.grupo_usuarios(nombre_grupo_usuarios)
        actual = self._rpc("usergroup.get", {"usrgrpids": [gid], "output": ["usrgrpid"],
                                             "selectHostGroupRights": "extend"})
        derechos = {str(d["id"]): d for d in (actual[0].get("hostgroup_rights") if actual else []) or []}
        if str(derechos.get(str(hostgroup_id), {}).get("permission")) == str(permiso):
            return
        derechos[str(hostgroup_id)] = {"id": str(hostgroup_id), "permission": str(permiso)}
        self._rpc("usergroup.update", {"usrgrpid": gid, "hostgroup_rights": list(derechos.values())})

    def buscar_usuario(self, username: str) -> dict | None:
        if self.demo:
            return _DEMO_USUARIOS.get(username.lower())
        r = self._rpc("user.get", {"filter": {"username": [username]}, "output": ["userid", "username"]})
        return {"id": str(r[0]["userid"])} if r else None

    def asegurar_usuario(self, username: str, password: str, nombre: str, rol: str,
                         grupo_soporte: str) -> tuple[str, str]:
        """Crea el usuario o, si ya existe, le fija clave, rol y grupo. → (id, 'creado'|'actualizado')."""
        if self.demo:
            ya = username.lower() in _DEMO_USUARIOS
            _DEMO_USUARIOS[username.lower()] = {"id": str(800 + len(_DEMO_USUARIOS))}
            return _DEMO_USUARIOS[username.lower()]["id"], "actualizado" if ya else "creado"
        grupo = self.grupo_usuarios(grupo_soporte)
        base = {"roleid": self._rol_id(rol), "usrgrps": [{"usrgrpid": grupo}]}
        ex = self.buscar_usuario(username)
        if ex:
            self._rpc("user.update", {"userid": ex["id"], "passwd": password, **base})
            return ex["id"], "actualizado"
        partes = (nombre or username).split(" ", 1)
        r = self._rpc("user.create", {"username": username, "passwd": password, "name": partes[0],
                                      "surname": partes[1] if len(partes) > 1 else "", **base})
        return str(r["userids"][0]), "creado"

    def fijar_password_usuario(self, uid: str, password: str) -> None:
        if not self.demo:
            self._rpc("user.update", {"userid": uid, "passwd": password})

    def cambiar_rol_usuario(self, uid: str, rol: str) -> None:
        if not self.demo:
            self._rpc("user.update", {"userid": uid, "roleid": self._rol_id(rol)})

    def activar_usuario(self, uid: str, activo: bool, grupo_soporte: str) -> None:
        """Baja = pasarlo al grupo integrado «Disabled»; alta = devolverlo al grupo de soporte."""
        if self.demo:
            return
        if activo:
            grupos = [{"usrgrpid": self.grupo_usuarios(grupo_soporte)}]
        else:
            r = self._rpc("usergroup.get", {"filter": {"name": ["Disabled"]}, "output": ["usrgrpid"]})
            if not r:
                raise AdapterError("Zabbix", "no existe el grupo «Disabled» para dar de baja al usuario")
            grupos = [{"usrgrpid": str(r[0]["usrgrpid"])}]
        self._rpc("user.update", {"userid": uid, "usrgrps": grupos})

    def eliminar_usuario(self, uid: str) -> None:
        if not self.demo:
            self._rpc("user.delete", [uid])

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
