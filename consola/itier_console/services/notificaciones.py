"""Avisos al cliente cuando soporte responde o resuelve su ticket.

A quién se avisa, en este orden: 1) quien pidió el soporte, por el canal por el que lo hizo (WhatsApp, correo o el
portal → el correo de ese usuario); 2) si no hay a quién (ticket abierto desde la consola, sin correo cargado),
los usuarios del portal del cliente y sus contactos que tengan correo. El mensaje lleva el enlace al ticket en el portal.
"""

from __future__ import annotations

import logging
import sqlite3

from . import ServiceError, canales, correo

log = logging.getLogger("itier.notificaciones")
MAX_DESTINATARIOS = 5


def _correo_valido(e: str | None) -> str | None:
    try:
        return correo.validar_email(e) if e else None
    except ServiceError:
        return None


def destinatarios(conn: sqlite3.Connection, ticket_id: int, client_id: int | None) -> list[dict]:
    """[{canal: whatsapp|correo, destino, etiqueta}]"""
    res: list[dict] = []
    o = conn.execute("SELECT canal, remitente FROM ticket_origen WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if o:
        if o["canal"] == "whatsapp":
            quien = "ID interno" if o["remitente"].startswith("lid:") else f"+{o['remitente']}"
            res.append({"canal": "whatsapp", "destino": o["remitente"], "etiqueta": f"WhatsApp {quien}"})
        elif o["canal"] == "email":
            res.append({"canal": "correo", "destino": o["remitente"], "etiqueta": o["remitente"]})
        elif o["canal"] == "portal" and client_id:
            u = conn.execute("SELECT email FROM users WHERE username = ? AND client_id = ?", (o["remitente"], client_id)).fetchone()
            mail = _correo_valido(u["email"]) if u else None
            if mail:
                res.append({"canal": "correo", "destino": mail, "etiqueta": mail})
    if not res and client_id:
        vistos: set[str] = set()
        filas = [r["email"] for r in conn.execute("SELECT email FROM users WHERE client_id = ? AND disabled = 0 AND email IS NOT NULL", (client_id,))]
        filas += [r["email"] for r in conn.execute("SELECT email FROM client_contactos WHERE client_id = ? AND email IS NOT NULL", (client_id,))]
        for e in filas:
            mail = _correo_valido(e)
            if mail and mail.lower() not in vistos and len(res) < MAX_DESTINATARIOS:
                vistos.add(mail.lower())
                res.append({"canal": "correo", "destino": mail, "etiqueta": mail})
    return res


def _enlace(cfg, ticket_id: int) -> str:
    return f"{cfg.console_public_url}/portal/tickets/{ticket_id}" if cfg.console_public_url else ""


def notificar(cfg, conn: sqlite3.Connection, ticket_id: int, client_id: int | None, titulo: str, texto: str) -> dict:
    """Manda `texto` a quien corresponda. Devuelve {enviados: [etiquetas], errores: [mensajes], sin_destino: bool}."""
    dest = destinatarios(conn, ticket_id, client_id)
    out = {"enviados": [], "errores": [], "sin_destino": not dest}
    enlace = _enlace(cfg, ticket_id)
    base = canales._RE_HILO.sub("", canales._RE_PREFIJO.sub("", titulo or "")).strip() or "Tu consulta de soporte"
    for d in dest:
        try:
            if d["canal"] == "whatsapp":
                canales.responder_whatsapp(cfg, d["destino"], texto + (f"\n\nSeguimiento: {enlace}" if enlace else ""))
            else:
                if not correo.configurado(cfg):
                    raise ServiceError("El envío de correo (SMTP) no está configurado.")
                cuerpo = (f"{texto}\n\n" + (f"Podés ver el ticket y responder en el portal: {enlace}\n" if enlace else "") +
                          "También podés responder este correo sin cambiar el asunto.\n\n— Soporte")
                correo.enviar(cfg, d["destino"], f"Re: [#{ticket_id}] {base}", cuerpo)
            out["enviados"].append(d["etiqueta"])
        except Exception as e:  # noqa: BLE001 — un destinatario caído no frena al resto
            out["errores"].append(f"{d['etiqueta']}: {getattr(e, 'mensaje', None) or e}")
            log.info("No se pudo avisar a %s del ticket %s: %s", d["etiqueta"], ticket_id, e)
    return out


def avisar_a_soporte(cfg, ticket_id: int, titulo: str, cliente: str, quien: str, texto: str) -> bool:
    """El cliente respondió desde el portal: se le avisa a la casilla de soporte (mejor esfuerzo)."""
    if not (cfg.soporte_email and correo.configurado(cfg)):
        return False
    try:
        correo.enviar(cfg, cfg.soporte_email, f"[#{ticket_id}] Respuesta del cliente · {cliente}",
                      f"{quien} ({cliente}) respondió el ticket #{ticket_id} «{titulo}»:\n\n{texto}\n\n"
                      + (f"Consola: {cfg.console_public_url}/tickets/{ticket_id}\n" if cfg.console_public_url else ""))
        return True
    except Exception as e:  # noqa: BLE001
        log.info("No se pudo avisar a soporte del ticket %s: %s", ticket_id, e)
        return False


def es_del_cliente(contenido: str) -> bool:
    """Los mensajes del cliente (portal, WhatsApp, correo) se reconocen por su marca; el resto es de soporte."""
    c = (contenido or "").lstrip()
    return c.startswith("Cliente ·") or "— Recibido por " in c
