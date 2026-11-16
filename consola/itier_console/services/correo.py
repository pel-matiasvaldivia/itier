"""Envío de correo (SMTP) para mandarle al cliente su enlace de instalación.

Opcional: si no hay ITIER_SMTP_HOST, la consola muestra el enlace y un `mailto:`
para que el operador lo envíe desde su propio correo.

Se manda un ENLACE, nunca el instalador adjunto: los servidores de correo
(Gmail, Outlook/Microsoft 365) bloquean adjuntos ejecutables (.exe/.cmd/.ps1).
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage

from . import ServiceError

_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")


def validar_email(email: str) -> str:
    email = (email or "").strip()
    if not _RE_EMAIL.match(email):
        raise ServiceError("La dirección de correo no es válida.")
    return email


def configurado(cfg) -> bool:
    return bool(cfg.smtp_host and cfg.smtp_from)


def texto_instalacion(nombre_cliente: str, url: str, dias: int) -> tuple[str, str]:
    asunto = f"Instalación del agente de soporte — {nombre_cliente}"
    cuerpo = (
        f"Hola,\n\n"
        f"Para dejar los equipos de {nombre_cliente} bajo monitoreo y soporte, hay que instalar\n"
        f"un agente en cada computadora con Windows. Lleva un par de minutos:\n\n"
        f"  1. Abrir este enlace en la computadora a instalar:\n"
        f"     {url}\n"
        f"  2. Descargar el instalador y abrirlo con doble clic.\n"
        f"  3. Si Windows muestra un aviso de seguridad, elegir «Más información» →\n"
        f"     «Ejecutar de todas formas», y responder «Sí» al pedido de permisos.\n\n"
        f"La instalación es automática y no hay que configurar nada. El enlace vence en {dias} días.\n\n"
        f"Ante cualquier duda, respondé este correo.\n"
    )
    return asunto, cuerpo


def enviar(cfg, destinatario: str, asunto: str, cuerpo: str) -> None:
    if not configurado(cfg):
        raise ServiceError("El envío de correo no está configurado (ITIER_SMTP_HOST / ITIER_SMTP_FROM).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg.smtp_from, destinatario, asunto
    msg.set_content(cuerpo)
    try:
        if cfg.smtp_tls == "ssl":
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
        with smtp:
            if cfg.smtp_tls == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise ServiceError(f"No se pudo enviar el correo: {str(e)[:160]}") from e
