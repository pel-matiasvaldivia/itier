"""Canales de entrada: WhatsApp (OpenWA) y correo → tickets del cliente correcto.

Todo lo que llega por un canal pasa por `registrar_entrante`:

  1. Se guarda en la bandeja (`entrantes`) — sirve de registro y de triage.
  2. Se identifica al cliente: contacto exacto (correo/WhatsApp) > dominio de correo
     registrado > nadie (queda «sin cliente» para que el PM lo asigne a mano).
  3. Con cliente → se abre un ticket en SU entidad de GLPI, o se suma un seguimiento si
     es la respuesta a un ticket ya abierto (`[#id]` en el asunto; conversación de
     WhatsApp de las últimas 24 h).
  4. Se le confirma al remitente (mejor esfuerzo) y se anota el origen del ticket.

Defensas: dedupe por Message-ID, tope de mensajes por remitente y hora, autorrespuestas
ignoradas (evita bucles), correo con SPF/DKIM/DMARC fallido → «revisar» (puede ser
suplantación), y un seguimiento solo se acepta sobre tickets del MISMO cliente.
"""

from __future__ import annotations

import email as email_mod
import html
import imaplib
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parseaddr

import httpx

from .. import audit, db as db_mod
from . import ServiceError
from . import correo as correo_svc
from . import tickets as tickets_svc

log = logging.getLogger("itier.canales")

# Dominios públicos: registrarlos asociaría a CUALQUIER persona del mundo con un cliente.
DOMINIOS_LIBRES = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.com.ar", "outlook.com", "outlook.com.ar",
    "live.com", "live.com.ar", "msn.com", "yahoo.com", "yahoo.com.ar", "yahoo.es", "ymail.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "zoho.com",
    "fibertel.com.ar", "speedy.com.ar", "arnet.com.ar", "telecentro.com.ar", "mail.com",
}

MAX_CUERPO = 8000
_RE_DOMINIO = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_RE_HILO = re.compile(r"\[#(\d{1,9})\]")
_RE_PREFIJO = re.compile(r"^\s*((re|rv|fwd?|reenv)\s*:\s*)+", re.I)
_RE_CITA = re.compile(
    r"^(>|on .{5,200} wrote:|el .{5,200} escribi[óo]:|-{2,}\s*(original message|mensaje original)|"
    r"de:\s.+|from:\s.+@.+)", re.I)
_REMITENTES_AUTOMATICOS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster")


# --------------------------------------------------------------------------- normalización

def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalizar_email(s: str | None) -> str | None:
    s = (s or "").strip().lower()
    return s if correo_svc._RE_EMAIL.match(s) else None


def dominio_de(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def normalizar_dominio(s: str) -> str:
    s = (s or "").strip().lower().lstrip("@")
    if "@" in s:
        s = dominio_de(s)
    if not _RE_DOMINIO.match(s):
        raise ServiceError("El dominio no es válido (ej.: cuyopack.com.ar).")
    if s in DOMINIOS_LIBRES:
        raise ServiceError(
            f"«{s}» es un correo público: registrarlo asociaría a cualquier persona con este cliente. "
            "Cargá a cada persona como contacto en su lugar.")
    return s


def normalizar_whatsapp(s: str | None) -> str | None:
    """Solo dígitos, formato internacional de Argentina para celulares: 549 + área + número."""
    if not s:
        return None
    s = s.strip()
    # WhatsApp ya no siempre muestra el número: a veces identifica al contacto con un ID interno
    # («168199593165032@lid» o «lid:168199593165032»). Se guarda tal cual, con el prefijo «lid:».
    if s.lower().endswith("@lid") or s.lower().startswith("lid:"):
        d = re.sub(r"\D", "", s)
        return f"lid:{d}" if 8 <= len(d) <= 20 else None
    s = s.split("@", 1)[0]                       # 5491155551234@c.us
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 10:                              # 11 5555 1234 sin país
        d = "549" + d
    elif d.startswith("54") and not d.startswith("549") and len(d) == 12:
        d = "549" + d[2:]
    return d if 8 <= len(d) <= 15 else None


# --------------------------------------------------------------------------- dominios y contactos

def agregar_dominio(conn: sqlite3.Connection, client_id: int, dominio: str, actor: str) -> str:
    dom = normalizar_dominio(dominio)
    ya = conn.execute("SELECT client_id FROM client_dominios WHERE dominio = ?", (dom,)).fetchone()
    if ya:
        raise ServiceError(f"El dominio «{dom}» ya está asociado a un cliente.")
    conn.execute("INSERT INTO client_dominios(client_id, dominio, created_at, created_by) VALUES (?,?,?,?)",
                 (client_id, dom, ahora(), actor))
    conn.commit()
    audit.registrar(conn, actor, "canal.dominio_agregado", {"client_id": client_id, "dominio": dom})
    return dom


def quitar_dominio(conn: sqlite3.Connection, client_id: int, dominio_id: int, actor: str) -> None:
    r = conn.execute("SELECT dominio FROM client_dominios WHERE id = ? AND client_id = ?", (dominio_id, client_id)).fetchone()
    if not r:
        return
    conn.execute("DELETE FROM client_dominios WHERE id = ?", (dominio_id,))
    conn.commit()
    audit.registrar(conn, actor, "canal.dominio_quitado", {"client_id": client_id, "dominio": r["dominio"]})


def agregar_contacto(conn: sqlite3.Connection, client_id: int, nombre: str, email: str, whatsapp: str, actor: str) -> int:
    em = normalizar_email(email) if (email or "").strip() else None
    if (email or "").strip() and not em:
        raise ServiceError("El correo no es válido.")
    wa = normalizar_whatsapp(whatsapp) if (whatsapp or "").strip() else None
    if (whatsapp or "").strip() and not wa:
        raise ServiceError("El número de WhatsApp no es válido (ej.: 11 5555 1234 o +54 9 11 5555 1234).")
    if not em and not wa:
        raise ServiceError("Cargá al menos un correo o un número de WhatsApp.")
    for campo, val in (("email", em), ("whatsapp", wa)):
        if val and conn.execute(f"SELECT 1 FROM client_contactos WHERE {campo} = ?", (val,)).fetchone():
            raise ServiceError(f"«{val}» ya está cargado como contacto de un cliente.")
    cur = conn.execute(
        "INSERT INTO client_contactos(client_id, nombre, email, whatsapp, created_at, created_by) VALUES (?,?,?,?,?,?)",
        (client_id, (nombre or "").strip() or None, em, wa, ahora(), actor))
    conn.commit()
    audit.registrar(conn, actor, "canal.contacto_agregado",
                    {"client_id": client_id, "email": em, "whatsapp": wa})
    return cur.lastrowid


def quitar_contacto(conn: sqlite3.Connection, client_id: int, contacto_id: int, actor: str) -> None:
    r = conn.execute("SELECT * FROM client_contactos WHERE id = ? AND client_id = ?", (contacto_id, client_id)).fetchone()
    if not r:
        return
    conn.execute("DELETE FROM client_contactos WHERE id = ?", (contacto_id,))
    conn.commit()
    audit.registrar(conn, actor, "canal.contacto_quitado",
                    {"client_id": client_id, "email": r["email"], "whatsapp": r["whatsapp"]})


def dominios_de(conn, client_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM client_dominios WHERE client_id = ? ORDER BY dominio", (client_id,))]


def contactos_de(conn, client_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM client_contactos WHERE client_id = ? ORDER BY nombre, email", (client_id,))]


def identificar_cliente(conn: sqlite3.Connection, canal: str, remitente: str) -> tuple[int | None, str]:
    """(client_id, cómo) — «contacto» > «dominio» > (None, «»)."""
    if canal == "whatsapp":
        r = conn.execute("SELECT client_id FROM client_contactos WHERE whatsapp = ?", (remitente,)).fetchone()
        return (r["client_id"], "contacto") if r else (None, "")
    r = conn.execute("SELECT client_id FROM client_contactos WHERE email = ?", (remitente,)).fetchone()
    if r:
        return r["client_id"], "contacto"
    r = conn.execute("SELECT client_id FROM client_dominios WHERE dominio = ?", (dominio_de(remitente),)).fetchone()
    return (r["client_id"], "dominio") if r else (None, "")


# --------------------------------------------------------------------------- ingesta común

def _limite_excedido(conn, cfg, canal: str, remitente: str) -> bool:
    desde = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    n = conn.execute("SELECT COUNT(*) n FROM entrantes WHERE canal = ? AND remitente = ? AND recibido_at >= ?",
                     (canal, remitente, desde)).fetchone()["n"]
    return n >= cfg.canal_limite_hora


def _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, estado, client_id=None, ticket_id=None, detalle=None) -> int:
    cur = conn.execute(
        "INSERT INTO entrantes(canal, remitente, nombre, asunto, cuerpo, recibido_at, client_id, estado, ticket_id, msg_id, detalle) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (canal, remitente, nombre, asunto, (cuerpo or "")[:MAX_CUERPO], ahora(), client_id, estado, ticket_id, msg_id, detalle))
    conn.commit()
    return cur.lastrowid


def _contenido_ticket(canal: str, remitente: str, nombre: str | None, cuerpo: str) -> str:
    quien = f"{nombre} ({remitente})" if nombre else remitente
    via = "WhatsApp" if canal == "whatsapp" else "correo"
    return f"{cuerpo}\n\n— Recibido por {via} de {quien}"


def _ticket_valido_para(conn, ticket_id: int, client_id: int) -> bool:
    r = conn.execute("SELECT client_id FROM ticket_origen WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return bool(r and r["client_id"] == client_id)


def _crear_o_seguir(cfg, conn, cli: dict, canal, remitente, nombre, asunto, cuerpo, hilo: int | None) -> tuple[str, int]:
    """Devuelve («ticket»|«seguimiento», ticket_id). Levanta AdapterError si GLPI falla."""
    contenido = _contenido_ticket(canal, remitente, nombre, cuerpo)
    ticket_id = None
    if hilo and _ticket_valido_para(conn, hilo, cli["id"]):
        ticket_id = hilo
    elif canal == "whatsapp":
        c = conn.execute("SELECT ticket_id, last_at FROM conversaciones WHERE canal = ? AND remitente = ?",
                         (canal, remitente)).fetchone()
        if c:
            lim = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
            if c["last_at"] >= lim and _ticket_valido_para(conn, c["ticket_id"], cli["id"]):
                ticket_id = c["ticket_id"]
    if ticket_id:
        tickets_svc.seguimiento(cfg, ticket_id, contenido)
        accion = "seguimiento"
    else:
        entidad = cli.get("glpi_entity_id")
        if not entidad:
            raise ServiceError("El cliente todavía no tiene entidad en GLPI (reintentá el aprovisionamiento).")
        titulo = _titulo(asunto, cuerpo)
        ticket_id = tickets_svc.crear(cfg, titulo, contenido, int(entidad), 3)
        conn.execute("INSERT OR REPLACE INTO ticket_origen(ticket_id, client_id, canal, remitente, created_at) VALUES (?,?,?,?,?)",
                     (ticket_id, cli["id"], canal, remitente, ahora()))
        accion = "ticket"
        from . import push as push_svc
        push_svc.avisar(conn, cfg, f"🎫 Nuevo ticket · {cli['nombre']}", f"#{ticket_id} {titulo} (por {canal})", f"/tickets/{ticket_id}",
                        cliente_id=cli["id"], tag=f"ticket-{ticket_id}")
    if canal == "whatsapp":
        conn.execute("INSERT OR REPLACE INTO conversaciones(canal, remitente, ticket_id, last_at) VALUES (?,?,?,?)",
                     (canal, remitente, ticket_id, ahora()))
    conn.commit()
    return accion, ticket_id


def _titulo(asunto: str | None, cuerpo: str) -> str:
    t = _RE_HILO.sub("", _RE_PREFIJO.sub("", asunto or "")).strip()
    if not t:
        t = " ".join((cuerpo or "").split())[:80] or "Consulta de soporte"
    return t[:120]


def registrar_entrante(cfg, conn: sqlite3.Connection, *, canal: str, remitente: str, nombre: str | None = None,
                       asunto: str | None = None, cuerpo: str = "", msg_id: str | None = None,
                       sospechoso: bool = False, responder: bool = True) -> dict:
    """Procesa un mensaje entrante. Devuelve {«estado», «ticket_id», «entrante_id», «client_id»}."""
    cuerpo = (cuerpo or "").strip()
    if msg_id and conn.execute("SELECT id FROM entrantes WHERE canal = ? AND msg_id = ?", (canal, msg_id)).fetchone():
        return {"estado": "duplicado", "ticket_id": None, "entrante_id": None, "client_id": None}
    if _limite_excedido(conn, cfg, canal, remitente):
        eid = _guardar(conn, canal, remitente, nombre, asunto, "", msg_id, "ignorado", detalle="Límite de mensajes por hora")
        return {"estado": "ignorado", "ticket_id": None, "entrante_id": eid, "client_id": None}
    client_id, como = identificar_cliente(conn, canal, remitente)
    if not client_id:
        eid = _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, "sin_cliente", detalle="Remitente no asociado a ningún cliente")
        if responder:
            _confirmar_desconocido(cfg, conn, canal, remitente)
        return {"estado": "sin_cliente", "ticket_id": None, "entrante_id": eid, "client_id": None}
    if sospechoso:
        eid = _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, "revisar", client_id,
                       detalle="El correo no pasó la verificación SPF/DKIM/DMARC: puede ser suplantación")
        return {"estado": "revisar", "ticket_id": None, "entrante_id": eid, "client_id": client_id}
    cli = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    cli = dict(cli) if cli else None
    if not cli:
        eid = _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, "sin_cliente", detalle="Cliente inexistente")
        return {"estado": "sin_cliente", "ticket_id": None, "entrante_id": eid, "client_id": None}
    hilo = int(m.group(1)) if (m := _RE_HILO.search(asunto or "")) else None
    try:
        accion, tid = _crear_o_seguir(cfg, conn, cli, canal, remitente, nombre, asunto, cuerpo, hilo)
    except Exception as e:  # noqa: BLE001 — GLPI caído, cliente sin entidad…: queda en la bandeja para reintentar
        log.warning("No se pudo crear el ticket del entrante (%s %s): %s", canal, remitente, e)
        det = getattr(e, "detalle", None) or getattr(e, "mensaje", None) or str(e)
        eid = _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, "error", client_id, detalle=str(det)[:300])
        return {"estado": "error", "ticket_id": None, "entrante_id": eid, "client_id": client_id}
    eid = _guardar(conn, canal, remitente, nombre, asunto, cuerpo, msg_id, accion, client_id, tid, detalle=f"Cliente por {como}")
    audit.registrar(conn, f"canal:{canal}", f"canal.{accion}",
                    {"ticket_id": tid, "cliente": cli["nombre"], "remitente": remitente, "por": como})
    if responder:
        _confirmar(cfg, canal, remitente, asunto, tid, accion)
    return {"estado": accion, "ticket_id": tid, "entrante_id": eid, "client_id": client_id}


def asignar_entrante(cfg, conn: sqlite3.Connection, entrante_id: int, client_id: int, actor: str,
                     recordar: bool = False) -> dict:
    """El PM asocia a mano un entrante (sin cliente / a revisar / con error) a un cliente y se crea el ticket."""
    e = conn.execute("SELECT * FROM entrantes WHERE id = ?", (entrante_id,)).fetchone()
    if not e or e["estado"] not in ("sin_cliente", "revisar", "error"):
        raise ServiceError("Ese mensaje ya fue atendido.")
    cli = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not cli:
        raise ServiceError("Cliente inexistente.")
    cli = dict(cli)
    if recordar:
        try:
            if e["canal"] == "whatsapp":
                agregar_contacto(conn, client_id, e["nombre"] or "", "", e["remitente"], actor)
            else:
                agregar_contacto(conn, client_id, e["nombre"] or "", e["remitente"], "", actor)
        except ServiceError:
            pass                                   # ya estaba cargado
    hilo = int(m.group(1)) if (m := _RE_HILO.search(e["asunto"] or "")) else None
    try:
        accion, tid = _crear_o_seguir(cfg, conn, cli, e["canal"], e["remitente"], e["nombre"], e["asunto"], e["cuerpo"] or "", hilo)
    except Exception as ex:  # noqa: BLE001
        det = getattr(ex, "detalle", None) or getattr(ex, "mensaje", None) or str(ex)
        raise ServiceError(f"No se pudo crear el ticket: {det}") from ex
    conn.execute("UPDATE entrantes SET estado = ?, client_id = ?, ticket_id = ?, detalle = ? WHERE id = ?",
                 (accion, client_id, tid, f"Asignado por {actor}", entrante_id))
    conn.commit()
    audit.registrar(conn, actor, "canal.entrante_asignado",
                    {"entrante_id": entrante_id, "ticket_id": tid, "cliente": cli["nombre"]})
    _confirmar(cfg, e["canal"], e["remitente"], e["asunto"], tid, accion)
    return {"ticket_id": tid, "accion": accion}


def descartar_entrante(conn: sqlite3.Connection, entrante_id: int, actor: str) -> None:
    conn.execute("UPDATE entrantes SET estado = 'descartado', detalle = ? WHERE id = ? AND estado IN ('sin_cliente','revisar','error')",
                 (f"Descartado por {actor}", entrante_id))
    conn.commit()
    audit.registrar(conn, actor, "canal.entrante_descartado", {"entrante_id": entrante_id})


def pendientes(conn) -> int:
    return conn.execute("SELECT COUNT(*) n FROM entrantes WHERE estado IN ('sin_cliente','revisar','error')").fetchone()["n"]


def origenes(conn) -> dict[int, str]:
    return {r["ticket_id"]: r["canal"] for r in conn.execute("SELECT ticket_id, canal FROM ticket_origen")}


def responder_solicitante(cfg, conn, ticket_id: int, texto: str, titulo: str = "") -> str:
    """Manda la respuesta de un técnico al solicitante por el canal por el que escribió (WhatsApp o correo).
    Devuelve una descripción de lo hecho; levanta ServiceError si no se puede."""
    o = conn.execute("SELECT canal, remitente FROM ticket_origen WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if not o:
        raise ServiceError("Este ticket no vino por WhatsApp ni por correo: no hay a quién enviarle la respuesta.")
    if o["canal"] == "whatsapp":
        if not cfg.wa_api_url:
            raise ServiceError("Falta la URL de la API de OpenWA (pantalla Canales) para responder por WhatsApp.")
        try:
            responder_whatsapp(cfg, o["remitente"], texto)
        except Exception as e:  # noqa: BLE001
            raise ServiceError(f"No se pudo enviar por WhatsApp: {e}") from e
        return "WhatsApp"
    if not correo_svc.configurado(cfg):
        raise ServiceError("El envío de correo (SMTP) no está configurado.")
    base = _RE_HILO.sub("", _RE_PREFIJO.sub("", titulo or "")).strip() or "Consulta de soporte"
    try:
        correo_svc.enviar(cfg, o["remitente"], f"Re: [#{ticket_id}] {base}",
                          f"{texto}\n\n—\nPara responder, contestá este correo sin cambiar el asunto.")
    except Exception as e:  # noqa: BLE001
        raise ServiceError(f"No se pudo enviar el correo: {getattr(e, 'mensaje', None) or e}") from e
    return "correo"


# --------------------------------------------------------------------------- respuestas al remitente

def _confirmar(cfg, canal, remitente, asunto, ticket_id, accion) -> None:
    if accion == "seguimiento":
        return                                     # no contestar cada mensaje de una conversación
    texto = (f"Recibimos tu consulta y abrimos el caso #{ticket_id}. "
             "Un técnico la va a tomar a la brevedad. Si querés agregar algo, respondé por acá.")
    try:
        if canal == "whatsapp":
            responder_whatsapp(cfg, remitente, texto)
        elif correo_svc.configurado(cfg):
            base = _RE_HILO.sub("", _RE_PREFIJO.sub("", asunto or "")).strip() or "Consulta de soporte"
            correo_svc.enviar(cfg, remitente, f"[#{ticket_id}] {base}",
                              f"{texto}\n\nPara sumar información, respondé este correo sin cambiar el asunto.\n")
    except Exception as e:  # noqa: BLE001 — confirmar es un extra
        log.info("No se pudo confirmar al remitente %s: %s", remitente, e)


def _confirmar_desconocido(cfg, conn, canal, remitente) -> None:
    """A un número desconocido se le contesta UNA vez por hora (por correo no: sería rebote a terceros)."""
    if canal != "whatsapp":
        return
    desde = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    n = conn.execute("SELECT COUNT(*) n FROM entrantes WHERE canal = 'whatsapp' AND remitente = ? AND estado = 'sin_cliente' AND recibido_at >= ?",
                     (remitente, desde)).fetchone()["n"]
    if n > 1:
        return
    try:
        responder_whatsapp(cfg, remitente,
                           "Hola 👋 recibimos tu mensaje, pero este número todavía no está asociado a ninguna empresa. "
                           "Un integrante de soporte lo va a revisar y te contacta.")
    except Exception as e:  # noqa: BLE001
        log.info("No se pudo contestar al número desconocido: %s", e)


def responder_whatsapp(cfg, numero: str, texto: str) -> None:
    """Respuesta por la API de OpenWA v5: POST /api/messages/sendText {to, content} con X-API-Key.

    Opcional (sin ITIER_WA_API_URL no hace nada) y de mejor esfuerzo: nunca frena la creación del ticket.
    """
    if not cfg.wa_api_url or cfg.demo:
        return
    headers = {"X-API-Key": cfg.wa_api_key, "api_key": cfg.wa_api_key} if cfg.wa_api_key else {}
    destino = f"{numero[4:]}@lid" if numero.startswith("lid:") else f"{numero}@c.us"
    r = httpx.post(f"{cfg.wa_api_url}/api/messages/sendText", headers=headers, timeout=10,
                   json={"to": destino, "content": texto})
    r.raise_for_status()


# --------------------------------------------------------------------------- WhatsApp (webhook de OpenWA)

_RE_PN = re.compile(r"^(\d{8,15})@(c\.us|s\.whatsapp\.net)$")


def _numero_real(m: dict) -> str | None:
    """Busca el teléfono del remitente cuando el chat viene identificado por un @lid."""
    candidatos = [m.get(k) for k in ("senderPn", "participantPn", "phoneNumber", "pn", "authorPn")]
    for k in ("sender", "contact", "chat"):
        sub = m.get(k)
        if isinstance(sub, dict):
            candidatos += [sub.get(x) for x in ("id", "phoneNumber", "number", "pn", "user", "_serialized")]
            if isinstance(sub.get("contact"), dict):
                candidatos += [sub["contact"].get(x) for x in ("id", "phoneNumber", "number")]
    for c in candidatos:
        if isinstance(c, dict):
            c = c.get("_serialized") or c.get("user")
        if not isinstance(c, str):
            continue
        mm = _RE_PN.match(c.strip())
        if mm:
            return normalizar_whatsapp(mm.group(1))
        if re.fullmatch(r"\+?\d{10,15}", c.strip()):
            return normalizar_whatsapp(c)
    return None


def parsear_whatsapp(body: dict) -> dict | None:
    """Del sobre del webhook de OpenWA al mensaje que nos interesa; None si hay que ignorarlo."""
    if not isinstance(body, dict):
        return None
    evento = str(body.get("event") or "").lower()
    if evento and "message" not in evento:
        return None
    m = body.get("data") if isinstance(body.get("data"), dict) else None
    m = m or (body.get("payload") if isinstance(body.get("payload"), dict) else None)
    if m is None and "from" in body:
        m = body
    if isinstance(m, dict) and isinstance(m.get("message"), dict):   # v5: {"payload": {"message": {...}}}
        m = m["message"]
    if not isinstance(m, dict):
        return None
    if m.get("isGroupMsg") or m.get("fromMe") or m.get("isStatus"):
        return None
    origen = str(m.get("from") or m.get("chatId") or "")
    if not origen or origen.endswith("@g.us") or "broadcast" in origen:
        return None
    if str(m.get("type") or "chat").lower() not in ("chat", "text", "conversation"):
        return None
    texto = str(m.get("body") or m.get("text") or m.get("content") or "").strip()
    numero = normalizar_whatsapp(origen)
    if numero and numero.startswith("lid:"):
        # El número real a veces viene en otro campo del mensaje (nunca en `to`: ese es el de soporte).
        pn = _numero_real(m)
        if pn:
            numero = pn
    if not texto or not numero:
        return None
    sender = m.get("sender") if isinstance(m.get("sender"), dict) else {}
    nombre = m.get("notifyName") or sender.get("pushname") or sender.get("formattedName") or None
    mid = m.get("id")
    if isinstance(mid, dict):
        mid = mid.get("_serialized") or mid.get("id")
    return {"remitente": numero, "nombre": (str(nombre)[:80] if nombre else None),
            "cuerpo": texto[:MAX_CUERPO], "msg_id": (str(mid)[:200] if mid else None)}


def procesar_whatsapp(cfg, conn, body: dict) -> dict:
    msg = parsear_whatsapp(body)
    if not msg:
        return {"estado": "ignorado"}
    if msg["remitente"].startswith("lid:"):
        # Sin número visible: se deja constancia (sin el texto del mensaje) para poder ajustar el parser.
        m = body.get("payload") or body.get("data") or body
        m = m.get("message", m) if isinstance(m, dict) else {}
        log.info("WhatsApp sin número (ID interno): campos del mensaje=%s",
                 {k: (v if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) < 60 and k not in ("body", "text", "content", "caption")) else type(v).__name__)
                  for k, v in (m.items() if isinstance(m, dict) else [])})
    return registrar_entrante(cfg, conn, canal="whatsapp", asunto=None, **msg)


# --------------------------------------------------------------------------- correo

def _a_texto(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"[ \t]+", " ", html.unescape(h))


def _sin_cita(texto: str) -> str:
    salida = []
    for linea in texto.replace("\r\n", "\n").split("\n"):
        if _RE_CITA.match(linea.strip()) and salida:
            break
        salida.append(linea)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(salida)).strip()


def parsear_email(raw: bytes) -> dict:
    msg = email_mod.message_from_bytes(raw, policy=policy.default)
    nombre, addr = parseaddr(str(msg.get("From", "")))
    addr = normalizar_email(addr) or ""
    auto = (
        str(msg.get("Auto-Submitted", "no")).lower() != "no"
        or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "list", "auto_reply")
        or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond") or msg.get("List-Id"))
        or any(p in addr for p in _REMITENTES_AUTOMATICOS)
    )
    auth = " ".join(str(h).lower() for h in msg.get_all("Authentication-Results", []))
    falla = any(f"{k}=fail" in auth for k in ("dkim", "spf", "dmarc"))
    cuerpo = ""
    parte = msg.get_body(preferencelist=("plain", "html"))
    if parte is not None:
        try:
            cuerpo = parte.get_content()
        except Exception:  # noqa: BLE001 — charset roto
            cuerpo = ""
        if parte.get_content_type() == "text/html":
            cuerpo = _a_texto(cuerpo)
    return {
        "remitente": addr, "nombre": (nombre or "").strip()[:80] or None,
        "asunto": " ".join(str(msg.get("Subject", "")).split())[:200],
        "cuerpo": _sin_cita(cuerpo)[:MAX_CUERPO],
        "msg_id": (str(msg.get("Message-ID", "")).strip()[:200] or None),
        "automatico": auto, "sospechoso": falla,
    }


def procesar_email(cfg, conn, raw: bytes) -> dict:
    p = parsear_email(raw)
    if not p["remitente"] or p["automatico"]:
        return {"estado": "ignorado"}
    propios = {x.lower() for x in (cfg.imap_user, cfg.soporte_email, normalizar_email(parseaddr(cfg.smtp_from or "")[1])) if x}
    if p["remitente"] in propios:
        return {"estado": "ignorado"}              # nunca procesar lo que mandamos nosotros (evita bucles)
    if not p["cuerpo"] and not p["asunto"]:
        return {"estado": "ignorado"}
    return registrar_entrante(cfg, conn, canal="email", remitente=p["remitente"], nombre=p["nombre"],
                              asunto=p["asunto"], cuerpo=p["cuerpo"] or p["asunto"], msg_id=p["msg_id"],
                              sospechoso=p["sospechoso"])


def _token_imap_ms(cfg) -> str:
    """Token de aplicación (client credentials) para IMAP de Exchange Online. Levanta ServiceError con el motivo."""
    try:
        r = httpx.post(f"https://login.microsoftonline.com/{cfg.imap_ms_tenant}/oauth2/v2.0/token", timeout=20, data={
            "grant_type": "client_credentials", "client_id": cfg.imap_ms_client_id, "client_secret": cfg.imap_ms_secret,
            "scope": "https://outlook.office365.com/.default"})
    except httpx.HTTPError as e:
        raise ServiceError(f"No se pudo hablar con Microsoft para obtener el token ({e.__class__.__name__}).") from e
    if r.status_code != 200:
        try:
            det = (r.json().get("error_description") or r.json().get("error") or "").splitlines()[0][:200]
        except ValueError:
            det = r.text[:200]
        if "AADSTS7000215" in det or "AADSTS7000222" in det:
            det = "El secreto de la aplicación es inválido o venció. " + det
        elif "AADSTS700016" in det:
            det = "La aplicación no existe en ese tenant (revisá el ID de aplicación y el ID de directorio). " + det
        raise ServiceError(f"Microsoft rechazó la autenticación de la aplicación: {det}")
    return r.json()["access_token"]


def _conectar_imap(cfg):
    if cfg.imap_tls == "ssl":
        imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, timeout=30)
    else:
        imap = imaplib.IMAP4(cfg.imap_host, cfg.imap_port, timeout=30)
        if cfg.imap_tls == "starttls":
            imap.starttls()
    if cfg.imap_auth == "oauth2_ms":
        # Microsoft 365 ya no admite IMAP con contraseña: autenticación de aplicación (XOAUTH2) sobre el buzón.
        token = _token_imap_ms(cfg)
        imap.authenticate("XOAUTH2", lambda _: f"user={cfg.imap_user}\x01auth=Bearer {token}\x01\x01".encode())
    else:
        imap.login(cfg.imap_user, cfg.imap_password)
    return imap


def sondear_buzon(cfg, conectar=None) -> int:
    """Una pasada: lee los correos no leídos, los procesa y los marca leídos. Devuelve cuántos vio."""
    imap = (conectar or _conectar_imap)(cfg)
    conn = db_mod.connect(cfg.db_path)
    vistos = 0
    try:
        imap.select(cfg.imap_folder)
        typ, datos = imap.search(None, "UNSEEN")
        for num in (datos[0].split() if typ == "OK" and datos and datos[0] else []):
            typ, parte = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not parte or not isinstance(parte[0], tuple):
                continue
            try:
                procesar_email(cfg, conn, parte[0][1])
            except Exception:  # noqa: BLE001 — un correo roto no frena el resto
                log.exception("Error procesando un correo")
            imap.store(num, "+FLAGS", "\\Seen")
            vistos += 1
    finally:
        conn.close()
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return vistos


def probar_imap(cfg) -> str:
    """Prueba usuario/clave y carpeta; devuelve un texto para mostrar o levanta ServiceError."""
    if not cfg.imap_configurado:
        raise ServiceError("Completá servidor, usuario y " + ("los datos de la aplicación de Microsoft 365." if cfg.imap_auth == "oauth2_ms" else "contraseña."))
    try:
        imap = _conectar_imap(cfg)
    except imaplib.IMAP4.error as e:
        if cfg.imap_auth == "oauth2_ms":
            raise ServiceError("Exchange rechazó el acceso de la aplicación al buzón. Revisá: el permiso IMAP.AccessAsApp con consentimiento, el "
                               "principal de servicio en Exchange y el permiso de acceso total (FullAccess) sobre el buzón. " + str(e)[:140]) from e
        raise ServiceError("El servidor rechazó el usuario o la contraseña (¿hace falta una contraseña de aplicación?): "
                           + str(e)[:120]) from e
    except (OSError, TimeoutError) as e:
        raise ServiceError(f"No se pudo conectar a {cfg.imap_host}:{cfg.imap_port} ({e.__class__.__name__}). "
                           "Revisá servidor, puerto y cifrado.") from e
    try:
        typ, d = imap.select(cfg.imap_folder, readonly=True)
        if typ != "OK":
            raise ServiceError(f"La carpeta «{cfg.imap_folder}» no existe en el buzón.")
        typ, n = imap.search(None, "UNSEEN")
        sin_leer = len(n[0].split()) if typ == "OK" and n and n[0] else 0
        return f"Conexión correcta. {sin_leer} correo(s) sin leer en {cfg.imap_folder}."
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


def iniciar_poller(cfg) -> threading.Event | None:
    """Hilo que mira el buzón de soporte. La config se relee en cada vuelta: lo que se cargue en
    la pantalla de Canales aplica sin reiniciar. Sin buzón configurado, la vuelta no hace nada."""
    if cfg.demo:
        return None
    from . import ajustes
    parar = threading.Event()

    def _loop():
        while not parar.is_set():
            espera = cfg.imap_poll_s
            try:
                conn = db_mod.connect(cfg.db_path)
                try:
                    ef = ajustes.efectiva(conn, cfg)
                finally:
                    conn.close()
                espera = ef.imap_poll_s
                if ef.imap_configurado:
                    sondear_buzon(ef)
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo leer el buzón de soporte: %s", e)
            parar.wait(espera)

    threading.Thread(target=_loop, name="itier-imap", daemon=True).start()
    return parar
