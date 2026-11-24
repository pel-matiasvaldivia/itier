"""Correo de bienvenida a un usuario de la plataforma: credenciales, responsabilidades del rol y manual.

Se arma en texto y en HTML a partir de services/manual.py. ⚠ Lleva la clave inicial en claro: por eso
es temporal (la consola y Mesh obligan a cambiarla) y el correo le pide al usuario borrarlo después.
Nada de lo que se arma acá se guarda ni se audita.
"""

from __future__ import annotations

from html import escape

from . import manual


def _sistemas(cfg) -> list[tuple[str, str, str]]:
    """(sistema, URL, para qué) de los que están disponibles."""
    s = [("Consola de iTiers", cfg.console_public_url or "(consultá la dirección a tu administrador)",
          "operación diaria: clientes, equipos, tickets y conexión remota")]
    if cfg.public_glpi_url:
        s.append(("GLPI", cfg.public_glpi_url, "tickets e inventario"))
    if cfg.mesh_enabled and cfg.mesh_url:
        s.append(("MeshCentral", cfg.mesh_url, "control remoto (también desde la consola, botón «Conectar»)"))
    if cfg.zbx_public_url:
        s.append(("Zabbix", cfg.zbx_public_url, "monitoreo de infraestructura"))
    return s


def armar(cfg, usuario: str, nombre: str, rol: str, clave: str, motivo: str = "alta") -> tuple[str, str, str]:
    """→ (asunto, texto, html). motivo: 'alta' (bienvenida completa) | 'reinicio' (solo credenciales nuevas)."""
    m = manual.manual_para(rol)
    sistemas = _sistemas(cfg)
    saludo = (nombre or usuario).split(" ")[0]
    url_manual = f"{cfg.console_public_url}/manual" if cfg.console_public_url else ""
    if motivo == "reinicio":
        asunto = "iTiers: tu clave fue reiniciada"
        intro = "Un administrador reinició tu clave de la plataforma iTiers. Estos son tus nuevos datos de acceso."
    else:
        asunto = f"Bienvenido/a a la plataforma iTiers — rol {m['rol_nombre']}"
        intro = (f"Se dio de alta tu usuario en la plataforma iTiers con el rol «{m['rol_nombre']}». "
                 "Estos son tus datos de acceso, tus responsabilidades y el manual de operación de tu perfil.")
    pasos_ingreso = [
        "Entrá a la consola con el usuario y la clave temporal de abajo.",
        "Cambiá la clave cuando la consola te lo pida y activá el segundo factor (2FA) con una app de autenticación.",
        "Entrá a MeshCentral con la misma clave temporal: también te pide cambiarla. En GLPI cambiala desde «Mi configuración».",
    ]
    seguridad = ("Esta clave es temporal y la misma en todos los sistemas. Cambiala hoy mismo y borrá este correo después. "
                 "No la compartas con nadie.")

    # ── texto ──
    t = [f"Hola {saludo},", "", intro, "", "TUS ACCESOS", "-----------"]
    for n, u, para in sistemas:
        t.append(f"  • {n}: {u}  ({para})")
    t += ["", f"  Usuario: {usuario}", f"  Clave temporal: {clave}", "", seguridad, "", "PRIMEROS PASOS", "--------------"]
    t += [f"  {i}. {p}" for i, p in enumerate(pasos_ingreso, 1)]
    if motivo != "reinicio":
        t += ["", f"TU MISIÓN ({m['rol_nombre']})", "-" * 20, m["mision"], "", "TUS RESPONSABILIDADES", "---------------------"]
        t += [f"  • {r}" for r in m["responsabilidades"]]
        t += ["", "QUÉ PODÉS HACER EN LA CONSOLA", "-----------------------------"]
        t += [f"  • {menu}: {que}" for menu, que in m["funciones"]]
        t += ["", "MANUAL DE OPERACIONES", "---------------------"]
        for titulo, pasos in m["procedimientos"]:
            t += ["", titulo.upper()] + [f"  {i}. {p}" for i, p in enumerate(pasos, 1)]
        t += ["", "REGLAS DE ORO", "-------------"] + [f"  • {r}" for r in m["reglas"]]
        t += ["", f"Escalamiento: {m['escalamiento']}"]
        if url_manual:
            t += ["", f"Podés volver a leer este manual cuando quieras en la consola: {url_manual}"]
    t += ["", "Cualquier duda, respondé este correo o consultá a tu administrador.", ""]
    texto = "\n".join(t)

    # ── HTML ──
    def li(items):
        return "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"

    h = [f"<p>Hola {escape(saludo)},</p>", f"<p>{escape(intro)}</p>", "<h3>Tus accesos</h3><ul>"]
    for n, u, para in sistemas:
        enlace = f'<a href="{escape(u)}">{escape(u)}</a>' if u.startswith("http") else escape(u)
        h.append(f"<li><strong>{escape(n)}</strong>: {enlace} <span style='color:#667'>({escape(para)})</span></li>")
    h.append("</ul>")
    h.append("<table cellpadding='8' style='border:1px solid #ccd;border-collapse:collapse;background:#f6f7fb'>"
             f"<tr><td>Usuario</td><td><code><strong>{escape(usuario)}</strong></code></td></tr>"
             f"<tr><td>Clave temporal</td><td><code><strong>{escape(clave)}</strong></code></td></tr></table>")
    h.append(f"<p><strong>⚠ {escape(seguridad)}</strong></p>")
    h.append("<h3>Primeros pasos</h3><ol>" + "".join(f"<li>{escape(p)}</li>" for p in pasos_ingreso) + "</ol>")
    if motivo != "reinicio":
        h.append(f"<h3>Tu misión — {escape(m['rol_nombre'])}</h3><p>{escape(m['mision'])}</p>")
        h.append("<h3>Tus responsabilidades</h3>" + li(m["responsabilidades"]))
        h.append("<h3>Qué podés hacer en la consola</h3><ul>" + "".join(
            f"<li><strong>{escape(a)}</strong>: {escape(b)}</li>" for a, b in m["funciones"]) + "</ul>")
        h.append("<h3>Manual de operaciones</h3>")
        for titulo, pasos in m["procedimientos"]:
            h.append(f"<h4>{escape(titulo)}</h4><ol>" + "".join(f"<li>{escape(p)}</li>" for p in pasos) + "</ol>")
        h.append("<h3>Reglas de oro</h3>" + li(m["reglas"]))
        h.append(f"<p><strong>Escalamiento:</strong> {escape(m['escalamiento'])}</p>")
        if url_manual:
            h.append(f'<p>Podés volver a leer este manual cuando quieras en la consola: <a href="{escape(url_manual)}">{escape(url_manual)}</a></p>')
    h.append("<p>Cualquier duda, respondé este correo o consultá a tu administrador.</p>")
    html = ("<!doctype html><html><body style='font-family:Segoe UI,Arial,sans-serif;max-width:720px;color:#1a1d29'>"
            + "".join(h) + "</body></html>")
    return asunto, texto, html
