"""Activos de cada cliente (notebooks, desktops, monitores, celulares…) y su asignación a personas.

Las personas son los contactos del cliente (`client_contactos`): las mismas que escriben por correo o WhatsApp; para asignar un
activo no hace falta que tengan canal. Cada movimiento (alta, asignación, devolución, cambio de estado) queda en el historial.
Todo se valida contra el cliente: un activo solo se asigna a personas del MISMO cliente.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .. import audit
from . import ServiceError

TIPOS = {"notebook": ("Notebook", "💻"), "desktop": ("Desktop", "🖥️"), "monitor": ("Monitor", "🖵"),
         "celular": ("Celular", "📱"), "otro": ("Otro", "📦")}
ESTADOS = {"en_uso": "En uso", "en_stock": "En stock", "en_reparacion": "En reparación", "de_baja": "De baja"}
_SINONIMOS_TIPO = {
    "notebook": "notebook", "laptop": "notebook", "portatil": "notebook", "portátil": "notebook", "note": "notebook",
    "desktop": "desktop", "pc": "desktop", "escritorio": "desktop", "computadora": "desktop", "cpu": "desktop",
    "monitor": "monitor", "pantalla": "monitor", "display": "monitor",
    "celular": "celular", "telefono": "celular", "teléfono": "celular", "movil": "celular", "móvil": "celular", "phone": "celular", "smartphone": "celular",
    "otro": "otro",
}
_SINONIMOS_ESTADO = {"en uso": "en_uso", "uso": "en_uso", "asignado": "en_uso", "en stock": "en_stock", "stock": "en_stock", "disponible": "en_stock",
                     "en reparacion": "en_reparacion", "en reparación": "en_reparacion", "reparacion": "en_reparacion", "reparación": "en_reparacion",
                     "de baja": "de_baja", "baja": "de_baja"}
_COLUMNAS = {  # alias de encabezados del CSV → campo
    "tipo": "tipo", "type": "tipo", "categoria": "tipo", "categoría": "tipo",
    "etiqueta": "etiqueta", "codigo": "etiqueta", "código": "etiqueta", "inventario": "etiqueta", "id": "etiqueta", "nombre": "etiqueta", "tag": "etiqueta",
    "marca": "marca", "fabricante": "marca", "brand": "marca",
    "modelo": "modelo", "model": "modelo",
    "serie": "serie", "serial": "serie", "n° de serie": "serie", "nro de serie": "serie", "numero de serie": "serie", "número de serie": "serie", "s/n": "serie",
    "imei": "imei", "linea": "linea", "línea": "linea", "numero": "linea", "número": "linea", "telefono": "linea_alt",
    "fecha_compra": "fecha_compra", "fecha de compra": "fecha_compra", "compra": "fecha_compra",
    "garantia_hasta": "garantia_hasta", "garantia": "garantia_hasta", "garantía": "garantia_hasta", "garantía hasta": "garantia_hasta", "vence garantia": "garantia_hasta",
    "estado": "estado", "asignado_a": "asignado", "asignado a": "asignado", "asignado": "asignado", "usuario": "asignado", "responsable": "asignado",
    "notas": "notas", "observaciones": "notas", "comentarios": "notas",
}
MAX_FILAS = 1000


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _txt(v, largo=200) -> str | None:
    v = (v or "").strip()
    return v[:largo] or None


def _fecha(v: str | None, campo: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    raise ServiceError(f"La fecha de {campo} no es válida (usá AAAA-MM-DD o DD/MM/AAAA).")


def garantia_estado(hasta: str | None) -> str | None:
    """vigente | por_vencer (≤ 60 días) | vencida | None."""
    if not hasta:
        return None
    try:
        d = date.fromisoformat(hasta)
    except ValueError:
        return None
    hoy = date.today()
    return "vencida" if d < hoy else ("por_vencer" if d <= hoy + timedelta(days=60) else "vigente")


# ---------------------------------------------------------------- personas

def personas(conn: sqlite3.Connection, client_id: int, solo_activas: bool = True) -> list[dict]:
    filas = conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM activos a WHERE a.persona_id = p.id AND a.estado != 'de_baja') AS n_activos "
        f"FROM client_contactos p WHERE p.client_id = ? {'AND p.activo = 1' if solo_activas else ''} ORDER BY COALESCE(p.nombre, p.email, p.whatsapp)", (client_id,))
    return [dict(r) for r in filas]


def crear_persona(conn: sqlite3.Connection, client_id: int, actor: str, nombre: str, email: str = "", cargo: str = "", area: str = "") -> int:
    from . import canales, correo
    nombre = (nombre or "").strip()
    if not nombre:
        raise ServiceError("El nombre de la persona es obligatorio.")
    em = None
    if (email or "").strip():
        em = canales.normalizar_email(email)
        if not em:
            raise ServiceError("El correo no es válido.")
        if conn.execute("SELECT 1 FROM client_contactos WHERE email = ?", (em,)).fetchone():
            raise ServiceError(f"«{em}» ya está cargado como contacto de un cliente.")
    cur = conn.execute("INSERT INTO client_contactos(client_id, nombre, email, whatsapp, cargo, area, activo, created_at, created_by) VALUES (?,?,?,?,?,?,1,?,?)",
                       (client_id, nombre[:120], em, None, _txt(cargo, 80), _txt(area, 80), ahora(), actor))
    conn.commit()
    audit.registrar(conn, actor, "activos.persona_creada", {"client_id": client_id, "nombre": nombre})
    return cur.lastrowid


def editar_persona(conn, client_id: int, pid: int, actor: str, nombre: str, cargo: str, area: str, activo: bool) -> None:
    p = conn.execute("SELECT * FROM client_contactos WHERE id = ? AND client_id = ?", (pid, client_id)).fetchone()
    if not p:
        raise ServiceError("Persona no encontrada.")
    if not (nombre or "").strip():
        raise ServiceError("El nombre es obligatorio.")
    if not activo:
        n = conn.execute("SELECT COUNT(*) n FROM activos WHERE persona_id = ? AND estado != 'de_baja'", (pid,)).fetchone()["n"]
        if n:
            raise ServiceError(f"{p['nombre'] or 'La persona'} tiene {n} activo(s) asignado(s): devolvelos antes de desactivarla.")
    conn.execute("UPDATE client_contactos SET nombre = ?, cargo = ?, area = ?, activo = ? WHERE id = ?",
                 (nombre.strip()[:120], _txt(cargo, 80), _txt(area, 80), 1 if activo else 0, pid))
    conn.commit()
    audit.registrar(conn, actor, "activos.persona_editada", {"client_id": client_id, "persona_id": pid, "activo": activo})


def _persona(conn, client_id: int, pid) -> dict:
    p = conn.execute("SELECT * FROM client_contactos WHERE id = ? AND client_id = ? AND activo = 1", (pid, client_id)).fetchone() if pid else None
    if not p:
        raise ServiceError("Elegí una persona de este cliente.")
    return dict(p)


def _nombre_persona(p: dict) -> str:
    return p.get("nombre") or p.get("email") or p.get("whatsapp") or f"#{p['id']}"


# ---------------------------------------------------------------- activos

def _fila(conn, client_id: int, aid: int) -> dict:
    r = conn.execute("SELECT a.*, COALESCE(p.nombre, p.email, p.whatsapp) AS persona FROM activos a "
                     "LEFT JOIN client_contactos p ON p.id = a.persona_id WHERE a.id = ? AND a.client_id = ?", (aid, client_id)).fetchone()
    if not r:
        raise ServiceError("Activo no encontrado.")
    return _enriquecer(dict(r))


def _enriquecer(a: dict) -> dict:
    a["tipo_label"], a["tipo_icono"] = TIPOS.get(a["tipo"], TIPOS["otro"])
    a["estado_label"] = ESTADOS.get(a["estado"], a["estado"])
    a["garantia"] = garantia_estado(a.get("garantia_hasta"))
    return a


def obtener(conn, client_id: int, aid: int) -> dict:
    return _fila(conn, client_id, aid)


def listar(conn, client_id: int, tipo: str = "", estado: str = "", persona: str = "", q: str = "") -> list[dict]:
    sql = ("SELECT a.*, COALESCE(p.nombre, p.email, p.whatsapp) AS persona FROM activos a "
           "LEFT JOIN client_contactos p ON p.id = a.persona_id WHERE a.client_id = ?")
    args: list = [client_id]
    if tipo in TIPOS:
        sql += " AND a.tipo = ?"; args.append(tipo)
    if estado in ESTADOS:
        sql += " AND a.estado = ?"; args.append(estado)
    elif estado == "sin_agente":
        sql += " AND a.tipo IN ('notebook','desktop') AND a.glpi_id IS NULL AND a.estado != 'de_baja'"
    elif estado == "garantia":
        sql += " AND a.garantia_hasta IS NOT NULL AND a.garantia_hasta <= ? AND a.estado != 'de_baja'"; args.append((date.today() + timedelta(days=60)).isoformat())
    if persona.isdigit():
        sql += " AND a.persona_id = ?"; args.append(int(persona))
    elif persona == "sin":
        sql += " AND a.persona_id IS NULL"
    if q.strip():
        sql += " AND (a.etiqueta LIKE ? OR a.marca LIKE ? OR a.modelo LIKE ? OR a.serie LIKE ? OR a.imei LIKE ? OR p.nombre LIKE ?)"
        args += [f"%{q.strip()}%"] * 6
    sql += " ORDER BY a.tipo, COALESCE(a.etiqueta, ''), a.id"
    return [_enriquecer(dict(r)) for r in conn.execute(sql, args)]


def resumen(conn, client_id: int) -> dict:
    todos = [dict(r) for r in conn.execute("SELECT tipo, estado, persona_id, garantia_hasta, glpi_id FROM activos WHERE client_id = ?", (client_id,))]
    vivos = [a for a in todos if a["estado"] != "de_baja"]
    por_tipo = {t: sum(1 for a in vivos if a["tipo"] == t) for t in TIPOS}
    return {"total": len(vivos), "asignados": sum(1 for a in vivos if a["persona_id"]), "en_stock": sum(1 for a in vivos if a["estado"] == "en_stock"),
            "en_reparacion": sum(1 for a in vivos if a["estado"] == "en_reparacion"),
            "garantia": sum(1 for a in vivos if garantia_estado(a["garantia_hasta"]) in ("por_vencer", "vencida")),
            "con_agente": sum(1 for a in vivos if a["glpi_id"]),
            "sin_agente": sum(1 for a in vivos if a["tipo"] in ("notebook", "desktop") and not a["glpi_id"]),
            "por_tipo": {k: v for k, v in por_tipo.items() if v}, "bajas": len(todos) - len(vivos)}


def _historial(conn, aid: int, accion: str, persona: str | None, detalle: str | None, por: str) -> None:
    conn.execute("INSERT INTO activo_historial(activo_id, accion, persona, detalle, por, at) VALUES (?,?,?,?,?,?)", (aid, accion, persona, detalle, por, ahora()))


def historial(conn, aid: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM activo_historial WHERE activo_id = ? ORDER BY id DESC", (aid,))]


def _validar(d: dict) -> dict:
    tipo = (d.get("tipo") or "").strip().lower()
    if tipo not in TIPOS:
        raise ServiceError("Elegí el tipo de activo.")
    limpio = {"tipo": tipo, "etiqueta": _txt(d.get("etiqueta"), 60), "marca": _txt(d.get("marca"), 60), "modelo": _txt(d.get("modelo"), 80),
              "serie": _txt(d.get("serie"), 80), "imei": _txt(d.get("imei"), 40) if tipo == "celular" else None,
              "linea": _txt(d.get("linea"), 40) if tipo == "celular" else None,
              "fecha_compra": _fecha(d.get("fecha_compra"), "compra"), "garantia_hasta": _fecha(d.get("garantia_hasta"), "garantía"),
              "notas": _txt(d.get("notas"), 1000)}
    if limpio["fecha_compra"] and limpio["garantia_hasta"] and limpio["garantia_hasta"] < limpio["fecha_compra"]:
        raise ServiceError("La garantía no puede vencer antes de la fecha de compra.")
    if not (limpio["etiqueta"] or limpio["serie"] or limpio["modelo"]):
        raise ServiceError("Indicá al menos la etiqueta, el modelo o el número de serie para poder identificarlo.")
    return limpio


def _unico(conn, client_id: int, d: dict, excepto: int | None = None) -> None:
    for campo, texto in (("etiqueta", "etiqueta"), ("serie", "número de serie")):
        if not d.get(campo):
            continue
        sql = f"SELECT id FROM activos WHERE client_id = ? AND {campo} = ?" + (" AND tipo = ?" if campo == "serie" else "")
        args = [client_id, d[campo]] + ([d["tipo"]] if campo == "serie" else [])
        r = conn.execute(sql, args).fetchone()
        if r and r["id"] != excepto:
            raise ServiceError(f"Ya existe un activo con esa {texto} («{d[campo]}») en este cliente.")


def crear(conn, client_id: int, actor: str, datos: dict, persona_id=None) -> int:
    d = _validar(datos)
    _unico(conn, client_id, d)
    estado = "en_stock"
    pers = None
    if persona_id:
        pers = _persona(conn, client_id, persona_id)
        estado = "en_uso"
    cur = conn.execute(
        "INSERT INTO activos(client_id, tipo, etiqueta, marca, modelo, serie, imei, linea, fecha_compra, garantia_hasta, estado, persona_id, notas, created_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (client_id, d["tipo"], d["etiqueta"], d["marca"], d["modelo"], d["serie"], d["imei"], d["linea"], d["fecha_compra"], d["garantia_hasta"],
         estado, pers["id"] if pers else None, d["notas"], ahora(), actor))
    aid = cur.lastrowid
    _historial(conn, aid, "alta", None, None, actor)
    if pers:
        _historial(conn, aid, "asignado", _nombre_persona(pers), "Asignado al dar de alta", actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.creado", {"client_id": client_id, "activo_id": aid, "tipo": d["tipo"], "etiqueta": d["etiqueta"]})
    return aid


def editar(conn, client_id: int, aid: int, actor: str, datos: dict) -> None:
    _fila(conn, client_id, aid)
    d = _validar(datos)
    _unico(conn, client_id, d, excepto=aid)
    conn.execute("UPDATE activos SET tipo=?, etiqueta=?, marca=?, modelo=?, serie=?, imei=?, linea=?, fecha_compra=?, garantia_hasta=?, notas=?, updated_at=? WHERE id=?",
                 (d["tipo"], d["etiqueta"], d["marca"], d["modelo"], d["serie"], d["imei"], d["linea"], d["fecha_compra"], d["garantia_hasta"], d["notas"], ahora(), aid))
    _historial(conn, aid, "editado", None, None, actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.editado", {"client_id": client_id, "activo_id": aid})


def asignar(conn, client_id: int, aid: int, actor: str, persona_id, nota: str = "") -> None:
    a = _fila(conn, client_id, aid)
    if a["estado"] == "de_baja":
        raise ServiceError("Un activo dado de baja no se puede asignar.")
    p = _persona(conn, client_id, persona_id)
    if a["persona_id"] == p["id"]:
        raise ServiceError("Ya está asignado a esa persona.")
    if a["persona_id"]:
        _historial(conn, aid, "devuelto", a["persona"], "Reasignado a otra persona", actor)
    conn.execute("UPDATE activos SET persona_id = ?, estado = 'en_uso', updated_at = ? WHERE id = ?", (p["id"], ahora(), aid))
    _historial(conn, aid, "asignado", _nombre_persona(p), _txt(nota, 300), actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.asignado", {"client_id": client_id, "activo_id": aid, "persona": _nombre_persona(p)})


def devolver(conn, client_id: int, aid: int, actor: str, nota: str = "") -> None:
    a = _fila(conn, client_id, aid)
    if not a["persona_id"]:
        raise ServiceError("El activo no está asignado a nadie.")
    conn.execute("UPDATE activos SET persona_id = NULL, estado = 'en_stock', updated_at = ? WHERE id = ?", (ahora(), aid))
    _historial(conn, aid, "devuelto", a["persona"], _txt(nota, 300), actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.devuelto", {"client_id": client_id, "activo_id": aid, "persona": a["persona"]})


def cambiar_estado(conn, client_id: int, aid: int, actor: str, estado: str, nota: str = "") -> None:
    a = _fila(conn, client_id, aid)
    if estado not in ("en_stock", "en_reparacion", "de_baja"):
        raise ServiceError("Estado inválido.")
    if estado == a["estado"] and not a["persona_id"]:
        raise ServiceError("Ya está en ese estado.")
    if a["persona_id"]:                                     # al pasar a stock/reparación/baja se libera de la persona
        _historial(conn, aid, "devuelto", a["persona"], f"Pasó a {ESTADOS[estado].lower()}", actor)
    conn.execute("UPDATE activos SET estado = ?, persona_id = NULL, updated_at = ? WHERE id = ?", (estado, ahora(), aid))
    _historial(conn, aid, "estado", None, f"{ESTADOS[estado]}" + (f": {_txt(nota, 300)}" if _txt(nota, 300) else ""), actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.estado", {"client_id": client_id, "activo_id": aid, "estado": estado})


def eliminar(conn, client_id: int, aid: int, actor: str) -> None:
    a = _fila(conn, client_id, aid)
    conn.execute("DELETE FROM activo_historial WHERE activo_id = ?", (aid,))
    conn.execute("DELETE FROM activos WHERE id = ?", (aid,))
    conn.commit()
    audit.registrar(conn, actor, "activos.eliminado", {"client_id": client_id, "activo_id": aid, "tipo": a["tipo"], "etiqueta": a["etiqueta"]})


# ---------------------------------------------------------------- CSV

ENCABEZADOS_CSV = ["tipo", "etiqueta", "marca", "modelo", "serie", "imei", "linea", "fecha_compra", "garantia_hasta", "estado", "asignado_a", "notas"]


def exportar_csv(conn, client_id: int) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(ENCABEZADOS_CSV)
    for a in listar(conn, client_id):
        w.writerow([a["tipo_label"], a["etiqueta"] or "", a["marca"] or "", a["modelo"] or "", a["serie"] or "", a["imei"] or "", a["linea"] or "",
                    a["fecha_compra"] or "", a["garantia_hasta"] or "", a["estado_label"], a["persona"] or "", a["notas"] or ""])
    return "﻿" + out.getvalue()


def plantilla_csv() -> str:
    return ("﻿" + ";".join(ENCABEZADOS_CSV) + "\n"
            "Notebook;NB-001;Lenovo;ThinkPad E14;PF3ABC12;;;15/03/2025;15/03/2028;En uso;Ana Pérez;Gerencia\n"
            "Monitor;MON-014;Dell;P2422H;CN0XYZ99;;;;;En stock;;\n"
            "Celular;CEL-003;Samsung;Galaxy A54;;356938035643809;1155551234;01/08/2025;01/08/2026;En uso;Luis Gómez;\n")


def _decodificar(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ServiceError("No se pudo leer el archivo: guardalo como CSV (UTF-8).")


def importar_csv(conn, client_id: int, actor: str, raw: bytes) -> dict:
    """Carga activos desde un CSV (separador «;» o «,»). Devuelve {creados, omitidos, personas_nuevas, errores:[(fila, motivo)]}."""
    if len(raw) > 1_000_000:
        raise ServiceError("El archivo es demasiado grande (máximo 1 MB).")
    texto = _decodificar(raw)
    muestra = texto.splitlines()[0] if texto.strip() else ""
    delim = ";" if muestra.count(";") >= muestra.count(",") else ","
    filas = list(csv.reader(io.StringIO(texto), delimiter=delim))
    if len(filas) < 2:
        raise ServiceError("El archivo no tiene filas para importar.")
    if len(filas) - 1 > MAX_FILAS:
        raise ServiceError(f"Hay más de {MAX_FILAS} filas: dividí el archivo.")
    cab = [_COLUMNAS.get(h.strip().lower().lstrip("﻿"), "") for h in filas[0]]
    if "tipo" not in cab:
        raise ServiceError("Falta la columna «tipo» (Notebook, Desktop, Monitor, Celular). Descargá la plantilla de ejemplo.")
    res = {"creados": 0, "omitidos": 0, "personas_nuevas": 0, "errores": []}
    cache_personas: dict[str, int] = {}
    for n, fila in enumerate(filas[1:], start=2):
        if not any(c.strip() for c in fila):
            continue
        d = {cab[i]: c.strip() for i, c in enumerate(fila) if i < len(cab) and cab[i]}
        try:
            tipo = _SINONIMOS_TIPO.get(d.get("tipo", "").lower().strip())
            if not tipo:
                raise ServiceError(f"tipo «{d.get('tipo', '')}» desconocido")
            d["tipo"] = tipo
            if not d.get("linea") and d.get("linea_alt"):
                d["linea"] = d["linea_alt"]
            est = _SINONIMOS_ESTADO.get((d.get("estado") or "").lower().strip())
            pid = None
            quien = (d.get("asignado") or "").strip()
            if quien:
                clave = quien.lower()
                if clave not in cache_personas:
                    r = conn.execute("SELECT id FROM client_contactos WHERE client_id = ? AND (LOWER(email) = ? OR LOWER(nombre) = ?)", (client_id, clave, clave)).fetchone()
                    if r:
                        cache_personas[clave] = r["id"]
                    else:
                        cache_personas[clave] = crear_persona(conn, client_id, actor, quien if "@" not in quien else quien.split("@")[0], quien if "@" in quien else "")
                        res["personas_nuevas"] += 1
                pid = cache_personas[clave]
                p = conn.execute("SELECT activo FROM client_contactos WHERE id = ?", (pid,)).fetchone()
                if p and not p["activo"]:
                    conn.execute("UPDATE client_contactos SET activo = 1 WHERE id = ?", (pid,))
            try:
                aid = crear(conn, client_id, actor, d, persona_id=pid)
            except ServiceError as e:
                if "Ya existe un activo" in e.mensaje:
                    res["omitidos"] += 1
                    res["errores"].append((n, "omitida: " + e.mensaje))
                    continue
                raise
            if not pid and est in ("en_reparacion", "de_baja"):
                cambiar_estado(conn, client_id, aid, actor, est, "Importado")
            res["creados"] += 1
        except ServiceError as e:
            res["errores"].append((n, e.mensaje))
    audit.registrar(conn, actor, "activos.importado", {"client_id": client_id, "creados": res["creados"], "omitidos": res["omitidos"], "errores": len(res["errores"])})
    return res


# ---------------------------------------------------------------- integración con el inventario de GLPI (agente)
# El GLPI Agent corre en Windows, macOS y Linux: sirve para notebooks y desktops. Los celulares (Android/iOS) no tienen agente de GLPI:
# se llevan a mano (o por un MDM). Los activos se cruzan con las computadoras de GLPI por número de serie (o, si falta, por nombre).

def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tipo_glpi(texto: str | None) -> str:
    t = (texto or "").lower()
    if any(x in t for x in ("laptop", "notebook", "portable", "portátil", "portatil", "netbook")):
        return "notebook"
    if any(x in t for x in ("server", "servidor")):
        return "otro"
    return "desktop"


def frescura(ultimo: str | None) -> str | None:
    """Qué tan reciente es el último inventario del agente: al_dia (≤ 3 días) | atrasado (≤ 14) | sin_reportar | None."""
    if not ultimo:
        return None
    try:
        d = datetime.fromisoformat(ultimo.replace("T", " ")[:19])
    except ValueError:
        return None
    dias = (datetime.now() - d).days
    return "al_dia" if dias <= 3 else ("atrasado" if dias <= 14 else "sin_reportar")


def equipos_glpi(cfg, cli: dict) -> tuple[list[dict], str | None]:
    """(equipos que GLPI conoce del cliente, error). Nunca levanta: la lista de activos funciona aunque GLPI esté caído."""
    from ..adapters import AdapterError, glpi as glpi_mod
    if not cfg.glpi_configurado or not cli.get("glpi_entity_id"):
        return [], None
    try:
        with glpi_mod.cliente(cfg) as g:
            return g.equipos_inventariados(cli["glpi_entity_id"]), None
    except AdapterError as e:
        return [], e.detalle


def vista_agente(activos: list[dict], equipos: list[dict]) -> None:
    """Agrega a cada activo `glpi` (el equipo vinculado, si GLPI lo devolvió) y `agente` (frescura del inventario)."""
    por_id = {e["id"]: e for e in equipos}
    for a in activos:
        e = por_id.get(str(a.get("glpi_id") or ""))
        a["glpi"] = e
        ultimo = (e or {}).get("ultimo_inventario") or a.get("glpi_visto")
        a["agente"] = frescura(ultimo) if a.get("glpi_id") else None
        a["agente_visto"] = (ultimo or "")[:16]


def pendientes_glpi(conn, client_id: int, equipos: list[dict]) -> list[dict]:
    vinculados = {r["glpi_id"] for r in conn.execute("SELECT glpi_id FROM activos WHERE client_id = ? AND glpi_id IS NOT NULL", (client_id,))}
    return [e for e in equipos if e["id"] not in vinculados]


def _set_vinculo(conn, aid: int, e: dict | None) -> None:
    conn.execute("UPDATE activos SET glpi_id = ?, glpi_visto = ?, updated_at = ? WHERE id = ?",
                 (e["id"] if e else None, (e or {}).get("ultimo_inventario") or None, ahora(), aid))


def sincronizar(conn, cfg, cli: dict, actor: str, importar: bool = True) -> dict:
    """Vincula los activos con los equipos de GLPI (serie, o nombre = etiqueta) y trae los equipos que faltan en la lista."""
    equipos, error = equipos_glpi(cfg, cli)
    res = {"vinculados": 0, "actualizados": 0, "importados": 0, "sin_serie": 0, "error": error}
    if error:
        return res
    cid = cli["id"]
    por_id = {e["id"]: e for e in equipos}
    libres = [e for e in equipos if e["id"] not in {r["glpi_id"] for r in conn.execute("SELECT glpi_id FROM activos WHERE client_id = ? AND glpi_id IS NOT NULL", (cid,))}]
    for a in [dict(r) for r in conn.execute("SELECT * FROM activos WHERE client_id = ? AND tipo IN ('notebook','desktop','otro') AND estado != 'de_baja'", (cid,))]:
        if a["glpi_id"]:
            e = por_id.get(a["glpi_id"])
            if e and (e["ultimo_inventario"] or None) != a["glpi_visto"]:
                _set_vinculo(conn, a["id"], e)
                res["actualizados"] += 1
            continue
        ns = _norm(a["serie"])
        elegido = next((e for e in libres if ns and _norm(e["serie"]) == ns), None) or \
                  next((e for e in libres if a["etiqueta"] and e["nombre"] and e["nombre"].strip().lower() == a["etiqueta"].strip().lower()), None)
        if elegido:
            libres.remove(elegido)
            _set_vinculo(conn, a["id"], elegido)
            _historial(conn, a["id"], "editado", None, f"Vinculado con el equipo «{elegido['nombre']}» de GLPI", actor)
            res["vinculados"] += 1
    if importar:
        for e in libres:
            tipo = _tipo_glpi(e["tipo"])
            etiqueta = e["nombre"] or f"GLPI-{e['id']}"
            if conn.execute("SELECT 1 FROM activos WHERE client_id = ? AND etiqueta = ?", (cid, etiqueta)).fetchone():
                etiqueta = f"{etiqueta}-{e['id']}"
            serie = e["serie"] if e["serie"] and not conn.execute("SELECT 1 FROM activos WHERE client_id = ? AND tipo = ? AND serie = ?", (cid, tipo, e["serie"])).fetchone() else ""
            if not e["serie"]:
                res["sin_serie"] += 1
            try:
                aid = crear(conn, cid, actor, {"tipo": tipo, "etiqueta": etiqueta, "marca": e["fabricante"], "modelo": e["modelo"], "serie": serie,
                                               "notas": "Importado del inventario de GLPI (agente)."})
            except ServiceError:
                continue
            _set_vinculo(conn, aid, e)
            conn.execute("UPDATE activo_historial SET detalle = 'Importado del inventario de GLPI' WHERE activo_id = ? AND accion = 'alta'", (aid,))
            res["importados"] += 1
    conn.commit()
    audit.registrar(conn, actor, "activos.sincronizado", {"client_id": cid, **{k: v for k, v in res.items() if k != "error"}})
    return res


def vincular(conn, client_id: int, aid: int, actor: str, glpi_id: str, equipos: list[dict]) -> None:
    a = _fila(conn, client_id, aid)
    if a["tipo"] not in ("notebook", "desktop", "otro"):
        raise ServiceError("Solo se vinculan notebooks, desktops u otros equipos con agente (los monitores y celulares no reportan a GLPI).")
    e = next((x for x in equipos if x["id"] == str(glpi_id)), None)
    if not e:
        raise ServiceError("Ese equipo no está en el inventario de GLPI de este cliente.")
    if conn.execute("SELECT 1 FROM activos WHERE client_id = ? AND glpi_id = ? AND id != ?", (client_id, e["id"], aid)).fetchone():
        raise ServiceError("Ese equipo de GLPI ya está vinculado a otro activo.")
    _set_vinculo(conn, aid, e)
    _historial(conn, aid, "editado", None, f"Vinculado con el equipo «{e['nombre']}» de GLPI", actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.vinculado", {"client_id": client_id, "activo_id": aid, "glpi_id": e["id"]})


def desvincular(conn, client_id: int, aid: int, actor: str) -> None:
    a = _fila(conn, client_id, aid)
    if not a.get("glpi_id"):
        raise ServiceError("El activo no está vinculado a GLPI.")
    _set_vinculo(conn, aid, None)
    _historial(conn, aid, "editado", None, "Se quitó el vínculo con GLPI", actor)
    conn.commit()
    audit.registrar(conn, actor, "activos.desvinculado", {"client_id": client_id, "activo_id": aid})


# ---------------------------------------------------------------- personas desde Microsoft 365 / Google Workspace
# Se traen solo los usuarios con licencia ACTIVA: en Microsoft 365, con al menos una licencia asignada y la cuenta habilitada (así quedan afuera los buzones
# compartidos, las salas y los usuarios bloqueados); en Google Workspace, cuentas ni suspendidas ni archivadas (Google no informa la licencia en el directorio).
# Quienes ya no cumplen se desactivan como personas (si no tienen activos asignados). Los invitados externos no se traen.

def _usuarios_origen(cfg, cli: dict) -> tuple[list[dict], list[dict], dict]:
    """(licenciados, fuentes, errores): usuarios normalizados {origen, origen_id, email, nombre, cargo, area} de cada nube conectada al cliente."""
    from ..adapters import AdapterError, gws as gws_mod, m365 as m365_mod
    from . import m365 as m365_svc
    lic, fuentes, errores = [], [], {}
    if cli.get("m365_tenant_id") and cli.get("m365_estado") == "conectado":
        try:
            todos = m365_mod.cliente(m365_svc.cfg_de(cfg, cli), cli["m365_tenant_id"]).listar_usuarios()
            act = [u for u in todos if u["licencias"] and u["habilitado"] and u["tipo"] != "Guest" and (u["correo"] or u["upn"])]
            lic += [{"origen": "m365", "origen_id": u["id"], "email": (u["correo"] or u["upn"]).lower(), "nombre": u["nombre"] or u["upn"].split("@")[0],
                     "cargo": u["cargo"], "area": u["departamento"]} for u in act]
            fuentes.append({"origen": "m365", "nombre": "Microsoft 365", "total": len(todos), "con_licencia": len(act)})
        except AdapterError as e:
            errores["m365"] = e.detalle
    if cli.get("gws_admin") and cli.get("gws_estado") == "conectado":
        try:
            todos = gws_mod.cliente(cfg, cli["gws_admin"]).listar_usuarios()
            act = [u for u in todos if not u["suspendido"] and not u["archivado"] and u["email"]]
            lic += [{"origen": "gws", "origen_id": u["id"], "email": u["email"].lower(), "nombre": u["nombre"] or u["email"].split("@")[0],
                     "cargo": u["cargo"], "area": u["departamento"]} for u in act]
            fuentes.append({"origen": "gws", "nombre": "Google Workspace", "total": len(todos), "con_licencia": len(act)})
        except AdapterError as e:
            errores["gws"] = e.detalle
    return lic, fuentes, errores


def fuentes_conectadas(cli: dict) -> list[str]:
    f = []
    if cli.get("m365_tenant_id") and cli.get("m365_estado") == "conectado":
        f.append("Microsoft 365")
    if cli.get("gws_admin") and cli.get("gws_estado") == "conectado":
        f.append("Google Workspace")
    return f


def sincronizar_usuarios(conn, cfg, cli: dict, actor: str) -> dict:
    """Trae como personas a los usuarios con licencia activa de las nubes conectadas. Devuelve el detalle de lo que hizo."""
    cid = cli["id"]
    lic, fuentes, errores = _usuarios_origen(cfg, cli)
    res = {"fuentes": fuentes, "errores": errores, "creadas": [], "actualizadas": 0, "reactivadas": 0, "desactivadas": [], "con_activos": [], "conflictos": []}
    if not fuentes:
        return res
    vistos: set[str] = set()
    for u in lic:
        if u["email"] in vistos:                                   # la misma persona en las dos nubes: se toma una sola vez
            continue
        vistos.add(u["email"])
        p = conn.execute("SELECT * FROM client_contactos WHERE LOWER(email) = ?", (u["email"],)).fetchone()
        if p and p["client_id"] != cid:
            res["conflictos"].append((u["email"], "ya está cargado como contacto de otro cliente"))
            continue
        if p:
            cambios, args = [], []
            for campo, valor in (("nombre", u["nombre"]), ("cargo", u["cargo"]), ("area", u["area"])):
                if valor and not p[campo]:
                    cambios.append(f"{campo} = ?"); args.append(valor[:120])
            if not p["origen"]:
                cambios += ["origen = ?", "origen_id = ?"]; args += [u["origen"], u["origen_id"]]
            if not p["activo"]:
                cambios.append("activo = 1"); res["reactivadas"] += 1
            if cambios:
                conn.execute(f"UPDATE client_contactos SET {', '.join(cambios)} WHERE id = ?", (*args, p["id"]))
                res["actualizadas"] += 1
            continue
        conn.execute("INSERT INTO client_contactos(client_id, nombre, email, whatsapp, cargo, area, activo, origen, origen_id, created_at, created_by) VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                     (cid, u["nombre"][:120], u["email"], None, _txt(u["cargo"], 80), _txt(u["area"], 80), u["origen"], u["origen_id"], ahora(), actor))
        res["creadas"].append(u["nombre"])
    # bajas: las personas que vinieron de una nube que respondió y ya no tienen licencia activa
    for f in fuentes:
        activos_ids = {u["origen_id"] for u in lic if u["origen"] == f["origen"]}
        for p in conn.execute("SELECT * FROM client_contactos WHERE client_id = ? AND origen = ? AND activo = 1", (cid, f["origen"])).fetchall():
            if p["origen_id"] in activos_ids:
                continue
            n = conn.execute("SELECT COUNT(*) n FROM activos WHERE persona_id = ? AND estado != 'de_baja'", (p["id"],)).fetchone()["n"]
            nombre = p["nombre"] or p["email"]
            if n:
                res["con_activos"].append((nombre, n))
            else:
                conn.execute("UPDATE client_contactos SET activo = 0 WHERE id = ?", (p["id"],))
                res["desactivadas"].append(nombre)
    conn.commit()
    audit.registrar(conn, actor, "activos.usuarios_sincronizados", {
        "client_id": cid, "fuentes": [f["nombre"] for f in fuentes], "creadas": len(res["creadas"]), "actualizadas": res["actualizadas"],
        "desactivadas": len(res["desactivadas"]), "con_activos": len(res["con_activos"]), "conflictos": len(res["conflictos"])})
    return res
