"""Auditoría: bitácora append-only y verificación de la cadena de hash."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import audit as audit_mod
from ..deps import get_db, render, require_user

router = APIRouter()


@router.get("/auditoria", response_class=HTMLResponse)
def ver(request: Request, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    entradas = audit_mod.listar(conn, limit=300)
    ok, motivo = audit_mod.verificar(conn)
    return render(request, "auditoria.html", entradas=entradas, cadena_ok=ok, cadena_motivo=motivo)
