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
    return {"api_key": cfg.wa_api_key, "X-API-Key": cfg.wa_api_key} if cfg.wa_api_key else {}


def _a_dataurl(r: httpx.Response) -> str | None:
    ct = r.headers.get("content-type", "")
    if ct.startswith("image/"):
        return f"data:{ct};base64," + base64.b64encode(r.content).decode()
    try:
        j = r.json()
    except ValueError:
        return None
    cand = j.get("response") if isinstance(j, dict) else j
    cand = cand if isinstance(cand, str) else (j.get("qr") or j.get("qrcode") or j.get("data")) if isinstance(j, dict) else None
    if isinstance(cand, str) and cand.startswith("data:image"):
        return cand
    if isinstance(cand, str) and re.fullmatch(r"[A-Za-z0-9+/=\s]{200,}", cand):
        return "data:image/png;base64," + cand.replace("\n", "")
    return None


def estado(cfg) -> dict:
    """{«estado»: conectado|esperando_qr|sin_conexion|no_configurado, «qr»: dataurl|None, «detalle»: str}."""
    if cfg.demo:
        return {"estado": "esperando_qr", "qr": _DEMO_QR, "detalle": "Modo demo: este QR es de ejemplo."}
    if not cfg.wa_api_url:
        return {"estado": "no_configurado", "qr": None, "detalle": "Falta la URL de la API de OpenWA."}
    h = _headers(cfg)
    base = cfg.wa_api_url
    try:
        with httpx.Client(timeout=8) as c:
            conectado = None
            for met, ruta in (("POST", "/getConnectionState"), ("GET", "/getConnectionState")):
                try:
                    r = c.request(met, base + ruta, headers=h, json={"args": {}} if met == "POST" else None)
                except httpx.HTTPError:
                    raise
                if r.status_code == 200:
                    txt = r.text.upper()
                    conectado = "CONNECTED" in txt and "DISCONNECTED" not in txt
                    break
            if conectado:
                return {"estado": "conectado", "qr": None, "detalle": "El número está vinculado y recibiendo mensajes."}
            for met, ruta in (("GET", "/qr"), ("POST", "/getQrCode"), ("GET", "/getQrCode"), ("GET", "/qr.png")):
                r = c.request(met, base + ruta, headers=h, json={"args": {}} if met == "POST" else None)
                if r.status_code == 200:
                    qr = _a_dataurl(r)
                    if qr:
                        return {"estado": "esperando_qr", "qr": qr, "detalle": "Escaneá el código con WhatsApp → Dispositivos vinculados."}
            return {"estado": "esperando_qr" if conectado is False else "sin_conexion", "qr": None,
                    "detalle": "OpenWA responde pero no entregó el QR por API. Miralo en los logs: "
                               "docker compose logs -f openwa (o abrí su /api-docs por túnel SSH)."}
    except httpx.HTTPError as e:
        return {"estado": "sin_conexion", "qr": None,
                "detalle": f"No se pudo hablar con OpenWA ({e.__class__.__name__}). ¿Está levantado el servicio «openwa»?"}
