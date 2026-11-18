"""Equipos de todos los clientes para el técnico: estado, alertas y conexión remota.

  GET /equipos                          todos los equipos (filtros: cliente, solo alertas)
  GET /equipos/{slug}/{nodo}/remoto     control remoto de MeshCentral EMBEBIDO en el panel

Los datos salen de GLPI (discos y antivirus del último inventario del agente), MeshCentral
(en línea, enlace remoto) y Zabbix (si el cliente lo tiene). Las lecturas se cachean un
rato: cada pantalla consulta varios sistemas.
"""

from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, perf
from ..adapters import glpi as glpi_mod, mesh as mesh_mod
from ..deps import flash, get_db, render, require_permiso
from ..services import equipos as equipos_svc, onboarding

router = APIRouter()

_TTL_S = 90
_CACHE: dict[str, tuple[float, dict]] = {}


def _datos(cfg, cli: dict, forzar: bool) -> dict:
    ahora = time.monotonic()
    clave = cli["slug"]
    hit = _CACHE.get(clave)
    if hit and not forzar and ahora - hit[0] < _TTL_S:
        return hit[1]
    datos = equipos_svc.listar(cfg, cli, salud=True)
    _CACHE[clave] = (ahora, datos)
    return datos


@router.get("/equipos", response_class=HTMLResponse)
def lista(request: Request, cliente: str = "", alertas: str = "", actualizar: str = "",
          user: dict = Depends(require_permiso("equipos")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    if actualizar:   # «Actualizar datos»: también se salta el caché de lecturas de GLPI
        glpi_mod._LECTURAS.vaciar()
    clientes = [c for c in onboarding.listar_clientes(conn) if c.get("estado") == "activo"]
    todos = [{"slug": c["slug"], "nombre": c["nombre"]} for c in clientes]
    if cliente:
        clientes = [c for c in clientes if c["slug"] == cliente]
    filas, notas = [], []
    # Cada cliente consulta GLPI, Mesh y Zabbix por separado: en paralelo la pantalla tarda lo del más lento.
    resultados = perf.paralelo([(lambda c=c: _datos(cfg, c, bool(actualizar))) for c in clientes])
    for c, (ok, d) in zip(clientes, resultados):
        if not ok:
            notas.append(f"{c['nombre']}: {getattr(d, 'detalle', d)}"[:160])
            continue
        for e in d["equipos"]:
            filas.append({**e, "cliente": c["nombre"], "slug": c["slug"]})
        if d["notas"]:
            notas.append(f"{c['nombre']}: {', '.join(d['notas'])}")
    n_alertas = sum(1 for f in filas if f["alertas"])
    if alertas:
        filas = [f for f in filas if f["alertas"]]
    gravedad = {"alta": 0, "media": 1, "baja": 2}
    filas.sort(key=lambda f: (min((gravedad[a[0]] for a in f["alertas"]), default=3), f["cliente"], f["nombre"]))
    return render(
        request, "equipos.html", filas=filas, clientes=todos, filtro_cliente=cliente, solo_alertas=bool(alertas),
        total=len(filas) if not alertas else None, n_alertas=n_alertas,
        n_linea=sum(1 for f in filas if f["en_linea"]), notas=notas, umbral=cfg.alerta_disco_pct,
        mesh_ok=cfg.mesh_enabled)


@router.get("/equipos/{slug}/{nodo}/remoto", response_class=HTMLResponse)
def remoto(request: Request, slug: str, nodo: str, n: str = "", user: dict = Depends(require_permiso("equipos.remoto")),
           conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli or not cfg.mesh_enabled or not mesh_mod.nodo_valido(nodo):
        flash(request, "No se puede abrir la conexión remota de ese equipo.", "error")
        return RedirectResponse("/equipos", status_code=303)
    nombre = (n or "equipo")[:64]
    audit.registrar(conn, user["user"], "equipo.remoto.abierto", {"cliente": cli["nombre"], "equipo": nombre})
    return render(request, "equipo_remoto.html", cli=cli, nombre=nombre, src=mesh_mod.url_remoto(cfg, nodo),
                  mesh_url=cfg.mesh_url)
