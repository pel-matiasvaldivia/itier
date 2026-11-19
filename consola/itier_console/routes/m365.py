"""Microsoft 365 por cliente: conectar el tenant y hacer ABM de sus usuarios (Microsoft Graph)."""

from __future__ import annotations

import secrets
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..adapters import AdapterError, m365 as m365_mod
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, ajustes, onboarding
from ..services import m365 as svc
from ..services.usuarios import generar_clave

router = APIRouter()


def _cfg(request, conn):
    return ajustes.efectiva(conn, request.app.state.cfg)


def _cli(conn, slug):
    return onboarding.obtener_cliente_por_slug(conn, slug)


def _fuera(request, msg="Cliente no encontrado.") -> RedirectResponse:
    flash(request, msg, "error")
    return RedirectResponse("/clientes", status_code=303)


def _csrf(request, csrf) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


def _redirect_uri(cfg, request) -> str:
    base = cfg.console_public_url or str(request.base_url).rstrip("/")
    return f"{base}/m365/consentimiento"


# ---------------------------------------------------------------- aplicación del MSP (admin)

@router.get("/m365/aplicacion", response_class=HTMLResponse)
def app_form(request: Request, user: dict = Depends(require_permiso("m365.app")), conn: sqlite3.Connection = Depends(get_db)):
    cfg = _cfg(request, conn)
    return render(request, "m365_app.html", cfg_ef=cfg, redirect_uri=_redirect_uri(cfg, request))


@router.post("/m365/aplicacion")
def app_guardar(request: Request, client_id: str = Form(""), secreto: str = Form(""), csrf: str = Form(""),
                user: dict = Depends(require_permiso("m365.app")), conn: sqlite3.Connection = Depends(get_db)):
    if not _csrf(request, csrf):
        return RedirectResponse("/m365/aplicacion", status_code=303)
    cid = client_id.strip()
    if cid and len(cid) != 36:
        flash(request, "El ID de aplicación (client id) es un GUID de 36 caracteres.", "error")
        return RedirectResponse("/m365/aplicacion", status_code=303)
    vals = {"m365_client_id": cid}
    if secreto.strip():
        vals["m365_client_secret"] = secreto.strip()
    ajustes.guardar(conn, request.app.state.cfg, user["user"], vals)
    audit.registrar(conn, user["user"], "m365.aplicacion_configurada", {"client_id": cid, "secreto_cambiado": bool(secreto.strip())})
    flash(request, "Aplicación de Microsoft 365 guardada.", "ok")
    return RedirectResponse("/m365/aplicacion", status_code=303)


# ---------------------------------------------------------------- conexión del tenant de un cliente

@router.post("/clientes/{slug}/m365/conectar")
def conectar(request: Request, slug: str, tenant: str = Form(""), csrf: str = Form(""),
             user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    cfg = _cfg(request, conn)
    try:
        hint = svc.tenant_valido(tenant)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    if cfg.demo:                                        # sin Microsoft real: se conecta al tenant de ejemplo
        svc.verificar_conexion(conn, cfg, cli, "demo-tenant")
        audit.registrar(conn, user["user"], "m365.conectado", {"cliente": cli["nombre"], "tenant": "demo-tenant"})
        flash(request, "Tenant de ejemplo conectado (modo demo).", "ok")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    if not cfg.m365_configurado:
        flash(request, "Primero un administrador tiene que configurar la aplicación de Microsoft 365 (client id y secreto).", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    state = secrets.token_urlsafe(24)
    request.session["m365_state"] = {"state": state, "slug": slug}
    return RedirectResponse(m365_mod.url_consentimiento(cfg, _redirect_uri(cfg, request), state, hint or None), status_code=303)


@router.get("/m365/consentimiento")
def consentimiento(request: Request, state: str = "", tenant: str = "", admin_consent: str = "", error: str = "",
                   error_description: str = "", user: dict = Depends(require_permiso("clientes.editar")),
                   conn: sqlite3.Connection = Depends(get_db)):
    """Vuelta de Microsoft después de que el administrador del cliente aceptó (o rechazó) el consentimiento."""
    guardado = request.session.pop("m365_state", None) or {}
    if not guardado or not state or not secrets.compare_digest(str(guardado.get("state", "")), state):
        flash(request, "La autorización de Microsoft no coincide con una solicitud iniciada desde la consola. Empezá de nuevo.", "error")
        return RedirectResponse("/clientes", status_code=303)
    slug = guardado.get("slug", "")
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if error or admin_consent.lower() != "true":
        flash(request, f"El administrador del cliente no completó la autorización: {(error_description or error or 'cancelado')[:200]}", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        svc.tenant_valido(tenant)
        org = svc.verificar_conexion(conn, _cfg(request, conn), cli, tenant.lower())
    except (ServiceError, AdapterError) as e:
        flash(request, "Se recibió la autorización pero todavía no se pudo leer el tenant (Microsoft puede tardar unos minutos en "
                       f"propagarla): {getattr(e, 'mensaje', None) or getattr(e, 'detalle', e)}. Probá «Verificar conexión» en un rato.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    audit.registrar(conn, user["user"], "m365.conectado", {"cliente": cli["nombre"], "tenant": tenant, "organizacion": org.get("nombre")})
    flash(request, f"Tenant «{org.get('nombre') or tenant}» conectado.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/m365/verificar")
def verificar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
              conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if _csrf(request, csrf):
        try:
            org = svc.verificar_conexion(conn, _cfg(request, conn), cli)
            flash(request, f"Conexión correcta con «{org.get('nombre')}».", "ok")
        except (ServiceError, AdapterError) as e:
            flash(request, f"No se pudo verificar: {getattr(e, 'mensaje', None) or getattr(e, 'detalle', e)}", "error")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/m365/desconectar")
def desconectar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
                conn: sqlite3.Connection = Depends(get_db)):
    cli = _cli(conn, slug)
    if not cli:
        return _fuera(request)
    if _csrf(request, csrf):
        svc.marcar(conn, cli["id"], m365_tenant_id=None, m365_estado=None, m365_detalle=None, m365_dominio=None,
                   m365_nombre=None, m365_conectado_at=None)
        audit.registrar(conn, user["user"], "m365.desconectado", {"cliente": cli["nombre"]})
        flash(request, "Tenant desconectado. Para revocar el acceso del todo, el cliente también puede quitar la aplicación en su Entra ID.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


# ---------------------------------------------------------------- ABM de usuarios del tenant

def _contexto(request, conn, slug):
    """(cliente, cfg, adaptador) o (None, ...) si no hay conexión; deja un flash explicando por qué."""
    cli = _cli(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return None, None, None
    if not svc.conectado(cli):
        flash(request, "Este cliente todavía no tiene el tenant de Microsoft 365 conectado.", "error")
        return None, None, None
    cfg = _cfg(request, conn)
    return cli, cfg, m365_mod.cliente(cfg, cli["m365_tenant_id"])


@router.get("/clientes/{slug}/m365", response_class=HTMLResponse)
def usuarios(request: Request, slug: str, q: str = "", estado: str = "", user: dict = Depends(require_permiso("m365")),
             conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    error, lista, planes = None, [], []
    try:
        lista, planes = g.listar_usuarios(), g.planes()
    except AdapterError as e:
        error = e.detalle
    por_sku = {p["sku_id"]: p for p in planes}
    for u in lista:
        u["planes"] = [por_sku[s]["nombre"] if s in por_sku else "Otro plan" for s in u["licencias"]]
    resumen = {"total": len(lista), "habilitados": sum(1 for u in lista if u["habilitado"]),
               "bloqueados": sum(1 for u in lista if not u["habilitado"]), "sin_licencia": sum(1 for u in lista if not u["licencias"])}
    ql = q.strip().lower()
    vista = [u for u in lista if (not ql or ql in (u["nombre"] + " " + u["upn"] + " " + u["cargo"]).lower())
             and (estado != "bloqueados" or not u["habilitado"]) and (estado != "sin_licencia" or not u["licencias"])]
    return render(request, "m365_usuarios.html", cli=cli, usuarios=vista, planes=planes, resumen=resumen, error=error, q=q, estado=estado)


@router.get("/clientes/{slug}/m365/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, slug: str, user: dict = Depends(require_permiso("m365")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    try:
        org, planes = g.organizacion(), g.planes()
    except AdapterError as e:
        flash(request, f"No se pudo leer el tenant: {e.detalle}", "error")
        return RedirectResponse(f"/clientes/{slug}/m365", status_code=303)
    return render(request, "m365_nuevo.html", cli=cli, org=org, planes=planes, smtp=bool(cfg.smtp_host and cfg.smtp_from), v={})


@router.post("/clientes/{slug}/m365/nuevo", response_class=HTMLResponse)
def nuevo_post(request: Request, slug: str, nombre: str = Form(""), apellido: str = Form(""), usuario: str = Form(""),
               dominio: str = Form(""), cargo: str = Form(""), departamento: str = Form(""), licencias: list[str] = Form([]),
               enviar_a: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("m365")),
               conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}/m365/nuevo", status_code=303)
    usuario = usuario.strip() or svc.sugerir_usuario(nombre, apellido)
    try:
        org, planes = g.organizacion(), g.planes()
        validos = {p["sku_id"] for p in planes}
        r = svc.crear(cfg, cli, org, nombre=nombre, apellido=apellido, usuario=usuario, dominio=dominio, cargo=cargo,
                      departamento=departamento, sku_ids=[s for s in licencias if s in validos], enviar_a=enviar_a)
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        try:
            org, planes = g.organizacion(), g.planes()
        except AdapterError:
            return RedirectResponse(f"/clientes/{slug}/m365", status_code=303)
        return render(request, "m365_nuevo.html", cli=cli, org=org, planes=planes, smtp=bool(cfg.smtp_host and cfg.smtp_from),
                      v={"nombre": nombre, "apellido": apellido, "usuario": usuario, "dominio": dominio, "cargo": cargo,
                         "departamento": departamento, "licencias": licencias, "enviar_a": enviar_a}, status_code=400)
    audit.registrar(conn, user["user"], "m365.usuario_creado", {"cliente": cli["nombre"], "upn": r["upn"], "licencias": len(licencias)})
    nueva = {"motivo": "alta", **r}
    return _detalle(request, conn, cli, cfg, g, r["id"], nueva=nueva)


def _detalle(request, conn, cli, cfg, g, uid, nueva=None, status_code=200):
    try:
        u, planes = g.ver_usuario(uid), g.planes()
    except AdapterError as e:
        flash(request, f"No se pudo leer el usuario: {e.detalle}", "error")
        return RedirectResponse(f"/clientes/{cli['slug']}/m365", status_code=303)
    return render(request, "m365_usuario.html", cli=cli, u=u, planes=planes, nueva=nueva, smtp=bool(cfg.smtp_host and cfg.smtp_from),
                  status_code=status_code)


@router.get("/clientes/{slug}/m365/{uid}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, uid: str, user: dict = Depends(require_permiso("m365")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    try:
        svc.uid_valido(uid)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/m365", status_code=303)
    return _detalle(request, conn, cli, cfg, g, uid)


def _accion(request, slug, uid, csrf, conn, permiso_user, hacer, auditoria, ok_msg):
    """Envoltorio común de las acciones sobre un usuario: contexto, CSRF, errores de Graph, auditoría y vuelta al detalle."""
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    volver = RedirectResponse(f"/clientes/{slug}/m365/{uid}", status_code=303)
    if not _csrf(request, csrf):
        return volver
    try:
        svc.uid_valido(uid)
        res = hacer(cli, cfg, g)
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        return volver
    audit.registrar(conn, permiso_user["user"], auditoria[0], {"cliente": cli["nombre"], "usuario_id": uid, **auditoria[1]})
    flash(request, ok_msg, "ok")
    return res if isinstance(res, RedirectResponse) else volver


@router.post("/clientes/{slug}/m365/{uid}/editar")
def editar(request: Request, slug: str, uid: str, nombre: str = Form(""), apellido: str = Form(""), cargo: str = Form(""),
           departamento: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("m365")),
           conn: sqlite3.Connection = Depends(get_db)):
    if not nombre.strip() or not apellido.strip():
        flash(request, "Nombre y apellido son obligatorios.", "error")
        return RedirectResponse(f"/clientes/{slug}/m365/{uid}", status_code=303)
    return _accion(request, slug, uid, csrf, conn, user,
                   lambda cli, cfg, g: g.actualizar_usuario(uid, {"nombre": nombre.strip(), "apellido": apellido.strip(),
                                                                  "cargo": cargo.strip(), "departamento": departamento.strip()}),
                   ("m365.usuario_editado", {}), "Datos actualizados.")


@router.post("/clientes/{slug}/m365/{uid}/licencias")
def licencias(request: Request, slug: str, uid: str, licencias: list[str] = Form([]), csrf: str = Form(""),
              user: dict = Depends(require_permiso("m365")), conn: sqlite3.Connection = Depends(get_db)):
    def hacer(cli, cfg, g):
        planes = {p["sku_id"] for p in g.planes()}
        actuales = set(g.ver_usuario(uid)["licencias"])
        deseadas = {s for s in licencias if s in planes}
        agregar, quitar = sorted(deseadas - actuales), sorted((actuales & planes) - deseadas)
        g.asignar_licencias(uid, agregar, quitar)
    return _accion(request, slug, uid, csrf, conn, user, hacer, ("m365.licencias", {}), "Licencias actualizadas.")


@router.post("/clientes/{slug}/m365/{uid}/clave", response_class=HTMLResponse)
def clave(request: Request, slug: str, uid: str, enviar_a: str = Form(""), csrf: str = Form(""),
          user: dict = Depends(require_permiso("m365")), conn: sqlite3.Connection = Depends(get_db)):
    cli, cfg, g = _contexto(request, conn, slug)
    if not g:
        return RedirectResponse(f"/clientes/{slug}" if _cli(conn, slug) else "/clientes", status_code=303)
    if not _csrf(request, csrf):
        return RedirectResponse(f"/clientes/{slug}/m365/{uid}", status_code=303)
    try:
        svc.uid_valido(uid)
        u = g.ver_usuario(uid)
        nueva_clave = generar_clave()
        g.reiniciar_clave(uid, nueva_clave)
        estado = "no solicitado"
        if enviar_a.strip():
            from ..services import correo
            try:
                correo.enviar(cfg, correo.validar_email(enviar_a), f"Nueva clave de Microsoft 365 — {cli['nombre']}",
                              f"Hola {u['nombre_pila'] or u['nombre']},\n\nTu clave temporal de Microsoft 365 es: {nueva_clave}\n"
                              f"Usuario: {u['upn']}\nIngresá en https://www.office.com; te va a pedir cambiarla.\n")
                estado = f"ok (enviado a {enviar_a.strip()})"
            except Exception as e:  # noqa: BLE001
                estado = f"error: {getattr(e, 'mensaje', None) or e}"
    except (ServiceError, AdapterError) as e:
        flash(request, getattr(e, "mensaje", None) or getattr(e, "detalle", str(e)), "error")
        return RedirectResponse(f"/clientes/{slug}/m365/{uid}", status_code=303)
    audit.registrar(conn, user["user"], "m365.clave_reiniciada", {"cliente": cli["nombre"], "upn": u["upn"]})
    return _detalle(request, conn, cli, cfg, g, uid, nueva={"motivo": "clave", "usuario": u["upn"], "upn": u["upn"], "clave": nueva_clave, "correo": estado})


@router.post("/clientes/{slug}/m365/{uid}/estado")
def estado(request: Request, slug: str, uid: str, habilitar: str = Form(""), csrf: str = Form(""),
           user: dict = Depends(require_permiso("m365")), conn: sqlite3.Connection = Depends(get_db)):
    hab = habilitar == "1"
    return _accion(request, slug, uid, csrf, conn, user, lambda cli, cfg, g: g.habilitar(uid, hab),
                   ("m365.usuario_habilitado" if hab else "m365.usuario_bloqueado", {}),
                   "Usuario habilitado." if hab else "Usuario bloqueado: ya no puede iniciar sesión.")


@router.post("/clientes/{slug}/m365/{uid}/eliminar")
def eliminar(request: Request, slug: str, uid: str, csrf: str = Form(""), user: dict = Depends(require_permiso("m365.eliminar")),
             conn: sqlite3.Connection = Depends(get_db)):
    return _accion(request, slug, uid, csrf, conn, user,
                   lambda cli, cfg, g: (g.eliminar(uid), RedirectResponse(f"/clientes/{slug}/m365", status_code=303))[1],
                   ("m365.usuario_eliminado", {}),
                   "Usuario eliminado. Microsoft lo conserva 30 días en la papelera de usuarios eliminados (se puede restaurar desde el Centro de administración).")
