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
    adapter_cache_s: int = 60              # segundos que se reusan sesiones y lecturas de GLPI/Zabbix (0 = sin caché)

    # -- Enrolamiento automatizado (instaladores por cliente) ----------------
    # URL pública de la consola (la que alcanza el appliance del cliente), para
    # armar el comando de instalación. Si falta, se deduce del request.
    console_public_url: str | None = None
    # Vigencia (horas) y usos máximos de un enlace de instalación.
    enroll_ttl_h: int = 24
    enroll_max_usos: int = 3
    # MSI de los agentes para puestos Windows (instalador .ps1).
    # Túnel al servidor Zabbix: hostname publicado por el Cloudflare Tunnel del
    # VPS (TCP) + service token de Cloudflare Access. COMUNES a todos los
    # clientes: la consola los incluye en cada instalador.
    zbx_tunnel_hostname: str | None = None
    cf_access_client_id: str | None = None
    cf_access_client_secret: str | None = None
    # Enlace de descarga para equipos Windows (se manda por correo al cliente).
    equipos_ttl_h: int = 168
    equipos_max_usos: int = 500
    # SMTP para enviar el enlace de instalación al cliente (opcional).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: str = "starttls"     # starttls | ssl | no
    glpi_agent_msi_url: str = ""
    zbx_agent_msi_url: str = ""
    cloudflared_win_url: str = ""
    mesh_url: str | None = None          # https://itier-mesh.<dominio> (MeshCentral, soporte remoto)
    mesh_user: str | None = None
    mesh_password: str | None = None
    alerta_disco_pct: int = 90             # alerta cuando un disco supera este % de uso
    zbx_soporte_grupo: str = "iTier Soporte"  # grupo de usuarios de Zabbix con acceso a los hosts de los clientes
    zbx_public_url: str | None = None      # URL pública de la web de Zabbix (solo para mostrarla en el correo de bienvenida)
    # -- Canales de entrada -----------------------------------------------------------------
    soporte_email: str | None = None       # casilla de soporte que se muestra a los clientes
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"
    imap_tls: str = "ssl"                  # ssl | starttls | no
    imap_poll_s: int = 60
    imap_auth: str = "password"            # password | oauth2_ms (Microsoft 365: app con permiso IMAP.AccessAsApp)
    imap_ms_tenant: str | None = None
    imap_ms_client_id: str | None = None
    imap_ms_secret: str | None = None
    wa_hook_token: str | None = None       # secreto en la URL del webhook de OpenWA (/hooks/whatsapp/<token>)
    wa_api_url: str | None = None          # API de OpenWA (para responder: confirmaciones)
    wa_api_key: str | None = None
    wa_numero: str | None = None           # número de soporte (solo para mostrarlo)
    canal_limite_hora: int = 20
    # -- Microsoft 365 (aplicación multi-tenant del MSP; se carga en la consola) -----------------
    m365_client_id: str | None = None
    m365_client_secret: str | None = None
    # -- Google Workspace (cuenta de servicio del MSP con delegación en todo el dominio) -----------
    gws_sa_json: str | None = None
    # -- Reporte de horas / rentabilidad (editables en la pantalla Horas) ---------------------
    fx_ars_usd: float | None = None        # cotización de referencia (BCRA), pesos por dólar
    fx_fecha: str | None = None
    costo_plataforma_usd: float = 60.0     # VPS y demás costos fijos mensuales, en USD            # mensajes por remitente y hora antes de frenarlo (anti-inundación)
    mesh_support_group: str = "SOPORTE"    # grupo de usuarios de Mesh (técnicos) con acceso a todos los clientes

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
            totp_issuer=os.environ.get("ITIER_CONSOLE_TOTP_ISSUER", "iTiers"),
            admin_user=os.environ.get("ITIER_CONSOLE_ADMIN_USER") or None,
            admin_password=os.environ.get("ITIER_CONSOLE_ADMIN_PASSWORD") or None,
            lockout_threshold=int(os.environ.get("ITIER_CONSOLE_LOCKOUT_THRESHOLD", "5")),
            lockout_window_s=int(os.environ.get("ITIER_CONSOLE_LOCKOUT_WINDOW", "900")),
            glpi_url=(os.environ.get("ITIER_GLPI_URL") or "").rstrip("/") or None,
            glpi_apirest_path=os.environ.get("ITIER_GLPI_APIREST_PATH", "/apirest.php"),
            glpi_app_token=os.environ.get("ITIER_GLPI_APP_TOKEN") or None,
            glpi_user_token=os.environ.get("ITIER_GLPI_USER_TOKEN") or None,
            public_glpi_url=(os.environ.get("ITIER_PUBLIC_GLPI_URL") or "").rstrip("/") or None,
            zbx_url=(os.environ.get("ITIER_ZBX_URL") or "").rstrip("/") or None,
            zbx_token=os.environ.get("ITIER_ZBX_TOKEN") or None,
            zbx_user=os.environ.get("ITIER_ZBX_USER") or None,
            zbx_password=os.environ.get("ITIER_ZBX_PASSWORD") or None,
            zbx_server_host=os.environ.get("ITIER_ZBX_SERVER_HOST") or None,
            http_timeout_s=float(os.environ.get("ITIER_CONSOLE_HTTP_TIMEOUT", "6")),
            adapter_cache_s=int(os.environ.get("ITIER_ADAPTER_CACHE_S", "60")),
            console_public_url=(os.environ.get("ITIER_CONSOLE_PUBLIC_URL") or "").rstrip("/") or None,
            enroll_ttl_h=int(os.environ.get("ITIER_ENROLL_TTL_H", "24")),
            enroll_max_usos=int(os.environ.get("ITIER_ENROLL_MAX_USOS", "3")),
            zbx_tunnel_hostname=(os.environ.get("ITIER_ZBX_TUNNEL_HOSTNAME") or "").strip() or (
                "itier-zbx.demo.example" if demo else None),
            cf_access_client_id=(os.environ.get("ITIER_CF_ACCESS_CLIENT_ID") or "").strip() or (
                "demo.access" if demo else None),
            cf_access_client_secret=(os.environ.get("ITIER_CF_ACCESS_CLIENT_SECRET") or "").strip() or (
                "demo-secreto-0123456789abcdef" if demo else None),
            equipos_ttl_h=int(os.environ.get("ITIER_EQUIPOS_TTL_H", "168")),
            equipos_max_usos=int(os.environ.get("ITIER_EQUIPOS_MAX_USOS", "500")),
            smtp_host=os.environ.get("ITIER_SMTP_HOST") or None,
            smtp_port=int(os.environ.get("ITIER_SMTP_PORT", "587")),
            smtp_user=os.environ.get("ITIER_SMTP_USER") or None,
            smtp_password=os.environ.get("ITIER_SMTP_PASSWORD") or None,
            smtp_from=os.environ.get("ITIER_SMTP_FROM") or os.environ.get("ITIER_SMTP_USER") or None,
            smtp_tls=(os.environ.get("ITIER_SMTP_TLS") or "starttls").lower(),
            glpi_agent_msi_url=os.environ.get("ITIER_GLPI_AGENT_MSI_URL") or (
                "https://github.com/glpi-project/glpi-agent/releases/download/1.21/GLPI-Agent-1.21-x64.msi"),
            # ⚠ Fijá la versión menor que exista en cdn.zabbix.com (misma mayor que el server).
            zbx_agent_msi_url=os.environ.get("ITIER_ZBX_AGENT_MSI_URL") or (
                "https://cdn.zabbix.com/zabbix/binaries/stable/7.0/7.0.22/"
                "zabbix_agent2-7.0.22-windows-amd64-openssl.msi"),
            mesh_url=(os.environ.get("ITIER_MESH_URL") or "").rstrip("/") or ("https://itier-mesh.demo.example" if demo else None),
            mesh_user=os.environ.get("ITIER_MESH_USER") or None,
            mesh_password=os.environ.get("ITIER_MESH_PASSWORD") or None,
            alerta_disco_pct=int(os.environ.get("ITIER_ALERTA_DISCO_PCT", "90")),
            zbx_soporte_grupo=(os.environ.get("ITIER_ZBX_SOPORTE_GROUP", "iTier Soporte") or "iTier Soporte").strip(),
            zbx_public_url=(os.environ.get("ITIER_ZBX_PUBLIC_URL") or "").rstrip("/") or None,
            soporte_email=(os.environ.get("ITIER_SOPORTE_EMAIL") or "").strip() or None,
            imap_host=os.environ.get("ITIER_IMAP_HOST") or None,
            imap_port=int(os.environ.get("ITIER_IMAP_PORT", "993")),
            imap_user=os.environ.get("ITIER_IMAP_USER") or None,
            imap_password=os.environ.get("ITIER_IMAP_PASSWORD") or None,
            imap_folder=os.environ.get("ITIER_IMAP_FOLDER") or "INBOX",
            imap_tls=(os.environ.get("ITIER_IMAP_TLS") or "ssl").lower(),
            imap_poll_s=max(15, int(os.environ.get("ITIER_IMAP_POLL_S", "60"))),
            imap_auth=(os.environ.get("ITIER_IMAP_AUTH") or "password").lower(),
            imap_ms_tenant=(os.environ.get("ITIER_IMAP_MS_TENANT") or "").strip() or None,
            imap_ms_client_id=(os.environ.get("ITIER_IMAP_MS_CLIENT_ID") or "").strip() or None,
            imap_ms_secret=os.environ.get("ITIER_IMAP_MS_SECRET") or None,
            wa_hook_token=(os.environ.get("ITIER_WA_HOOK_TOKEN") or "").strip() or None,
            wa_api_url=(os.environ.get("ITIER_WA_API_URL") or "").rstrip("/") or None,
            wa_api_key=os.environ.get("ITIER_WA_API_KEY") or None,
            wa_numero=(os.environ.get("ITIER_WA_NUMERO") or "").strip() or None,
            canal_limite_hora=int(os.environ.get("ITIER_CANAL_LIMITE_HORA", "20")),
            m365_client_id=(os.environ.get("ITIER_M365_CLIENT_ID") or "").strip() or None,
            m365_client_secret=os.environ.get("ITIER_M365_CLIENT_SECRET") or None,
            gws_sa_json=os.environ.get("ITIER_GWS_SA_JSON") or None,
            fx_ars_usd=float(os.environ["ITIER_FX_ARS_USD"]) if os.environ.get("ITIER_FX_ARS_USD") else None,
            costo_plataforma_usd=float(os.environ.get("ITIER_COSTO_PLATAFORMA_USD", "60")),
            mesh_support_group=(os.environ.get("ITIER_MESH_SUPPORT_GROUP", "SOPORTE") or "").strip(),
            cloudflared_win_url=os.environ.get("ITIER_CLOUDFLARED_WIN_URL") or (
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"),
        )

    # -- ayudas de estado ----------------------------------------------------

    @property
    def glpi_configurado(self) -> bool:
        return self.demo or bool(self.glpi_url and self.glpi_app_token)

    @property
    def zbx_configurado(self) -> bool:
        return self.demo or bool(self.zbx_url and (self.zbx_token or (self.zbx_user and self.zbx_password)))

    @property
    def gws_configurado(self) -> bool:
        return bool(self.gws_sa_json)

    @property
    def m365_configurado(self) -> bool:
        return bool(self.m365_client_id and self.m365_client_secret)

    @property
    def imap_configurado(self) -> bool:
        if self.imap_auth == "oauth2_ms":
            return bool(self.imap_host and self.imap_user and self.imap_ms_tenant and self.imap_ms_client_id and self.imap_ms_secret)
        return bool(self.imap_host and self.imap_user and self.imap_password)

    @property
    def wa_configurado(self) -> bool:
        return bool(self.wa_hook_token)

    @property
    def mesh_enabled(self) -> bool:
        return self.demo or bool(self.mesh_url and self.mesh_user and self.mesh_password)
