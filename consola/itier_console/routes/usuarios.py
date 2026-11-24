"""ABM de usuarios de la plataforma (técnicos, PM, administradores). Solo el rol Administrador.

  GET  /usuarios                    lista + alta + estado de sincronización con GLPI / Zabbix / Mesh
  POST /usuarios                    alta (genera la clave; se muestra UNA vez)
  POST /usuarios/{id}/rol           cambiar rol
  POST /usuarios/{id}/clave         reiniciar clave (nueva clave en los tres sistemas)
  POST /usuarios/{id}/baja|alta     baja / alta lógica
  POST /usuarios/{id}/eliminar      eliminar (consola + GLPI + Zabbix + Mesh)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, correo, rbac, usuarios as svc

router = APIRouter()


def _csrf(request: Request, csrf: str) -> bool:
    if security.verificar_csrf(request.session, csrf):
        return True
    flash(request, "Sesión expirada, reintentá.", "error")
    return False


def _resumen(estados: dict[str, str]) -> str:
    malos = [f"{k}: {v}" for k, v in estados.items() if v.startswith("error")]
    return " · ".join(malos)


@router.get("/usuarios", response_class=HTMLResponse)
def lista(request: Request, user: dict = Depends(require_permiso("usuarios")), conn: sqlite3.Connection = Depends(get_db)):
    nueva = request.session.pop("_clave_usuario", None)   # se muestra UNA sola vez
    lista_u = svc.listar(conn)
    resumen = {"total": len(lista_u), "activos": sum(1 for u in lista_u if not u["disabled"]),
               "baja": sum(1 for u in lista_u if u["disabled"]),
               "errores": sum(1 for u in lista_u if any((u.get(k) or "").startswith("error")
                                                         for k in ("sync_glpi", "sync_zbx", "sync_mesh")))}
    from ..services import onboarding
    clientes = [{"slug": c["slug"], "nombre": c["nombre"], "id": c["id"]} for c in onboarding.listar_clientes(conn)]
    por_id = {c["id"]: c["nombre"] for c in clientes}
    for u in lista_u:
        u["empresa"] = por_id.get(u.get("scope_client_id")) if rbac.es_de_un_cliente(u["rol"]) else None
    return render(request, "usuarios.html", clientes=clientes, usuarios=lista_u, resumen=resumen, roles={k: v for k, v in rbac.ROLES.items() if k not in rbac.DE_UN_CLIENTE}, nueva=nueva, yo=user["uid"],
                  cfg_glpi=request.app.state.cfg.glpi_configurado, cfg_zbx=request.app.state.cfg.zbx_configurado,
                  cfg_mesh=request.app.state.cfg.mesh_enabled, smtp=correo.configurado(request.app.state.cfg))


@router.post("/usuarios")
def alta(request: Request, username: str = Form(""), email: str = Form(""), nombre: str = Form(""),
         rol: str = Form("tecnico"), cliente: str = Form(""), enviar: str = Form(""), csrf: str = Form(""), user: dict = Depends(require_permiso("usuarios")),
         conn: sqlite3.Connection = Depends(get_db)):
    if not _csrf(request, csrf):
        return RedirectResponse("/usuarios", status_code=303)
    try:
        from ..services import onboarding
        cli = onboarding.obtener_cliente_por_slug(conn, cliente) if cliente else None
        _uid, clave, estados = svc.crear(conn, request.app.state.cfg, user["user"], username, email, nombre, rol, enviar=bool(enviar),
                                         cliente_id=cli["id"] if cli else None)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse("/usuarios", status_code=303)
    request.session["_clave_usuario"] = {"usuario": username.strip().lower(), "clave": clave, "estados": estados,
                                         "motivo": "alta"}
    return RedirectResponse("/usuarios", status_code=303)


@router.post("/usuarios/{uid}/rol")
def rol(request: Request, uid: int, rol: str = Form(""), csrf: str = Form(""),
        user: dict = Depends(require_permiso("usuarios")), conn: sqlite3.Connection = Depends(get_db)):
    if _csrf(request, csrf):
        try:
            malos = _resumen(svc.cambiar_rol(conn, request.app.state.cfg, user["user"], uid, rol))
            flash(request, "Rol actualizado." + (f" Con problemas en: {malos}" if malos else ""), "error" if malos else "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse("/usuarios", status_code=303)


@router.post("/usuarios/{uid}/clave")
def clave(request: Request, uid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("usuarios")),
          conn: sqlite3.Connection = Depends(get_db)):
    if _csrf(request, csrf):
        try:
            nueva, estados = svc.reiniciar_clave(conn, request.app.state.cfg, user["user"], uid)
            u = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            request.session["_clave_usuario"] = {"usuario": u["username"], "clave": nueva, "estados": estados,
                                                 "motivo": "reinicio"}
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse("/usuarios", status_code=303)


@router.post("/usuarios/{uid}/baja")
def baja(request: Request, uid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("usuarios")),
         conn: sqlite3.Connection = Depends(get_db)):
    return _activar(request, uid, csrf, user, conn, False)


@router.post("/usuarios/{uid}/alta")
def reactivar(request: Request, uid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("usuarios")),
              conn: sqlite3.Connection = Depends(get_db)):
    return _activar(request, uid, csrf, user, conn, True)


def _activar(request, uid, csrf, user, conn, activo):
    if _csrf(request, csrf):
        if uid == user["uid"] and not activo:
            flash(request, "No podés dar de baja tu propio usuario.", "error")
        else:
            try:
                malos = _resumen(svc.activar(conn, request.app.state.cfg, user["user"], uid, activo))
                flash(request, ("Usuario activado." if activo else "Usuario dado de baja en todos los sistemas.")
                      + (f" Con problemas en: {malos}" if malos else ""), "error" if malos else "ok")
            except ServiceError as e:
                flash(request, e.mensaje, "error")
    return RedirectResponse("/usuarios", status_code=303)


@router.post("/usuarios/{uid}/eliminar")
def eliminar(request: Request, uid: int, csrf: str = Form(""), user: dict = Depends(require_permiso("usuarios")),
             conn: sqlite3.Connection = Depends(get_db)):
    if _csrf(request, csrf):
        try:
            malos = _resumen(svc.eliminar(conn, request.app.state.cfg, user["user"], user["uid"], uid))
            flash(request, "Usuario eliminado." + (f" Con problemas en: {malos}" if malos else ""), "error" if malos else "ok")
        except ServiceError as e:
            flash(request, e.mensaje, "error")
    return RedirectResponse("/usuarios", status_code=303)
