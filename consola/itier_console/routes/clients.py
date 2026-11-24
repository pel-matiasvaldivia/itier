"""Clientes: listado, alta (onboarding) y detalle con el bundle de enrolamiento."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import audit, security
from ..deps import flash, get_db, render, require_permiso
from ..services import ServiceError, correo
from ..services import onboarding
from ..services import tickets as tickets_svc
from ..adapters import AdapterError

router = APIRouter()


@router.get("/clientes", response_class=HTMLResponse)
def listar(request: Request, user: dict = Depends(require_permiso("clientes.ver")), conn: sqlite3.Connection = Depends(get_db)):
    from datetime import datetime, timedelta
    cfg = request.app.state.cfg
    eq = {r["client_id"]: dict(r) for r in conn.execute(
        "SELECT client_id, COUNT(*) n, SUM(estado != 'ok') n_err, MAX(ts) ultimo FROM endpoint_reports GROUP BY client_id")}
    act = {r["client_id"]: dict(r) for r in conn.execute(
        "SELECT client_id, COUNT(*) n, SUM(glpi_id IS NULL AND tipo IN ('notebook','desktop')) sin_agente "
        "FROM activos WHERE estado != 'de_baja' GROUP BY client_id")}
    ctc = {r["client_id"]: r["n"] for r in conn.execute(
        "SELECT client_id, COUNT(*) n FROM client_contactos WHERE activo = 1 GROUP BY client_id")}
    clientes = onboarding.listar_clientes(conn)

    # Tickets abiertos por cliente: una sola consulta a GLPI agrupada por entidad (si GLPI cae, se muestra «—»).
    por_ent, glpi_ok = {}, False
    if cfg.glpi_configurado:
        try:
            for t in tickets_svc.listar(cfg, limit=500):
                st = int(t.get("status", 0) or 0)
                if st not in (1, 2, 3, 4):
                    continue
                d = por_ent.setdefault(str(t.get("entities_id")), {"abiertos": 0, "nuevos": 0, "urgentes": 0})
                d["abiertos"] += 1
                d["nuevos"] += st == 1
                d["urgentes"] += int(t.get("priority", 0) or 0) >= 5
            glpi_ok = True
        except AdapterError:
            pass

    limite = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    for c in clientes:
        e = eq.get(c["id"], {})
        c["n_equipos"] = e.get("n", 0)
        c["equipos_err"] = e.get("n_err") or 0
        c["equipos_ultimo"] = (e.get("ultimo") or "")[:16]
        c["equipos_viejo"] = bool(e.get("ultimo")) and e["ultimo"] < limite
        a = act.get(c["id"], {})
        c["n_activos"], c["sin_agente"] = a.get("n", 0), a.get("sin_agente") or 0
        c["n_contactos"] = ctc.get(c["id"], 0)
        t = por_ent.get(str(c.get("glpi_entity_id")), {}) if glpi_ok else None
        c["tk"] = (t or {"abiertos": 0, "nuevos": 0, "urgentes": 0}) if glpi_ok else None
        motivos = []
        if c["estado"] != "activo":
            motivos.append("Aprovisionamiento: " + c["estado"])
        if c["tk"] and c["tk"]["urgentes"]:
            motivos.append(f"{c['tk']['urgentes']} ticket(s) urgente(s)")
        if c["equipos_err"]:
            motivos.append(f"{c['equipos_err']} equipo(s) con error de agente")
        if c["n_equipos"] and c["equipos_viejo"]:
            motivos.append("Equipos sin reportar hace >2 días")
        c["motivos"] = motivos
        c["prio"] = (0 if c["estado"] != "activo" else 1 if (c["tk"] and c["tk"]["urgentes"]) else
                     2 if (c["equipos_err"] or c["equipos_viejo"]) else 3 if (c["tk"] and c["tk"]["nuevos"]) else 4)
    clientes.sort(key=lambda c: (c["prio"], -(c["tk"]["abiertos"] if c["tk"] else 0), c["nombre"].lower()))
    resumen = {"total": len(clientes), "activos": sum(1 for c in clientes if c["estado"] == "activo"),
               "avisos": sum(1 for c in clientes if c["motivos"]),
               "equipos": sum(c["n_equipos"] for c in clientes),
               "abiertos": sum(c["tk"]["abiertos"] for c in clientes if c["tk"]),
               "urgentes": sum(c["tk"]["urgentes"] for c in clientes if c["tk"]),
               "sin_equipos": sum(1 for c in clientes if not c["n_equipos"]), "glpi_ok": glpi_ok}
    return render(request, "clientes_lista.html", clientes=clientes, resumen=resumen)


@router.get("/clientes/nuevo", response_class=HTMLResponse)
def nuevo_form(request: Request, user: dict = Depends(require_permiso("clientes.editar"))):
    return render(request, "clientes_nuevo.html")


@router.post("/clientes/nuevo")
def nuevo_post(
    request: Request,
    nombre: str = Form(...),
    notas: str = Form(""),
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("clientes.editar")),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/clientes/nuevo", status_code=303)
    cfg = request.app.state.cfg
    try:
        cli = onboarding.alta_cliente(conn, cfg, user["user"], nombre, notas)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        if e.cliente_id:
            # Alta parcial: igual existe el registro, llevamos al detalle.
            cli = onboarding.obtener_cliente(conn, e.cliente_id)
            return RedirectResponse(f"/clientes/{cli['slug']}", status_code=303)
        return RedirectResponse("/clientes/nuevo", status_code=303)
    flash(request, f"Cliente «{cli['nombre']}» dado de alta. Ya podés generar sus instaladores.", "ok")
    return RedirectResponse(f"/clientes/{cli['slug']}/instaladores", status_code=303)


@router.get("/clientes/{slug}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, user: dict = Depends(require_permiso("clientes.ver")), conn: sqlite3.Connection = Depends(get_db)):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    bundle = onboarding.bundle_env(cfg, cli)
    usuarios = security.listar_usuarios_cliente(conn, cli["id"])
    from ..services import ajustes, canales
    ef = ajustes.efectiva(conn, cfg)
    return render(request, "clientes_detalle.html", cli=cli, bundle=bundle, usuarios=usuarios,
                  dominios=canales.dominios_de(conn, cli["id"]), contactos=canales.contactos_de(conn, cli["id"]),
                  soporte_email=ef.soporte_email, wa_numero=ef.wa_numero, m365_app_ok=ef.m365_configurado)


@router.post("/clientes/{slug}/portal-usuarios")
def crear_usuario_portal(
    request: Request,
    slug: str,
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(""),
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("clientes.editar")),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    username = username.strip()
    if not username:
        flash(request, "Falta el usuario.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    if len(password) < 10:
        flash(request, "La contraseña temporal debe tener al menos 10 caracteres.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    if security.obtener_usuario(conn, username):
        flash(request, f"El usuario «{username}» ya existe.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    if email.strip():
        try:
            email = correo.validar_email(email)
        except ServiceError as e:
            flash(request, e.mensaje, "error")
            return RedirectResponse(f"/clientes/{slug}", status_code=303)
    security.crear_usuario(conn, username, password, must_change_pw=True, client_id=int(cli["id"]), email=email.strip() or None)
    audit.registrar(conn, user["user"], "portal.usuario.alta", {"cliente": cli["nombre"], "username": username, "email": email.strip() or None})
    flash(request, f"Usuario de portal «{username}» creado. Pasale la clave; cambia clave y enrola 2FA al entrar.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/portal-usuarios/{uid}/eliminar")
def eliminar_usuario_portal(
    request: Request,
    slug: str,
    uid: int,
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("clientes.editar")),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    u = security.obtener_usuario_id(conn, uid)
    if not u or u["client_id"] != cli["id"]:
        flash(request, "Usuario no encontrado para este cliente.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    security.eliminar_usuario(conn, uid)
    audit.registrar(conn, user["user"], "portal.usuario.baja", {"cliente": cli["nombre"], "username": u["username"]})
    flash(request, f"Usuario de portal «{u['username']}» eliminado.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/mesh")
def mesh_guardar(request: Request, slug: str, mesh_url: str = Form(""), mesh_user: str = Form(""), mesh_clave: str = Form(""),
                 csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
                 conn: sqlite3.Connection = Depends(get_db)):
    """Servidor MeshCentral propio del cliente (con su dominio) y su usuario de servicio: los agentes ya instalados no se tocan."""
    from ..adapters import mesh as mesh_mod
    from ..services import mesh_cliente as mp
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if security.verificar_csrf(request.session, csrf):
        try:
            mp.guardar(conn, request.app.state.cfg, cli["id"], mesh_url, mesh_user, mesh_clave)
            cli = onboarding.obtener_cliente_por_slug(conn, slug)
            with mesh_mod.cliente(mp.cfg_de(request.app.state.cfg, cli)) as m:
                n = len(m.equipos(None))
            audit.registrar(conn, user["user"], "mesh.propio_configurado", {"cliente": cli["nombre"], "url": cli["mesh_url"]})
            flash(request, f"Mesh del cliente conectado: {n} equipos visibles.", "ok")
        except (ServiceError, AdapterError) as e:
            flash(request, "Se guardó, pero no se pudo verificar: " + (getattr(e, "mensaje", None) or getattr(e, "detalle", str(e))), "error")
    else:
        flash(request, "Sesión expirada, reintentá.", "error")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/mesh/quitar")
def mesh_quitar(request: Request, slug: str, csrf: str = Form(""), user: dict = Depends(require_permiso("clientes.editar")),
                conn: sqlite3.Connection = Depends(get_db)):
    from ..services import mesh_cliente as mp
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if cli and security.verificar_csrf(request.session, csrf):
        mp.quitar(conn, cli["id"])
        audit.registrar(conn, user["user"], "mesh.propio_quitado", {"cliente": cli["nombre"]})
        flash(request, "Se volvió al servidor Mesh de iTiers.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/reintentar")
def reintentar(
    request: Request,
    slug: str,
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("clientes.editar")),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        cli = onboarding.reprovisionar(conn, request.app.state.cfg, user["user"], cli["id"])
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    flash(request, f"«{cli['nombre']}» quedó aprovisionado.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)


@router.post("/clientes/{slug}/eliminar")
def eliminar(
    request: Request,
    slug: str,
    csrf: str = Form(""),
    user: dict = Depends(require_permiso("clientes.eliminar")),
    conn: sqlite3.Connection = Depends(get_db),
):
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        onboarding.eliminar_cliente(conn, user["user"], cli["id"])
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    flash(request, f"Registro de «{cli['nombre']}» eliminado (GLPI/Zabbix no se tocaron).", "ok")
    return RedirectResponse("/clientes", status_code=303)


@router.post("/clientes/{slug}/portal-usuarios/{uid}/email")
def email_usuario_portal(request: Request, slug: str, uid: int, email: str = Form(""), csrf: str = Form(""),
                         user: dict = Depends(require_permiso("clientes.editar")), conn: sqlite3.Connection = Depends(get_db)):
    """Correo de un usuario del portal: ahí recibe los avisos cuando soporte responde sus tickets."""
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
        return RedirectResponse("/clientes", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    fila = conn.execute("SELECT username FROM users WHERE id = ? AND client_id = ?", (uid, cli["id"])).fetchone()
    if not fila:
        flash(request, "Usuario no encontrado.", "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    try:
        valor = correo.validar_email(email) if email.strip() else None
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}", status_code=303)
    conn.execute("UPDATE users SET email = ? WHERE id = ?", (valor, uid))
    conn.commit()
    audit.registrar(conn, user["user"], "portal.usuario.correo", {"cliente": cli["nombre"], "username": fila["username"], "email": valor})
    flash(request, f"Correo de «{fila['username']}» {'guardado' if valor else 'quitado'}.", "ok")
    return RedirectResponse(f"/clientes/{slug}", status_code=303)
