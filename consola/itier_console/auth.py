"""Rutas de autenticación: login (1er factor) → TOTP (2º factor) → sesión.

Flujo:
  1. POST /login            — usuario + contraseña (Argon2id, con lockout).
  2a. Si el usuario no tiene TOTP confirmado  → /login/2fa/enrolar (QR + verificación).
  2b. Si ya lo tiene                          → /login/2fa (pide el código).
  3. Sesión establecida. Si must_change_pw    → se fuerza /cambiar-clave.

Mientras el 1er factor está validado pero el 2º no, la sesión está en un estado
intermedio ("totp"/"enroll") que NO da acceso a las rutas protegidas.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import audit, security
from .deps import flash, get_db, render, sesion_usuario

router = APIRouter()


def _finalizar_login(request: Request, user: sqlite3.Row) -> None:
    request.session.clear()
    request.session["uid"] = int(user["id"])
    request.session["user"] = user["username"]
    request.session["is_admin"] = bool(user["is_admin"])
    request.session["client_id"] = user["client_id"]
    request.session["must_change"] = bool(user["must_change_pw"])
    request.session["stage"] = "authed"


def _destino_post_login(request: Request) -> str:
    if request.session.get("must_change"):
        return "/cambiar-clave"
    return "/portal" if request.session.get("client_id") else "/"


# ── Login (primer factor) ───────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if sesion_usuario(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", user=None)


@router.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/login", status_code=303)

    cfg = request.app.state.cfg
    res = security.autenticar(conn, cfg.lockout_threshold, cfg.lockout_window_s, username.strip(), password)
    if not res.ok:
        if res.bloqueado:
            flash(request, f"Cuenta bloqueada temporalmente. Reintentá en ~{res.restante_s // 60 + 1} min.", "error")
        else:
            flash(request, "Usuario o contraseña incorrectos.", "error")
        audit.registrar(conn, username.strip() or "(anónimo)", "login.fallo", {"motivo": res.motivo})
        return RedirectResponse("/login", status_code=303)

    user = res.user
    # Estado intermedio: 1er factor ok, falta el 2º.
    request.session.clear()
    request.session["pending_uid"] = int(user["id"])
    request.session["pending_user"] = user["username"]
    if user["totp_secret"] and user["totp_confirmed"]:
        request.session["stage"] = "totp"
        return RedirectResponse("/login/2fa", status_code=303)
    request.session["stage"] = "enroll"
    return RedirectResponse("/login/2fa/enrolar", status_code=303)


# ── Segundo factor: verificación ────────────────────────────────────────────

@router.get("/login/2fa", response_class=HTMLResponse)
def totp_form(request: Request):
    if request.session.get("stage") != "totp":
        return RedirectResponse("/login", status_code=303)
    return render(request, "2fa_verify.html", user=None, pending_user=request.session.get("pending_user"))


@router.post("/login/2fa")
def totp_post(
    request: Request,
    code: str = Form(...),
    csrf: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if request.session.get("stage") != "totp":
        return RedirectResponse("/login", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/login", status_code=303)

    uid = request.session.get("pending_uid")
    user = security.obtener_usuario_id(conn, uid)
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    if security.verificar_totp(user["totp_secret"], code):
        audit.registrar(conn, user["username"], "login.ok", {"metodo": "totp"})
        _finalizar_login(request, user)
        return RedirectResponse(_destino_post_login(request), status_code=303)

    flash(request, "Código inválido. Verificá la hora del dispositivo.", "error")
    audit.registrar(conn, user["username"], "login.2fa_fallo", {})
    return RedirectResponse("/login/2fa", status_code=303)


# ── Segundo factor: enrolamiento (primer uso) ───────────────────────────────

@router.get("/login/2fa/enrolar", response_class=HTMLResponse)
def enrolar_form(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if request.session.get("stage") != "enroll":
        return RedirectResponse("/login", status_code=303)
    uid = request.session.get("pending_uid")
    user = security.obtener_usuario_id(conn, uid)
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # Reutiliza el secreto sin confirmar si ya existe; si no, genera y persiste.
    secret = user["totp_secret"]
    if not secret or user["totp_confirmed"]:
        secret = security.nuevo_secreto_totp()
        security.set_totp_secret(conn, int(user["id"]), secret)

    cfg = request.app.state.cfg
    uri = security.uri_totp(secret, user["username"], cfg.totp_issuer)
    return render(
        request, "2fa_enroll.html", user=None,
        pending_user=user["username"], secret=secret, qr_svg=security.qr_svg(uri),
    )


@router.post("/login/2fa/enrolar")
def enrolar_post(
    request: Request,
    code: str = Form(...),
    csrf: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if request.session.get("stage") != "enroll":
        return RedirectResponse("/login", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/login", status_code=303)

    uid = request.session.get("pending_uid")
    user = security.obtener_usuario_id(conn, uid)
    if not user or not user["totp_secret"]:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    if security.verificar_totp(user["totp_secret"], code):
        security.confirmar_totp(conn, int(user["id"]))
        user = security.obtener_usuario_id(conn, uid)
        audit.registrar(conn, user["username"], "2fa.enrolado", {})
        _finalizar_login(request, user)
        flash(request, "Segundo factor activado.", "ok")
        return RedirectResponse(_destino_post_login(request), status_code=303)

    flash(request, "El código no coincide. Escaneá el QR y reintentá.", "error")
    return RedirectResponse("/login/2fa/enrolar", status_code=303)


# ── Cambio de contraseña (forzado tras el alta, o voluntario) ───────────────

@router.get("/cambiar-clave", response_class=HTMLResponse)
def cambiar_clave_form(request: Request):
    if not sesion_usuario(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "cambiar_clave.html")


@router.post("/cambiar-clave")
def cambiar_clave_post(
    request: Request,
    actual: str = Form(""),
    nueva: str = Form(...),
    repetir: str = Form(...),
    csrf: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    u = sesion_usuario(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not security.verificar_csrf(request.session, csrf):
        flash(request, "Sesión expirada, reintentá.", "error")
        return RedirectResponse("/cambiar-clave", status_code=303)

    user = security.obtener_usuario_id(conn, u["uid"])
    # Si no es un cambio forzado, exigimos la contraseña actual.
    if not request.session.get("must_change") and not security.verify_password(user["password_hash"], actual):
        flash(request, "La contraseña actual no es correcta.", "error")
        return RedirectResponse("/cambiar-clave", status_code=303)
    if len(nueva) < 10:
        flash(request, "La nueva contraseña debe tener al menos 10 caracteres.", "error")
        return RedirectResponse("/cambiar-clave", status_code=303)
    if nueva != repetir:
        flash(request, "Las contraseñas no coinciden.", "error")
        return RedirectResponse("/cambiar-clave", status_code=303)

    security.cambiar_password(conn, u["uid"], nueva)
    request.session["must_change"] = False
    audit.registrar(conn, user["username"], "clave.cambiada", {})
    flash(request, "Contraseña actualizada.", "ok")
    return RedirectResponse(_destino_post_login(request), status_code=303)


# ── Logout ──────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    u = sesion_usuario(request)
    if u:
        audit.registrar(conn, u["user"], "logout", {})
    request.session.clear()
    flash(request, "Sesión cerrada.", "ok")
    return RedirectResponse("/login", status_code=303)
