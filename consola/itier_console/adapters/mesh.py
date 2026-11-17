"""Adaptador de MeshCentral (soporte remoto + estado de los equipos).

Una conexión WebSocket a /control.ashx autenticada con el header `x-meshauth`
(usuario y clave en base64: es lo que usa su propio `meshctrl.js`). Con eso:

  - cada cliente tiene un **grupo de dispositivos** («itier-<slug>»);
  - el instalador del agente se baja SIN login de /meshagents?id=4&meshid=… (id 4 =
    Windows x64 servicio; installflags=2 = instalación en segundo plano, sin
    pedir consentimiento) mientras `lockAgentDownload` no esté activado;
  - `nodes` devuelve los equipos con su estado de conexión (conn & 1 = agente
    conectado), que la consola cruza con lo que reportó el instalador.

Acciones verificadas contra el código de MeshCentral (meshctrl.js/meshuser.js):
meshes, createmesh, nodes. ⚠ No se probó contra un servidor real de ustedes.
Todo el acoplamiento con MeshCentral vive en ESTE archivo.
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import quote

from . import AdapterError

_ESPERA_S = 15.0

# Demo: un equipo ya conectado en cualquier grupo.
_DEMO_EQUIPOS = [{"nombre": "PC-VENTAS-01", "conectado": True}]


def id_corto(meshid: str) -> str:
    """'mesh//AbC@$…' → 'AbC@$…' (lo que usa /meshagents y la invitación)."""
    return (meshid or "").split("/")[-1]


class MeshClient:
    def __init__(self, cfg):
        self._cfg = cfg
        self._ws = None

    # ── conexión ────────────────────────────────────────────────────────────
    def __enter__(self) -> "MeshClient":
        if self._cfg.demo:
            return self
        try:
            from websockets.sync.client import connect
        except ImportError as e:  # pragma: no cover
            raise AdapterError("mesh", "falta la librería 'websockets'") from e
        url = self._cfg.mesh_url.replace("https://", "wss://").replace("http://", "ws://") + "/control.ashx"
        cred = (base64.b64encode(self._cfg.mesh_user.encode()).decode() + ","
                + base64.b64encode(self._cfg.mesh_password.encode()).decode())
        try:
            self._ws = connect(url, additional_headers={"x-meshauth": cred}, open_timeout=_ESPERA_S,
                               max_size=8 * 1024 * 1024)
        except Exception as e:  # noqa: BLE001 — cualquier falla de red/handshake
            raise AdapterError("mesh", f"no se pudo conectar a {self._cfg.mesh_url}: {e}") from e
        return self

    def __exit__(self, *exc) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _pedir(self, accion: dict, esperar: str, responseid: str = "itier") -> dict:
        """Envía una acción y devuelve la primera respuesta del tipo esperado."""
        accion = {**accion, "responseid": responseid}
        self._ws.send(json.dumps(accion))
        limite = time.monotonic() + _ESPERA_S
        while time.monotonic() < limite:
            try:
                msg = json.loads(self._ws.recv(timeout=max(0.1, limite - time.monotonic())))
            except TimeoutError:
                break
            except Exception as e:  # noqa: BLE001
                raise AdapterError("mesh", f"conexión interrumpida: {e}") from e
            if msg.get("action") == esperar and msg.get("responseid", responseid) == responseid:
                if isinstance(msg.get("result"), str) and msg["result"] != "ok":
                    raise AdapterError("mesh", f"{esperar}: {msg['result']}")
                return msg
        raise AdapterError("mesh", f"sin respuesta a «{esperar}»")

    # ── operaciones ─────────────────────────────────────────────────────────
    def _grupos(self) -> list[dict]:
        return self._pedir({"action": "meshes"}, "meshes").get("meshes", [])

    def asegurar_grupo(self, nombre: str) -> str:
        """Devuelve el id corto del grupo del cliente; lo crea si no existe."""
        if self._cfg.demo:
            return "MESHDEMO@" + nombre.split("-", 1)[-1]
        for g in self._grupos():
            if g.get("name") == nombre and g.get("mtype") == 2:
                return id_corto(g["_id"])
        self._pedir({"action": "createmesh", "meshname": nombre, "meshtype": 2,
                     "desc": "Cliente iTier"}, "createmesh")
        for g in self._grupos():
            if g.get("name") == nombre and g.get("mtype") == 2:
                return id_corto(g["_id"])
        raise AdapterError("mesh", f"se creó el grupo «{nombre}» pero no aparece en la lista")

    def equipos(self, mesh_id: str) -> list[dict]:
        """Equipos del grupo: [{nombre, conectado}]."""
        if self._cfg.demo:
            return list(_DEMO_EQUIPOS)
        r = self._pedir({"action": "nodes"}, "nodes")
        salida = []
        for clave, nodos in (r.get("nodes") or {}).items():
            if id_corto(clave) != mesh_id:
                continue
            for n in nodos:
                salida.append({"nombre": str(n.get("name", "")).upper(), "conectado": bool(int(n.get("conn", 0)) & 1)})
        return salida


def cliente(cfg) -> MeshClient:
    return MeshClient(cfg)


def url_agente_windows(cfg, mesh_id: str) -> str:
    """Enlace de descarga del agente Windows x64 (instalación en segundo plano)."""
    return f"{cfg.mesh_url}/meshagents?id=4&meshid={quote(mesh_id, safe='@$')}&installflags=2"
