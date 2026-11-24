"""Aplicación FastAPI de la consola de iTiers.

    itier-console            # arranca uvicorn (producción tras NPM)
    python -m itier_console  # equivalente para desarrollo

Al iniciar: crea el esquema SQLite, siembra el admin (si corresponde) y monta las
rutas. La sesión viaja en una cookie firmada (itsdangerous) con flags httponly +
secure + samesite.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import perf, security
from .config import Config
from .db import connect, init_db
from .deps import AccesoConsola, AlcanceRedirigir, AccesoPortal, AuthRequired, CambioClaveRequerido, PermisoDenegado, BASE, flash
from .routes import agents as r_agents
from .routes import activos as r_activos
from .routes import audit as r_audit
from .routes import canales as r_canales
from .routes import clients as r_clients
from .routes import curacion as r_curacion
from .routes import dashboard as r_dashboard
from .routes import discovery as r_discovery
from .routes import enrolamiento as r_enrolamiento
from .routes import equipos as r_equipos
from .routes import gws as r_gws
from .routes import m365 as r_m365
from .routes import manual as r_manual
from .routes import portal as r_portal
from .routes import reportes as r_reportes
from .routes import tickets as r_tickets
from .routes import usuarios as r_usuarios
from . import auth as r_auth

log = logging.getLogger("itier.consola")


def _sembrar_admin(cfg: Config) -> None:
    conn = connect(cfg.db_path)
    try:
        if security.contar_usuarios(conn) > 0:
            return
        if cfg.admin_user and cfg.admin_password:
            security.crear_usuario(conn, cfg.admin_user, cfg.admin_password, is_admin=True, must_change_pw=True)
            log.info("Usuario admin «%s» creado: cambiará la clave y enrolará 2FA en el primer login.", cfg.admin_user)
        elif cfg.demo:
            security.crear_usuario(conn, "admin", "itier-demo-admin", is_admin=True, must_change_pw=False)
            log.warning("DEMO: usuario admin / itier-demo-admin creado. NO usar en producción.")
        else:
            log.warning(
                "No hay usuarios y no se definió ITIER_CONSOLE_ADMIN_USER/PASSWORD. "
                "Creá uno con: itier-console-admin add --user <usuario> --admin"
            )
    finally:
        conn.close()


def create_app() -> FastAPI:
    logging.basicConfig(
        level=os.environ.get("ITIER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = Config.from_env()
    init_db(cfg.db_path)
    _sembrar_admin(cfg)

    app = FastAPI(title="Consola de iTiers", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    from .services import canales as _canales
    _canales.iniciar_poller(cfg)

    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie="itier_consola",
        https_only=cfg.cookie_secure,
        same_site="lax",
        max_age=cfg.session_max_age,
    )

    class EstaticosCacheados(StaticFiles):
        """Con `?v=<huella>` el archivo es inmutable (1 año); sin versión, se revalida."""
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            if resp.status_code in (200, 304):
                versionado = b"v=" in (scope.get("query_string") or b"")
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versionado else "no-cache"
            return resp

    app.mount("/static", EstaticosCacheados(directory=str(BASE / "static")), name="static")

    @app.middleware("http")
    async def _tiempos(request: Request, call_next):
        """Cuánto tarda cada request y cuánto de eso es GLPI/Zabbix/Mesh → cabecera Server-Timing y log si es lento."""
        acum = perf.iniciar()
        t0 = time.perf_counter()
        resp = await call_next(request)
        total = time.perf_counter() - t0
        if not request.url.path.startswith("/static") and request.url.path != "/healthz":
            resp.headers["Server-Timing"] = perf.server_timing(total, acum)
            if total > 1.0:
                log.warning("LENTO %s %s %.0f ms (%s)", request.method, request.url.path, total * 1000, perf.resumen(acum))
        return resp

    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, _exc: AuthRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CambioClaveRequerido)
    async def _cambio_requerido(request: Request, _exc: CambioClaveRequerido):
        flash(request, "Cambiá la contraseña inicial para continuar.", "info")
        return RedirectResponse("/cambiar-clave", status_code=303)

    @app.exception_handler(AccesoPortal)
    async def _acceso_portal(request: Request, _exc: AccesoPortal):
        return RedirectResponse("/portal", status_code=303)

    @app.exception_handler(AlcanceRedirigir)
    async def _alcance_redirigir(request: Request, exc: AlcanceRedirigir):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(PermisoDenegado)
    async def _permiso_denegado(request: Request, _exc: PermisoDenegado):
        flash(request, "Tu rol no tiene permiso para esa sección.", "error")
        return RedirectResponse("/", status_code=303)

    @app.exception_handler(AccesoConsola)
    async def _acceso_consola(request: Request, _exc: AccesoConsola):
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"status": "ok"})

    app.include_router(r_auth.router)
    app.include_router(r_dashboard.router)
    app.include_router(r_clients.router)
    app.include_router(r_agents.router)
    app.include_router(r_discovery.router)
    app.include_router(r_enrolamiento.router)
    app.include_router(r_equipos.router)
    app.include_router(r_usuarios.router)
    app.include_router(r_manual.router)
    app.include_router(r_curacion.router)
    app.include_router(r_tickets.router)
    app.include_router(r_canales.router)
    app.include_router(r_m365.router)
    app.include_router(r_activos.router)
    app.include_router(r_gws.router)
    app.include_router(r_reportes.router)
    app.include_router(r_audit.router)
    app.include_router(r_portal.router)

    log.info("Consola de iTiers lista (demo=%s, glpi=%s, zabbix=%s).",
             cfg.demo, cfg.glpi_configurado, cfg.zbx_configurado)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("ITIER_CONSOLE_HOST", "0.0.0.0")
    port = int(os.environ.get("ITIER_CONSOLE_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
