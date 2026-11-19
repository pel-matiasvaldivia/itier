"""Activos de un cliente (notebooks, desktops, monitores, celulares…) y su asignación a personas."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, onboarding
from ..services import activos as svc

router = APIRouter()
_CAMPOS = ("tipo", "etiqueta", "marca", "modelo", "serie", "imei", "linea", "fecha_compra", "garantia_hasta", "notas")


def _cli(request, conn, slug):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
    return cli


def _csrf(request, csrf) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


def _fuera() -> RedirectResponse:
    return RedirectResponse("/clientes", status_code=303)


def _vuelta(slug, aid=None) -> RedirectResponse:
    return RedirectResponse(f"/clientes/{slug}/activos" + (f"/{aid}" if aid else ""), status_code=303)


def _lista(request, conn, cli, f, **extra):
    """Pantalla de la lista, con el cruce contra los equipos que reporta el agente de GLPI."""
    activos = svc.listar(conn, cli["id"], f["tipo"], f["estado"], f["persona"], f["q"])
    equipos, error = svc.equipos_glpi(request.app.state.cfg, cli)
    svc.vista_agente(activos, equipos)
    return render(request, "activos_lista.html", cli=cli, activos=activos, resumen=svc.resumen(conn, cli["id"]), personas=svc.personas(conn, cli["id"]),
                  TIPOS=svc.TIPOS, ESTADOS=svc.ESTADOS, f=f, glpi_ok=bool(request.app.state.cfg.glpi_configurado and cli.get("glpi_entity_id")),
                  glpi_error=error, pendientes_glpi=len(svc.pendientes_glpi(conn, cli["id"], equipos)), **extra)


@router.get("/clientes/{slug}/activos", response_class=HTMLResponse)
def lista(request: Request, slug: str, tipo: str = "", estado: str = "", persona: str = "", q: str = "",
          user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    return _lista(request, conn, cli, {"tipo": tipo, "estado": estado, "persona": persona, "q": q})


@router.get("/clientes/{slug}/activos/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, slug: str, tipo: str = "notebook", user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    return render(request, "activo_form.html", cli=cli, a=None, v={"tipo": tipo}, personas=svc.personas(conn, cli["id"]), TIPOS=svc.TIPOS)


@router.post("/clientes/{slug}/activos/nuevo", response_class=HTMLResponse)
async def nuevo_post(request: Request, slug: str, user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    f = await request.form()
    if not _csrf(request, str(f.get("csrf", ""))):
        return RedirectResponse(f"/clientes/{slug}/activos/nuevo", status_code=303)
    datos = {k: str(f.get(k, "")) for k in _CAMPOS}
    pid = str(f.get("persona_id", "")).strip()
    try:
        aid = svc.crear(conn, cli["id"], user["user"], datos, int(pid) if pid.isdigit() else None)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return render(request, "activo_form.html", cli=cli, a=None, v={**datos, "persona_id": pid}, personas=svc.personas(conn, cli["id"]), TIPOS=svc.TIPOS, status_code=400)
    flash(request, "Activo agregado.", "ok")
    return _vuelta(slug, aid)


@router.get("/clientes/{slug}/activos/exportar.csv")
def exportar(request: Request, slug: str, user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    return Response(svc.exportar_csv(conn, cli["id"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="activos-{cli["slug"]}.csv"'})


@router.get("/activos/plantilla.csv")
def plantilla(user: dict = Depends(require_permiso("activos"))):
    return Response(svc.plantilla_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="plantilla-activos.csv"'})


@router.post("/clientes/{slug}/activos/importar", response_class=HTMLResponse)
async def importar(request: Request, slug: str, archivo: UploadFile = File(...), csrf: str = Form(""),
                   user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if not _csrf(request, csrf):
        return _vuelta(slug)
    raw = await archivo.read(1_000_001)
    try:
        res = svc.importar_csv(conn, cli["id"], user["user"], raw)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return _vuelta(slug)
    return _lista(request, conn, cli, {"tipo": "", "estado": "", "persona": "", "q": ""}, importacion=res)


@router.post("/clientes/{slug}/activos/sincronizar", response_class=HTMLResponse)
def sincronizar(request: Request, slug: str, importar: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("activos")),
                conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if not _csrf(request, csrf):
        return _vuelta(slug)
    res = svc.sincronizar(conn, request.app.state.cfg, cli, user["user"], importar=bool(importar))
    if res["error"]:
        flash(request, f"No se pudo leer el inventario de GLPI: {res['error']}", "error")
        return _vuelta(slug)
    return _lista(request, conn, cli, {"tipo": "", "estado": "", "persona": "", "q": ""}, sincronizacion=res)


@router.get("/clientes/{slug}/activos/{aid}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, aid: int, user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    try:
        a = svc.obtener(conn, cli["id"], aid)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return _vuelta(slug)
    equipos, error = svc.equipos_glpi(request.app.state.cfg, cli)
    svc.vista_agente([a], equipos)
    return render(request, "activo_detalle.html", cli=cli, a=a, hist=svc.historial(conn, aid), personas=svc.personas(conn, cli["id"]), ESTADOS=svc.ESTADOS,
                  v=a, TIPOS=svc.TIPOS, libres_glpi=svc.pendientes_glpi(conn, cli["id"], equipos), glpi_error=error)


@router.post("/clientes/{slug}/activos/{aid}/editar", response_class=HTMLResponse)
async def editar(request: Request, slug: str, aid: int, user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    f = await request.form()
    if not _csrf(request, str(f.get("csrf", ""))):
        return _vuelta(slug, aid)
    try:
        svc.editar(conn, cli["id"], aid, user["user"], {k: str(f.get(k, "")) for k in _CAMPOS})
        flash(request, "Datos del activo guardados.", "ok")
    except ServiceError as e:
        flash(request, e.mensaje, "error")
    return _vuelta(slug, aid)


def _simple(request, slug, aid, csrf, conn, hacer, ok, user, volver_a_lista=False):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if not _csrf(request, csrf):
        return _vuelta(slug, aid)
    try:
        hacer(cli)
        flash(request, ok, "ok")
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return _vuelta(slug, aid)
    return _vuelta(slug) if volver_a_lista else _vuelta(slug, aid)


@router.post("/clientes/{slug}/activos/{aid}/asignar")
def asignar(request: Request, slug: str, aid: int, persona_id: str = Form(""), nota: str = Form(""), csrf: str = Form(""),
            user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    return _simple(request, slug, aid, csrf, conn, lambda cli: svc.asignar(conn, cli["id"], aid, user["user"], int(persona_id) if persona_id.isdigit() else None, nota),
                   "Activo asignado.", user)


@router.post("/clientes/{slug}/activos/{aid}/devolver")
def devolver(request: Request, slug: str, aid: int, nota: str = Form(""), csrf: str = Form(""),
             user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    return _simple(request, slug, aid, csrf, conn, lambda cli: svc.devolver(conn, cli["id"], aid, user["user"], nota), "Activo devuelto: queda en stock.", user)


@router.post("/clientes/{slug}/activos/{aid}/estado")
def estado(request: Request, slug: str, aid: int, estado: str = Form(""), nota: str = Form(""), csrf: str = Form(""),
           user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    return _simple(request, slug, aid, csrf, conn, lambda cli: svc.cambiar_estado(conn, cli["id"], aid, user["user"], estado, nota), "Estado actualizado.", user)


@router.post("/clientes/{slug}/activos/{aid}/vincular")
def vincular(request: Request, slug: str, aid: int, glpi_id: str = Form(""), csrf: str = Form(""),
             user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    def hacer(cli):
        equipos, _ = svc.equipos_glpi(request.app.state.cfg, cli)
        svc.vincular(conn, cli["id"], aid, user["user"], glpi_id, equipos)
    return _simple(request, slug, aid, csrf, conn, hacer, "Activo vinculado con el equipo de GLPI.", user)


@router.post("/clientes/{slug}/activos/{aid}/desvincular")
def desvincular(request: Request, slug: str, aid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("activos")),
                conn: sqlite3.Connection = Depends(get_db)):
    return _simple(request, slug, aid, csrf, conn, lambda cli: svc.desvincular(conn, cli["id"], aid, user["user"]), "Vínculo con GLPI quitado.", user)


@router.post("/clientes/{slug}/activos/{aid}/eliminar")
def eliminar(request: Request, slug: str, aid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("activos.eliminar")),
             conn: sqlite3.Connection = Depends(get_db)):
    return _simple(request, slug, aid, csrf, conn, lambda cli: svc.eliminar(conn, cli["id"], aid, user["user"]), "Activo eliminado.", user, volver_a_lista=True)


# ---------------------------------------------------------------- personas

@router.get("/clientes/{slug}/personas", response_class=HTMLResponse)
def personas(request: Request, slug: str, user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    return render(request, "personas.html", cli=cli, personas=svc.personas(conn, cli["id"], solo_activas=False), fuentes=svc.fuentes_conectadas(cli))


@router.post("/clientes/{slug}/personas/sincronizar", response_class=HTMLResponse)
def personas_sincronizar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("activos")),
                         conn: sqlite3.Connection = Depends(get_db)):
    """Trae como personas a los usuarios con licencia activa de Microsoft 365 / Google Workspace del cliente."""
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}/personas", status_code=303)
    if not svc.fuentes_conectadas(cli):
        flash(request, "Este cliente no tiene Microsoft 365 ni Google Workspace conectados (ficha del cliente).", "error")
        return RedirectResponse(f"/clientes/{slug}/personas", status_code=303)
    res = svc.sincronizar_usuarios(conn, request.app.state.cfg, cli, user["user"])
    return render(request, "personas.html", cli=cli, personas=svc.personas(conn, cli["id"], solo_activas=False), fuentes=svc.fuentes_conectadas(cli), sync=res)


@router.post("/clientes/{slug}/personas")
def persona_nueva(request: Request, slug: str, nombre: str = Form(""), email: str = Form(""), cargo: str = Form(""), area: str = Form(""), csrf: str = Form(""),
                  volver: str = Form(""), user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if _csrf(request, csrf):
        try:
            svc.crear_persona(conn, cli["id"], user["user"], nombre, email, cargo, area)
            flash(request, "Persona agregada.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse(volver if volver.startswith(f"/clientes/{slug}/") else f"/clientes/{slug}/personas", status_code=303)


@router.post("/clientes/{slug}/personas/{pid}/editar")
def persona_editar(request: Request, slug: str, pid: int, nombre: str = Form(""), cargo: str = Form(""), area: str = Form(""), activa: str = Form(""),
                   csrf: str = Form(""), user: dict = Depends(require_permiso("activos")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(request, conn, slug)
    if not cli:
        return _fuera()
    if _csrf(request, csrf):
        try:
            svc.editar_persona(conn, cli["id"], pid, user["user"], nombre, cargo, area, bool(activa))
            flash(request, "Persona actualizada.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse(f"/clientes/{slug}/personas", status_code=303)
