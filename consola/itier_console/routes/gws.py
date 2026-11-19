"""Google Workspace por cliente: conectar el dominio y hacer ABM de sus usuarios (Directory API)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError, gws as gws_mod
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, ajustes, onboarding
from ..services import gws as svc
from ..services import correo
from ..services.usuarios import generar_clave

router = APIRouter()


def _cfg(request, conn):
    return ajustes.efectiva(conn, request.app.state.cfg)


def _cli(conn, slug):
    return onboarding.obtener_cliente_por_slug(conn, slug)


def _csrf(request, csrf) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


def _fuera(request) -> RedirectResponse:
    flash(request, "Cliente no encontrado.", "error")
    return RedirectResponse("/clientes", status_code=303)


def _sa_info(cfg) -> dict | None:
    """Datos públicos de la cuenta de servicio guardada (nunca la clave privada)."""
    if not cfg.gws_sa_json:
        return None
    try:
        sa = gws_mod.parsear_cuenta_servicio(cfg.gws_sa_json)
        return {"email": sa["client_email"], "client_id": sa["client_id"]}
    except ValueError:
        return None


# ---------------------------------------------------------------- cuenta de servicio del MSP (admin)

@router.get("/gws/aplicacion", response_class=HTMLResponse)
def app_form(request: Request, user: dict = Depends(require_permiso("gws.app")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = _cfg(request, conn)
    return render(request, "gws_app.html", sa=_sa_info(cfg), guardada=bool(cfg.gws_sa_json), scopes=gws_mod.SCOPES)


@router.post("/gws/aplicacion")
def app_guardar(request: Request, clave_json: str = Form(""), csrf: str = Form(""),
                user: dict = Depends(require_permiso("gws.app")), conn: sqlite3.Connection = Depends(get_db)):
    if not _csrf(request, csrf):
        return RedirectResponse("/gws/aplicacion", status_code=303)
    try:
        sa = gws_mod.parsear_cuenta_servicio(clave_json.strip())
    except ValueError as e:
        flash(request, f"La clave no se guardó: {e}", "error")
        return RedirectResponse("/gws/aplicacion", status_code=303)
    ajustes.guardar(conn, request.app.state.cfg, user["user"], {"gws_sa_json": clave_json.strip()})
    audit.registrar(conn, user["user"], "gws.cuenta_servicio_configurada", {"cuenta": sa["client_email"], "client_id": sa["client_id"]})
    flash(request, f"Cuenta de servicio guardada ({sa['client_email']}).", "ok")
    return RedirectResponse("/gws/aplicacion", status_code=303)


@router.post("/gws/aplicacion/borrar")
def app_borrar(request: Request, csrf: str = Form(""), user: dict = Depends(require_permiso("gws.app")),
               conn: sqlite3.Connection = Depends(get_db)):
    if _csrf(request, csrf):
        ajustes.guardar(conn, request.app.state.cfg, user["user"], {"gws_sa_json": ""})
        audit.registrar(conn, user["user"], "gws.cuenta_servicio_borrada", {})
        flash(request, "Cuenta de servicio borrada: la gestión de Google Workspace queda apagada.", "ok")
    return RedirectResponse("/gws/aplicacion", status_code=303)


# ---------------------------------------------------------------- conexión del dominio de un cliente

@router.post("/clientes/{slug}/gws/conectar")
def conectar(request: Request, slug: str, admin: str = Form(""), csrf: str = Form(""),
             user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    cfg = _cfg(request, conn)
    if not cfg.demo and not cfg.gws_configurado:
        flash(request, "Primero un administrador tiene que cargar la cuenta de servicio de Google.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        d = svc.verificar_conexion(conn, cfg, cli, admin)
    except (ServiceError, AdapterError) as e:
        flash(request, f"No se pudo conectar: {getattr(e, 'mensaje', None) or getattr(e, 'detalle', e)}", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    audit.registrar(conn, user["user"], "gws.conectado", {"cliente": cli["nombre"], "admin": admin.strip().lower(), "dominio": d["principal"]})
    flash(request, f"Dominio «{d['principal']}» conectado.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/gws/verificar")
def verificar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
              conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if _csrf(request, csrf):
        try:
            d = svc.verificar_conexion(conn, _cfg(request, conn), cli)
            flash(request, f"Conexión correcta con «{d['principal']}».", "ok")
        except (ServiceError, AdapterError) as e:
            flash(request, f"No se pudo verificar: {getattr(e, 'mensaje', None) or getattr(e, 'detalle', e)}", "error")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/gws/desconectar")
def desconectar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
                conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if _csrf(request, csrf):
        svc.marcar(conn, cli["id"], gws_admin=None, gws_estado=None, gws_detalle=None, gws_dominio=None, gws_conectado_at=None)
        audit.registrar(conn, user["user"], "gws.desconectado", {"cliente": cli["nombre"]})
        flash(request, "Dominio desconectado. Para revocar el acceso del todo, el cliente puede quitar la autorización en Delegación en todo el dominio.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


# ---------------------------------------------------------------- ABM de usuarios del dominio

def _contexto(request, conn, slug):
    cli = _cli(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return None, None, None
    if not svc.conectado(cli):
        flash(request, "Este cliente todavía no tiene el dominio de Google Workspace conectado.", "error")
        return None, None, None
    cfg = _cfg(request, conn)
    return cli, cfg, gws_mod.cliente(cfg, cli["gws_admin"])


def _atras(conn, slug) -> RedirectResponse:
    return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)


@router.get("/clientes/{slug}/gws", response_class=HTMLResponse)
def usuarios(request: Request, slug: str, q: str = "", estado: str = "", user: dict = Depends(require_permiso("gws")),
             conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    error, lista = None, []
    try:
        lista = g.listar_usuarios()
    except AdapterError as e:
        error = e.detalle
    resumen = {"total": len(lista), "activos": sum(1 for u in lista if not u["suspendido"]),
               "suspendidos": sum(1 for u in lista if u["suspendido"]), "admins": sum(1 for u in lista if u["admin"])}
    ql = q.strip().lower()
    vista = [u for u in lista if (not ql or ql in (u["nombre"] + " " + u["email"] + " " + u["cargo"]).lower())
             and (estado != "suspendidos" or u["suspendido"]) and (estado != "admins" or u["admin"])]
    return render(request, "gws_usuarios.html", cli=cli, usuarios=vista, resumen=resumen, error=error, q=q, estado=estado)


@router.get("/clientes/{slug}/gws/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, slug: str, user: dict = Depends(require_permiso("gws")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    try:
        doms = g.dominios()
    except AdapterError as e:
        flash(request, f"No se pudo leer el dominio: {e.detalle}", "error")
        return RedirectResponse(f"/clientes/{slug}/gws", status_code=303)
    return render(request, "gws_nuevo.html", cli=cli, doms=doms, smtp=correo.configurado(cfg), v={})


@router.post("/clientes/{slug}/gws/nuevo", response_class=HTMLResponse)
def nuevo_post(request: Request, slug: str, nombre: str = Form(""), apellido: str = Form(""), usuario: str = Form(""),
               dominio: str = Form(""), cargo: str = Form(""), departamento: str = Form(""), enviar_a: str = Form(""),
               csrf: str = Form(""), user: dict = Depends(require_permiso("gws")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}/gws/nuevo", status_code=303)
    usuario = usuario.strip() or svc.sugerir_usuario(nombre, apellido)
    try:
        doms = g.dominios()
        r = svc.crear(cfg, cli, doms, nombre=nombre, apellido=apellido, usuario=usuario, dominio=dominio, cargo=cargo,
                      departamento=departamento, enviar_a=enviar_a)
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        try:
            doms = g.dominios()
        except AdapterError:
            return RedirectResponse(f"/clientes/{slug}/gws", status_code=303)
        return render(request, "gws_nuevo.html", cli=cli, doms=doms, smtp=correo.configurado(cfg), status_code=400,
                      v={"nombre": nombre, "apellido": apellido, "usuario": usuario, "dominio": dominio, "cargo": cargo,
                         "departamento": departamento, "enviar_a": enviar_a})
    audit.registrar(conn, user["user"], "gws.usuario_creado", {"cliente": cli["nombre"], "email": r["email"]})
    return _detalle(request, cli, cfg, g, r["id"], nueva={"motivo": "alta", **r})


def _detalle(request, cli, cfg, g, uid, nueva=None):
    try:
        u = g.ver_usuario(uid)
    except AdapterError as e:
        flash(request, f"No se pudo leer el usuario: {e.detalle}", "error")
        return RedirectResponse(f"/clientes/{cli['slug']}/gws", status_code=303)
    return render(request, "gws_usuario.html", cli=cli, u=u, nueva=nueva)


@router.get("/clientes/{slug}/gws/{uid}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, uid: str, user: dict = Depends(require_permiso("gws")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    try:
        svc.uid_valido(uid)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/gws", status_code=303)
    return _detalle(request, cli, cfg, g, uid)


def _accion(request, slug, uid, csrf, conn, user, hacer, auditoria, ok_msg, proteger=None):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    volver = RedirectResponse(f"/clientes/{slug}/gws/{uid}", status_code=303)
    if not _csrf(request, csrf):
        return volver
    try:
        svc.uid_valido(uid)
        if proteger:
            svc.proteger_admin(g.ver_usuario(uid), proteger)
        res = hacer(cli, cfg, g)
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        return volver
    audit.registrar(conn, user["user"], auditoria, {"cliente": cli["nombre"], "usuario_id": uid})
    flash(request, ok_msg, "ok")
    return res if isinstance(res, RedirectResponse) else volver


@router.post("/clientes/{slug}/gws/{uid}/editar")
def editar(request: Request, slug: str, uid: str, nombre: str = Form(""), apellido: str = Form(""), cargo: str = Form(""),
           departamento: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("gws")),
           conn: sqlite3.Connection = Depends(get_db)):
    if not nombre.strip() or not apellido.strip():
        flash(request, "Nombre y apellido son obligatorios.", "error")
        return RedirectResponse(f"/clientes/{slug}/gws/{uid}", status_code=303)
    return _accion(request, slug, uid, csrf, conn, user,
                   lambda cli, cfg, g: g.actualizar_usuario(uid, {"nombre": nombre.strip(), "apellido": apellido.strip(),
                                                                  "cargo": cargo.strip(), "departamento": departamento.strip()}),
                   "gws.usuario_editado", "Datos actualizados.")


@router.post("/clientes/{slug}/gws/{uid}/clave", response_class=HTMLResponse)
def clave(request: Request, slug: str, uid: str, enviar_a: str = Form(""), csrf: str = Form(""),
          user: dict = Depends(require_permiso("gws")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return _atras(conn, slug)
    volver = RedirectResponse(f"/clientes/{slug}/gws/{uid}", status_code=303)
    if not _csrf(request, csrf):
        return volver
    try:
        svc.uid_valido(uid)
        u = g.ver_usuario(uid)
        svc.proteger_admin(u, "reiniciá la clave")
        nueva_clave = generar_clave()
        g.reiniciar_clave(uid, nueva_clave)
        estado = "no solicitado"
        if enviar_a.strip():
            try:
                correo.enviar(cfg, correo.validar_email(enviar_a), f"Nueva clave de Google Workspace — {cli['nombre']}",
                              f"Hola {u['nombre_pila'] or u['nombre']},\n\nTu clave temporal de Google Workspace es: {nueva_clave}\n"
                              f"Usuario: {u['email']}\nIngresá en https://accounts.google.com; te va a pedir cambiarla.\n")
                estado = f"ok (enviado a {enviar_a.strip()})"
            except Exception as e:  # noqa: BLE001
                estado = f"error: {getattr(e, 'mensaje', None) or e}"
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        return volver
    audit.registrar(conn, user["user"], "gws.clave_reiniciada", {"cliente": cli["nombre"], "email": u["email"]})
    return _detalle(request, cli, cfg, g, uid, nueva={"motivo": "clave", "usuario": u["email"], "email": u["email"], "clave": nueva_clave, "correo": estado})


@router.post("/clientes/{slug}/gws/{uid}/estado")
def estado(request: Request, slug: str, uid: str, suspender: str = Form(""), csrf: str = Form(""),
           user: dict = Depends(require_permiso("gws")), conn: sqlite3.Connection = Depends(get_db)):
    susp = suspender == "1"
    return _accion(request, slug, uid, csrf, conn, user, lambda cli, cfg, g: g.suspender(uid, susp),
                   "gws.usuario_suspendido" if susp else "gws.usuario_reactivado",
                   "Usuario suspendido: ya no puede iniciar sesión." if susp else "Usuario reactivado.",
                   proteger="suspendelo" if susp else None)


@router.post("/clientes/{slug}/gws/{uid}/eliminar")
def eliminar(request: Request, slug: str, uid: str, csrf: str = Form(""), user: dict = Depends(require_permiso("gws.eliminar")),
             conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, uid, csrf, conn, user,
                   lambda cli, cfg, g: (g.eliminar(uid), RedirectResponse(f"/clientes/{slug}/gws", status_code=303))[1],
                   "gws.usuario_eliminado",
                   "Usuario eliminado. Google permite restaurarlo durante unos 20 días desde la Admin console (Usuarios → Eliminados recientemente).",
                   proteger="eliminalo")
