"""Manual de operaciones de la consola según el rol (el mismo contenido que recibe el usuario por correo)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..deps import render, require_user
from ..services import manual, rbac

router = APIRouter()


@router.get("/manual", response_class=HTMLResponse)
def ver(request: Request, rol: str = "", user: dict = Depends(require_user)):
    # El administrador puede leer el manual de cualquier rol (para capacitar); el resto, el suyo.
    elegido = rol if (user.get("rol") == "admin" and rol in rbac.ROLES) else user.get("rol")
    return render(request, "manual.html", m=manual.manual_para(elegido), roles=rbac.ROLES,
                  es_admin=user.get("rol") == "admin", elegido=elegido)
