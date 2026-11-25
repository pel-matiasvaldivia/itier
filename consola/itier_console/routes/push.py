"""Suscripción a los avisos push (los llama la pantalla Guardia con fetch).

  GET  /push/clave       clave pública VAPID
  POST /push/suscribir   {endpoint, keys:{p256dh, auth}}   (cabecera X-CSRF)
  POST /push/baja        {endpoint}
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import security
from ..deps import get_db, require_permiso
from ..services import push as svc

router = APIRouter()


def _csrf(request: Request) -> bool:
    return security.verificar_csrf(request.session, request.headers.get("x-csrf", ""))


@router.get("/push/clave")
def clave(request: Request, user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    try:
        ef = svc.asegurar_claves(conn, request.app.state.cfg)
        return {"clave": ef.vapid_pub}
    except Exception:  # noqa: BLE001
        return {"clave": None}


@router.post("/push/suscribir")
async def suscribir(request: Request, user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    if not _csrf(request):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
    try:
        svc.suscribir(conn, user["uid"], await request.json(), request.headers.get("user-agent", ""))
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "suscripción inválida"}, status_code=400)
    return {"ok": True}


@router.post("/push/baja")
async def baja(request: Request, user: dict = Depends(require_permiso("tickets")), conn: sqlite3.Connection = Depends(get_db)):
    if not _csrf(request):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
    try:
        svc.baja(conn, user["uid"], (await request.json()).get("endpoint", ""))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"ok": False}, status_code=400)
    return {"ok": True}
