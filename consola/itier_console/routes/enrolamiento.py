"""Instaladores por cliente: página del operador + endpoints públicos por token.

  Operador (sesión + 2FA):
    GET  /clientes/{slug}/instaladores                  página con todo preconfigurado
    POST /clientes/{slug}/instaladores/enlace           genera el comando de una línea
    POST /clientes/{slug}/instaladores/enlace/{id}/revocar
    GET  /clientes/{slug}/instaladores/appliance.sh     descarga el instalador Linux
    GET  /clientes/{slug}/instaladores/windows.ps1      descarga el instalador Windows

  Appliance (sin sesión; el token del enlace es la credencial):
    GET  /enrolar/{token}/install.sh                    el instalador (consume un uso)
    POST /enrolar/{token}/reporte                       el appliance avisa cómo quedó

  Cliente final (sin sesión; enlace que el operador le manda por correo):
    GET  /descargar/{token}                             página con instrucciones
    GET  /descargar/{token}/instalador.cmd              instalador de Windows (doble clic)

  Equipos Windows (sin sesión; clave de reporte del cliente):
    POST /equipos/{clave}/reporte                       el equipo avisa cómo quedó
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .. import audit, security
from ..deps import flash, get_db, render, require_user
from ..adapters import glpi as glpi_mod, zabbix as zbx_mod
from ..deps import templates
from ..services import ServiceError, correo, enrolamiento, onboarding

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _base_publica(request: Request) -> str:
    cfg = request.app.state.cfg
    return cfg.console_public_url or str(request.base_url).rstrip("/")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _url_reporte_equipos(request: Request, conn: sqlite3.Connection, cli: dict, rol: str = "equipo") -> str:
    clave = enrolamiento.clave_central(conn, cli) if rol == "central" else enrolamiento.clave_reporte(conn, cli)
    return f"{_base_publica(request)}/equipos/{clave}/reporte"


def _transmision(cfg, cli: dict) -> tuple[dict, set, str]:
    """Qué equipos YA están llegando: hosts del grupo en Zabbix (con su
    disponibilidad) y CIs inventariados en GLPI. Best-effort."""
    zbx: dict[str, str] = {}
    glpi: set[str] = set()
    nota = ""
    try:
        with zbx_mod.cliente(cfg) as z:
            for h in z.listar_hosts(cli.get("zbx_hostgroup_id")):
                zbx[(h.get("host") or "").upper()] = str(h.get("available") or "0")
    except Exception as e:  # noqa: BLE001
        nota = f"Zabbix no respondió ({str(e)[:80]}). "
    try:
        with glpi_mod.cliente(cfg) as g:
            glpi = {(c.get("name") or "").upper() for c in g.listar_cis(cli.get("glpi_entity_id"))}
    except Exception as e:  # noqa: BLE001
        nota += f"GLPI no respondió ({str(e)[:80]})."
    return zbx, glpi, nota


def _cliente(request: Request, conn: sqlite3.Connection, slug: str) -> dict | None:
    cli = onboarding.obtener_cliente_por_slug(conn, slug)
    if not cli:
        flash(request, "Cliente no encontrado.", "error")
    return cli


# ── Operador ─────────────────────────────────────────────────────────────────

@router.get("/clientes/{slug}/instaladores", response_class=HTMLResponse)
def pagina(request: Request, slug: str, user: dict = Depends(require_user),
           conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    # El token en claro se muestra UNA sola vez, recién creado.
    nuevo = request.session.pop("_enlace_nuevo", None)
    comando = None
    if nuevo and nuevo.get("slug") == slug:
        comando = f"curl -fsSL {_base_publica(request)}/enrolar/{nuevo['token']}/install.sh | sudo bash"
    equipos = enrolamiento.listar_equipos(conn, cli["id"])
    nota_tx = ""
    if equipos:
        zbx, glpi, nota_tx = _transmision(cfg, cli)
        for e in equipos:
            e["en_glpi"] = e["host"] in glpi
            e["en_zabbix"] = e["host"] in zbx
            e["zbx_disponible"] = zbx.get(e["host"]) == "1"
    eq = request.session.pop("_enlace_equipos", None)
    enlace_equipos = mailto = None
    if eq and eq.get("slug") == slug:
        enlace_equipos = f"{_base_publica(request)}/descargar/{eq['token']}"
        asunto, cuerpo = correo.texto_instalacion(cli["nombre"], enlace_equipos, cfg.equipos_ttl_h // 24)
        mailto = "mailto:" + quote(eq.get("email") or "") + "?subject=" + quote(asunto) + "&body=" + quote(cuerpo)
    return render(
        request, "instaladores.html", cli=cli, comando=comando,
        faltantes=enrolamiento.faltantes(cfg, cli),
        enlaces=enrolamiento.listar_enlaces(conn, cli["id"]),
        ttl_h=cfg.enroll_ttl_h, max_usos=cfg.enroll_max_usos,
        equipos=equipos, nota_tx=nota_tx,
        tunel_ok=enrolamiento.tunel_configurado(cfg), tunel_host=cfg.zbx_tunnel_hostname, smtp=correo.configurado(cfg),
        enlace_equipos=enlace_equipos, mailto=mailto, equipos_dias=cfg.equipos_ttl_h // 24,
    )


@router.post("/clientes/{slug}/instaladores/enlace-equipos")
def enlace_equipos(request: Request, slug: str, email: str = Form(""), csrf: str = Form(""),
                   user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    """Genera el enlace de descarga del instalador de Windows y, si se indicó un
    correo y hay SMTP, se lo envía al cliente."""
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    destino = RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return destino
    cfg = request.app.state.cfg
    try:
        email = correo.validar_email(email) if email.strip() else ""
        token = enrolamiento.crear_enlace(conn, cfg, user["user"], cli, tipo="equipos")
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return destino
    request.session["_enlace_equipos"] = {"slug": slug, "token": token, "email": email}
    url = f"{_base_publica(request)}/descargar/{token}"
    if email and correo.configurado(cfg):
        try:
            asunto, cuerpo = correo.texto_instalacion(cli["nombre"], url, cfg.equipos_ttl_h // 24)
            correo.enviar(cfg, email, asunto, cuerpo)
            audit.registrar(conn, user["user"], "enrolamiento.correo.enviado",
                            {"cliente": cli["nombre"], "destinatario": email})
            flash(request, f"Enlace de instalación enviado a {email}.", "ok")
        except ServiceError as e:
            flash(request, e.mensaje + " Podés copiar el enlace y enviarlo vos.", "error")
    else:
        flash(request, "Enlace de instalación generado. Copialo o envialo ahora: no se vuelve a mostrar.", "ok")
    return destino


@router.post("/clientes/{slug}/instaladores/enlace")
def crear_enlace(request: Request, slug: str, csrf: str = Form(""),
                 user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    destino = RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return destino
    try:
        token = enrolamiento.crear_enlace(conn, request.app.state.cfg, user["user"], cli)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return destino
    request.session["_enlace_nuevo"] = {"slug": slug, "token": token}
    flash(request, "Comando de instalación generado. Copialo ahora: no se vuelve a mostrar.", "ok")
    return destino


@router.post("/clientes/{slug}/instaladores/enlace/{tid}/revocar")
def revocar(request: Request, slug: str, tid: int, csrf: str = Form(""),
            user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    if security.verificar_csrf(request.session, csrf):
        enrolamiento.revocar_enlace(conn, user["user"], cli, tid)
        flash(request, "Enlace revocado.", "ok")
    else:
        flash(request, "Sesión expirada, reintentá.", "error")
    return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)


def _descarga(cuerpo: str, nombre: str) -> Response:
    return Response(cuerpo, media_type="application/octet-stream",
                    headers={**_NO_CACHE, "Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/clientes/{slug}/instaladores/appliance.sh")
def bajar_appliance(request: Request, slug: str, user: dict = Depends(require_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    cfg = request.app.state.cfg
    try:
        falta = enrolamiento.faltantes(cfg, cli)
        if falta:
            raise ServiceError("No se puede generar el instalador: " + "; ".join(falta) + ".")
        # Descarga manual: sin token, así que el instalador no reporta a la consola.
        script = enrolamiento.instalador_linux(cfg, cli)
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    audit.registrar(conn, user["user"], "enrolamiento.instalador.descargado",
                    {"cliente": cli["nombre"], "tipo": "appliance", "via": "consola"})
    return _descarga(script, f"itier-{slug}-appliance.sh")


@router.get("/clientes/{slug}/instaladores/windows.{ext}")
def bajar_windows(request: Request, slug: str, ext: str, rol: str = "equipo",
                  user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    cli = _cliente(request, conn, slug)
    if not cli:
        return RedirectResponse("/clientes", status_code=303)
    try:
        if ext not in ("cmd", "ps1"):
            raise ServiceError("Formato de instalador desconocido.")
        if rol not in ("equipo", "central"):
            raise ServiceError("Rol de instalador desconocido.")
        script = enrolamiento.instalador_windows(
            request.app.state.cfg, cli, formato=ext, rol=rol,
            url_reporte=_url_reporte_equipos(request, conn, cli, rol))
    except ServiceError as e:
        flash(request, e.mensaje, "error")
        return RedirectResponse(f"/clientes/{slug}/instaladores", status_code=303)
    audit.registrar(conn, user["user"], "enrolamiento.instalador.descargado",
                    {"cliente": cli["nombre"], "tipo": f"windows.{ext}", "rol": rol, "via": "consola"})
    base = "central" if rol == "central" else ("instalar" if ext == "cmd" else "windows")
    nombre = f"itier-{slug}-{base}.{ext}"
    return _descarga(script, nombre)


# ── Appliance (público, por token) ───────────────────────────────────────────

_RECHAZO = "echo '[iTier] Enlace de instalacion invalido, vencido o agotado. Genera uno nuevo en la consola.' >&2; exit 1\n"


@router.get("/enrolar/{token}/install.sh")
def instalar(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    cfg = request.app.state.cfg
    cli = enrolamiento.canjear(conn, token, _ip(request))
    if not cli:
        # 404 + cuerpo que falla limpio si igual lo pasan por bash.
        return PlainTextResponse(_RECHAZO, status_code=404, headers=_NO_CACHE)
    try:
        script = enrolamiento.instalador_linux(
            cfg, cli, url_reporte=f"{_base_publica(request)}/enrolar/{token}/reporte")
    except ServiceError:
        return PlainTextResponse(_RECHAZO, status_code=404, headers=_NO_CACHE)
    return PlainTextResponse(script, media_type="text/x-shellscript", headers=_NO_CACHE)


@router.post("/enrolar/{token}/reporte")
def reporte(request: Request, token: str, estado: str = Form(""), host: str = Form(""),
            ip: str = Form(""), conn: sqlite3.Connection = Depends(get_db)):
    ok = enrolamiento.registrar_reporte(conn, token, _ip(request), estado, host, ip)
    return PlainTextResponse("ok\n" if ok else "rechazado\n", status_code=200 if ok else 404, headers=_NO_CACHE)


# ── Cliente final (público, por token): descarga del instalador de Windows ───

@router.get("/descargar/{token}", response_class=HTMLResponse)
def pagina_descarga(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    cli = enrolamiento.cliente_de_enlace(conn, token, "equipos")
    return templates.TemplateResponse(
        request, "descarga_publica.html", {"cli": cli, "token": token},
        status_code=200 if cli else 404, headers=_NO_CACHE)


@router.get("/descargar/{token}/instalador.cmd")
def descargar_instalador(request: Request, token: str, conn: sqlite3.Connection = Depends(get_db)):
    cli = enrolamiento.canjear(conn, token, _ip(request), tipo="equipos")
    if not cli:
        return PlainTextResponse("Enlace invalido o vencido.\n", status_code=404, headers=_NO_CACHE)
    try:
        script = enrolamiento.instalador_windows(
            request.app.state.cfg, cli, formato="cmd", url_reporte=_url_reporte_equipos(request, conn, cli))
    except ServiceError:
        return PlainTextResponse("Enlace invalido o vencido.\n", status_code=404, headers=_NO_CACHE)
    return _descarga(script, f"itier-{cli['slug']}-instalar.cmd")


# ── Equipos Windows: reporte del resultado de la instalación ─────────────────

@router.post("/equipos/{clave}/reporte")
async def reporte_equipo(request: Request, clave: str, conn: sqlite3.Connection = Depends(get_db)):
    if int(request.headers.get("content-length") or 0) > 8192:
        return PlainTextResponse("demasiado grande\n", status_code=413)
    form = await request.form()
    datos = {k: str(form.get(k) or "") for k in (
        "host", "estado", "os", "glpi_servicio", "glpi_conexion", "zbx_servicio", "zbx_conexion", "detalle", "ip")}
    ok = enrolamiento.registrar_equipo(conn, clave, _ip(request), datos)
    return PlainTextResponse("ok\n" if ok else "rechazado\n", status_code=200 if ok else 404, headers=_NO_CACHE)
