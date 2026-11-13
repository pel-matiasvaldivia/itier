"""Aplicación FastAPI de la consola de iTier.

    itier-console            # arranca uvicorn (producción tras NPM)
    python -m itier_console  # equivalente para desarrollo

Al iniciar: crea el esquema SQLite, siembra el admin (si corresponde) y monta las
rutas. La sesión viaja en una cookie firmada (itsdangerous) con flags httponly +
secure + samesite.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import security
from .config import Config
from .db import connect, init_db
from .deps import AccesoConsola, AccesoPortal, AuthRequired, CambioClaveRequerido, BASE, flash
from .routes import agents as r_agents
from .routes import audit as r_audit
from .routes import clients as r_clients
from .routes import dashboard as r_dashboard
from .routes import portal as r_portal
from .routes import tickets as r_tickets
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

    app = FastAPI(title="Consola de iTier", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg

    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie="itier_consola",
        https_only=cfg.cookie_secure,
        same_site="lax",
        max_age=cfg.session_max_age,
    )

    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

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
    app.include_router(r_tickets.router)
    app.include_router(r_audit.router)
    app.include_router(r_portal.router)

    log.info("Consola de iTier lista (demo=%s, glpi=%s, zabbix=%s).",
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
