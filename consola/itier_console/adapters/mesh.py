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

# Derechos de los técnicos sobre cada grupo de cliente: gestionar equipos, control remoto,
# terminal/consola, archivos, wake-on-lan y notas. NO editar el grupo ni administrar usuarios.
DERECHOS_SOPORTE = 4 | 8 | 16 | 32 | 64 | 128

# Demo: un equipo ya conectado en cualquier grupo.
_DEMO_EQUIPOS = [{"nombre": "PC-VENTAS-01", "conectado": True, "nodeid": "DEMONODO01@x"}]


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

    def _pedir(self, accion: dict, esperar: str, responseid: str = "itier", ok=None) -> dict:
        """Envía una acción y devuelve la primera respuesta del tipo esperado.

        Por defecto un `result` de texto distinto de "ok" es un error; `ok(msg)` permite
        otro criterio (addmeshuser responde result vacío + success/failed)."""
        accion = {**accion, "responseid": responseid}
        self._ws.send(json.dumps(accion))
        limite = time.monotonic() + _ESPERA_S
        while time.monotonic() < limite:
            try:
                msg = json.loads(self._ws.recv(timeout=max(0.1, limite - time.monotonic())))
            except TimeoutError:
                break
            except Exception as e:  # noqa: BLE001
                raise AdapterError("mesh", f"conexión interrumpida durante «{accion.get('action')}»: {e}") from e
            if msg.get("action") == esperar and msg.get("responseid", responseid) == responseid:
                if ok is not None:
                    if not ok(msg):
                        raise AdapterError("mesh", f"{esperar}: {msg.get('result') or 'sin cambios'}")
                elif isinstance(msg.get("result"), str) and msg["result"] != "ok":
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
        completo = self._buscar(nombre)
        if not completo:
            self._pedir({"action": "createmesh", "meshname": nombre, "meshtype": 2,
                         "desc": "Cliente iTier"}, "createmesh")
            completo = self._buscar(nombre)
        if not completo:
            raise AdapterError("mesh", f"se creó el grupo «{nombre}» pero no aparece en la lista")
        return id_corto(completo)

    def _buscar(self, nombre: str) -> str | None:
        for g in self._grupos():
            if g.get("name") == nombre and g.get("mtype") == 2:
                return g["_id"]
        return None

    def _grupo_usuarios(self, nombre: str) -> str:
        """Id del grupo de usuarios (técnicos); lo crea si no existe."""
        def buscar() -> str | None:
            for gid, g in (self._pedir({"action": "usergroups"}, "usergroups").get("ugroups") or {}).items():
                if g.get("name") == nombre:
                    return gid
            return None
        gid = buscar()
        if gid:
            return gid
        try:
            r = self._pedir({"action": "createusergroup", "name": nombre,
                             "desc": "Técnicos de soporte iTier"}, "createusergroup")
        except AdapterError as e:
            raise AdapterError("mesh", f"no se pudo crear el grupo de usuarios «{nombre}» ({e.detalle}). Creálo en MeshCentral "
                               "(Mis usuarios → Grupos de usuarios) o dale a la cuenta de servicio el permiso "
                               "«Administrar grupos de usuarios».") from e
        return r.get("ugrpid") or buscar() or ""

    def compartir_con_soporte(self, mesh_id: str) -> None:
        """Da acceso al grupo del cliente al grupo de usuarios de soporte (los técnicos son
        miembros de ese grupo). Idempotente. Los grupos de dispositivos solo los ven su dueño
        (la cuenta de servicio) y quienes tengan acceso."""
        nombre = self._cfg.mesh_support_group
        if self._cfg.demo or not nombre:
            return
        meshid = next((g["_id"] for g in self._grupos() if id_corto(g["_id"]) == mesh_id), None)
        if not meshid:
            raise AdapterError("mesh", f"no se encontró el grupo de equipos {mesh_id} para compartirlo")
        if not hasattr(self, "_ugrp"):
            self._ugrp = self._grupo_usuarios(nombre)
        if not self._ugrp:
            raise AdapterError("mesh", f"no se encontró el grupo de usuarios «{nombre}»")
        self._pedir({"action": "addmeshuser", "meshid": meshid, "userids": [self._ugrp],
                     "meshadmin": DERECHOS_SOPORTE}, "addmeshuser",
                    ok=lambda m: m.get("failed", 0) == 0 and m.get("success", 0) >= 1)

    # -- usuarios de plataforma (técnicos / PM / administradores) ---------------

    # rol de iTier → derechos de sitio en Mesh. 64 = «no puede crear grupos de equipos»;
    # 32 = cuenta bloqueada. La cuenta de servicio necesita «Gestionar usuarios» (y ser administradora
    # completa para crear otros administradores).
    DERECHOS_SITIO = {"admin": 0xFFFFFFFF, "pm": 64, "tecnico": 64}
    BLOQUEADA = 32

    def _en_soporte(self, username: str, grupo: str, agregar: bool) -> None:
        if not grupo:
            return
        ugrp = self._grupo_usuarios(grupo)
        if agregar:
            self._pedir({"action": "addusertousergroup", "ugrpid": ugrp, "usernames": [username]}, "addusertousergroup")
        else:
            dominio = ugrp.split("/")[1] if ugrp.count("/") >= 2 else ""
            self._pedir({"action": "removeuserfromusergroup", "ugrpid": ugrp,
                         "userid": f"user/{dominio}/{username.lower()}"}, "removeuserfromusergroup")

    def asegurar_usuario(self, username: str, password: str, email: str, rol: str, grupo_soporte: str) -> str:
        """Crea la cuenta (con cambio de clave obligatorio en el primer login de Mesh) y la suma al
        grupo de soporte. → 'creado' | 'existente' (si ya estaba: NO se puede fijar su clave por API)."""
        if self._cfg.demo:
            return "creado"
        try:
            self._pedir({"action": "adduser", "username": username, "pass": password, "email": email,
                         "emailVerified": True, "resetNextLogin": True,
                         "siteadmin": self.DERECHOS_SITIO[rol]}, "adduser")
            estado = "creado"
        except AdapterError as e:
            if "already exists" not in e.detalle:
                raise
            estado = "existente"
        self._en_soporte(username, grupo_soporte, True)
        return estado

    def cambiar_rol_usuario(self, username: str, rol: str) -> None:
        if not self._cfg.demo:
            self._pedir({"action": "edituser", "userid": username, "siteadmin": self.DERECHOS_SITIO[rol]}, "edituser")

    def bloquear_usuario(self, username: str, bloquear: bool, rol: str, grupo_soporte: str) -> None:
        """Baja = cuenta bloqueada + fuera del grupo de soporte; alta = derechos del rol + de vuelta al grupo."""
        if self._cfg.demo:
            return
        self._pedir({"action": "edituser", "userid": username,
                     "siteadmin": self.BLOQUEADA if bloquear else self.DERECHOS_SITIO[rol]}, "edituser")
        self._en_soporte(username, grupo_soporte, not bloquear)

    def eliminar_usuario(self, username: str) -> None:
        if not self._cfg.demo:
            self._pedir({"action": "deleteuser", "userid": username}, "deleteuser")

    def equipos(self, mesh_id: str) -> list[dict]:
        """Equipos del grupo: [{nombre, conectado, nodeid}] (nodeid corto, para el enlace remoto)."""
        if self._cfg.demo:
            return list(_DEMO_EQUIPOS)
        r = self._pedir({"action": "nodes"}, "nodes")
        salida = []
        for clave, nodos in (r.get("nodes") or {}).items():
            if id_corto(clave) != mesh_id:
                continue
            for n in nodos:
                salida.append({"nombre": str(n.get("name", "")).upper(), "conectado": bool(int(n.get("conn", 0)) & 1),
                               "nodeid": id_corto(n.get("_id", ""))})
        return salida


def cliente(cfg) -> MeshClient:
    return MeshClient(cfg)


def url_agente_windows(cfg, mesh_id: str) -> str:
    """Enlace de descarga del agente Windows x64 (instalación en segundo plano)."""
    return f"{cfg.mesh_url}/meshagents?id=4&meshid={quote(mesh_id, safe='@$')}&installflags=2"


_RE_NODO = r"^[A-Za-z0-9@$_+\-]{8,120}$"


def nodo_valido(nodeid: str) -> bool:
    import re
    return bool(re.match(_RE_NODO, nodeid or ""))


def url_remoto(cfg, nodeid: str) -> str:
    """Enlace a la vista de escritorio remoto del equipo (viewmode=11) en MeshCentral."""
    return f"{cfg.mesh_url}/?viewmode=11&gotonode={quote(nodeid, safe='@$')}"
