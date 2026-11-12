"""Configuración de la consola, leída del entorno.

Sin dependencias extra: un dataclass poblado desde variables de entorno. En modo
demo (ITIER_CONSOLE_DEMO=1) los adaptadores devuelven datos de fixture y no se
necesita ni GLPI ni Zabbix ni secretos reales: sirve para desarrollar y navegar
la UI. En modo real, las variables sensibles son obligatorias y la ausencia de
un secreto de sesión es un error (no se inventa uno silenciosamente).
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass

log = logging.getLogger("itier.consola.config")


def _bool(valor: str | None, defecto: bool = False) -> bool:
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


@dataclass(frozen=True)
class Config:
    # -- modo / sesión -------------------------------------------------------
    demo: bool
    secret_key: str
    db_path: str
    cookie_secure: bool
    session_max_age: int
    totp_issuer: str

    # -- semilla de administrador -------------------------------------------
    admin_user: str | None
    admin_password: str | None

    # -- anti fuerza bruta ---------------------------------------------------
    lockout_threshold: int
    lockout_window_s: int

    # -- GLPI ----------------------------------------------------------------
    glpi_url: str | None
    glpi_apirest_path: str
    glpi_app_token: str | None
    glpi_user_token: str | None
    # URL pública de GLPI (la que alcanza el cliente a través de NPM), para el
    # bundle de enrolamiento. Puede diferir de glpi_url, que la consola usa para
    # llegar a GLPI por la red interna del VPS.
    public_glpi_url: str | None

    # -- Zabbix --------------------------------------------------------------
    zbx_url: str | None
    zbx_token: str | None
    zbx_user: str | None
    zbx_password: str | None
    # Dirección (host:puerto o host) que marcan los zabbix-proxy de los clientes
    # desde internet. Va en el bundle de enrolamiento; no es la URL interna.
    zbx_server_host: str | None

    http_timeout_s: float

    @classmethod
    def from_env(cls) -> "Config":
        demo = _bool(os.environ.get("ITIER_CONSOLE_DEMO"), False)

        secret = os.environ.get("ITIER_CONSOLE_SECRET") or ""
        if not secret:
            if demo:
                secret = secrets.token_hex(32)
                log.warning(
                    "ITIER_CONSOLE_SECRET ausente: uso una clave de sesión EFÍMERA "
                    "(las sesiones se invalidan al reiniciar). Aceptable solo en demo."
                )
            else:
                raise RuntimeError(
                    "Falta ITIER_CONSOLE_SECRET: la clave para firmar las cookies de "
                    "sesión. Generá una con: openssl rand -hex 32"
                )

        return cls(
            demo=demo,
            secret_key=secret,
            db_path=os.environ.get("ITIER_CONSOLE_DB", "/data/consola.sqlite3"),
            # Por defecto exigimos cookies seguras (la consola va detrás de NPM con
            # TLS). Para probar en http://localhost poné ITIER_CONSOLE_COOKIE_SECURE=0.
            cookie_secure=_bool(os.environ.get("ITIER_CONSOLE_COOKIE_SECURE"), True),
            session_max_age=int(os.environ.get("ITIER_CONSOLE_SESSION_MAX_AGE", "28800")),
            totp_issuer=os.environ.get("ITIER_CONSOLE_TOTP_ISSUER", "iTier"),
            admin_user=os.environ.get("ITIER_CONSOLE_ADMIN_USER") or None,
            admin_password=os.environ.get("ITIER_CONSOLE_ADMIN_PASSWORD") or None,
            lockout_threshold=int(os.environ.get("ITIER_CONSOLE_LOCKOUT_THRESHOLD", "5")),
            lockout_window_s=int(os.environ.get("ITIER_CONSOLE_LOCKOUT_WINDOW", "900")),
            glpi_url=(os.environ.get("ITIER_GLPI_URL") or "").rstrip("/") or None,
            glpi_apirest_path=os.environ.get("ITIER_GLPI_APIREST_PATH", "/apirest.php"),
            glpi_app_token=os.environ.get("ITIER_GLPI_APP_TOKEN") or None,
            glpi_user_token=os.environ.get("ITIER_GLPI_USER_TOKEN") or None,
            public_glpi_url=(os.environ.get("ITIER_PUBLIC_GLPI_URL") or os.environ.get("ITIER_GLPI_URL") or "").rstrip("/") or None,
            zbx_url=(os.environ.get("ITIER_ZBX_URL") or "").rstrip("/") or None,
            zbx_token=os.environ.get("ITIER_ZBX_TOKEN") or None,
            zbx_user=os.environ.get("ITIER_ZBX_USER") or None,
            zbx_password=os.environ.get("ITIER_ZBX_PASSWORD") or None,
            zbx_server_host=os.environ.get("ITIER_ZBX_SERVER_HOST") or None,
            http_timeout_s=float(os.environ.get("ITIER_CONSOLE_HTTP_TIMEOUT", "6")),
        )

    # -- ayudas de estado ----------------------------------------------------

    @property
    def glpi_configurado(self) -> bool:
        return self.demo or bool(self.glpi_url and self.glpi_app_token)

    @property
    def zbx_configurado(self) -> bool:
        return self.demo or bool(self.zbx_url and (self.zbx_token or (self.zbx_user and self.zbx_password)))
