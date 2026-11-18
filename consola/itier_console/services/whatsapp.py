"""Estado de la sesión de WhatsApp (OpenWA) y su QR, para vincular el número desde la consola.

OpenWA no tiene una API de QR estable entre versiones: se prueban las rutas conocidas y se
devuelve lo primero que sirva. Si ninguna responde, la pantalla lo dice y sugiere mirar los logs
del contenedor (`docker compose logs openwa`), donde OpenWA también imprime el QR.
"""

from __future__ import annotations

import base64
import re

import httpx

_DEMO_QR = ("data:image/svg+xml;base64," + base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" fill="#fff"/>'
    b'<g fill="#111"><rect x="10" y="10" width="32" height="32"/><rect x="78" y="10" width="32" height="32"/>'
    b'<rect x="10" y="78" width="32" height="32"/><rect x="52" y="52" width="16" height="16"/>'
    b'<rect x="78" y="78" width="12" height="12"/><rect x="98" y="90" width="12" height="20"/></g>'
    b'<text x="60" y="118" font-size="7" text-anchor="middle" fill="#888">QR de ejemplo (demo)</text></svg>').decode())


def _headers(cfg) -> dict:
    return {"X-API-Key": cfg.wa_api_key, "api_key": cfg.wa_api_key} if cfg.wa_api_key else {}


def _qr_a_imagen(qr) -> str | None:
    """El QR que informa /health → imagen para mostrar. Acepta una imagen ya hecha (data URL / base64)
    o el texto crudo del código (que se dibuja acá)."""
    if isinstance(qr, dict):
        for k in ("dataUrl", "dataURL", "image", "png", "url", "qr", "code", "value", "text", "raw", "data"):
            if qr.get(k):
                r = _qr_a_imagen(qr[k])
                if r:
                    return r
        return None
    if not isinstance(qr, str) or not qr.strip():
        return None
    qr = qr.strip()
    if qr.startswith("data:image"):
        return qr
    if re.fullmatch(r"[A-Za-z0-9+/=\s]{400,}", qr):          # PNG en base64 sin encabezado
        return "data:image/png;base64," + re.sub(r"\s", "", qr)
    try:
        import segno
        return segno.make(qr, error="m").svg_data_uri(scale=6, border=2)
    except Exception:  # noqa: BLE001
        return None


def estado(cfg) -> dict:
    """{«estado»: conectado|esperando_qr|iniciando|sin_conexion|no_configurado, «qr»: imagen|None, «detalle»}.

    Sale de GET /health de OpenWA v5 (campos `connected`, `session.ready`, `session.state`, `qr`).
    """
    if cfg.demo:
        return {"estado": "esperando_qr", "qr": _DEMO_QR, "detalle": "Modo demo: este QR es de ejemplo."}
    if not cfg.wa_api_url:
        return {"estado": "no_configurado", "qr": None, "detalle": "Falta la URL de la API de OpenWA."}
    try:
        with httpx.Client(timeout=8) as c:
            r = c.get(cfg.wa_api_url + "/health", headers=_headers(cfg))
        r.raise_for_status()
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"estado": "sin_conexion", "qr": None,
                "detalle": f"No se pudo hablar con OpenWA ({e.__class__.__name__}). ¿Está levantado el servicio «openwa»? "
                           "Revisá: docker compose logs --tail=50 openwa"}
    ses = d.get("session") if isinstance(d.get("session"), dict) else {}
    if d.get("connected") and ses.get("ready", True):
        return {"estado": "conectado", "qr": None, "detalle": "El número está vinculado y recibiendo mensajes."}
    qr = _qr_a_imagen(d.get("qr"))
    if qr:
        return {"estado": "esperando_qr", "qr": qr,
                "detalle": "Escaneá el código con WhatsApp → Dispositivos vinculados. Se renueva solo cada pocos segundos."}
    st = str(ses.get("state") or "").upper()
    if st in ("STARTING", "LAUNCHING", "INITIALIZING", ""):
        return {"estado": "iniciando", "qr": None,
                "detalle": "OpenWA está arrancando el navegador y cargando WhatsApp Web: puede tardar un minuto. Esperá…"}
    return {"estado": "esperando_qr", "qr": None,
            "detalle": f"Sesión en estado «{st or 'desconocido'}» sin QR disponible. Si no cambia, miralo en: docker compose logs -f openwa"}
