"""Reporte de horas por cliente y rentabilidad.

Las horas salen de las TAREAS de los tickets de GLPI (campo `actiontime`): cada técnico las
carga con «Registrar tiempo» en el ticket. Con la tarifa por hora de cada persona, lo que se
factura por cliente y el costo fijo de la plataforma, se calcula el margen de cada cliente.
Todo en USD; lo facturado en pesos se convierte con la cotización de referencia (BCRA).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime

import httpx

from ..adapters import glpi as glpi_mod
from . import ServiceError

log = logging.getLogger("itier.horas")

_RE_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BCRA_URL = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD"


def mes_valido(mes: str | None) -> str:
    return mes if mes and _RE_MES.match(mes) else datetime.now().strftime("%Y-%m")


def tarifas(conn: sqlite3.Connection) -> dict[str, float]:
    return {r["username"]: float(r["usd_hora"]) for r in conn.execute("SELECT username, usd_hora FROM tarifas")}


def guardar_tarifas(conn: sqlite3.Connection, valores: dict[str, str]) -> None:
    for u, v in valores.items():
        v = (v or "").strip().replace(",", ".")
        if not v:
            conn.execute("DELETE FROM tarifas WHERE username = ?", (u,))
            continue
        try:
            x = float(v)
        except ValueError as e:
            raise ServiceError(f"La tarifa de {u} no es un número.") from e
        if not 0 <= x <= 10000:
            raise ServiceError(f"La tarifa de {u} está fuera de rango.")
        conn.execute("INSERT INTO tarifas(username, usd_hora) VALUES (?, ?) "
                     "ON CONFLICT(username) DO UPDATE SET usd_hora = excluded.usd_hora", (u, x))
    conn.commit()


def numero_ar(v: str) -> float:
    """Número escrito a la argentina: «257.000», «257.000,50», «1400», «1.455,5», «$ 257000»."""
    v = (v or "").strip().replace("$", "").replace(" ", "")
    if "," in v:                                   # 257.000,50
        v = v.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", v):   # 257.000
        v = v.replace(".", "")
    return float(v)


def guardar_facturacion(conn: sqlite3.Connection, valores: dict[str, str]) -> None:
    """valores: slug del cliente → monto mensual en pesos (vacío = sin dato)."""
    for slug, v in valores.items():
        if not (v or "").strip():
            conn.execute("UPDATE clients SET facturacion_ars = NULL WHERE slug = ?", (slug,))
            continue
        try:
            x = numero_ar(v)
        except ValueError as e:
            raise ServiceError(f"El monto de «{slug}» no es un número.") from e
        if x < 0:
            raise ServiceError("Los montos no pueden ser negativos.")
        conn.execute("UPDATE clients SET facturacion_ars = ? WHERE slug = ?", (x, slug))
    conn.commit()


def cotizacion_bcra() -> tuple[float, str]:
    """Cotización oficial del dólar según el BCRA (mejor esfuerzo): (pesos por USD, fecha)."""
    try:
        r = httpx.get(BCRA_URL, timeout=10)
        r.raise_for_status()
        d = r.json()
        res = d.get("results") if isinstance(d, dict) else None
        res = res[0] if isinstance(res, list) and res else res
        fecha = str((res or {}).get("fecha") or datetime.now().strftime("%Y-%m-%d"))
        for det in (res or {}).get("detalle", []):
            if str(det.get("codigoMoneda", "")).upper() == "USD" and det.get("tipoCotizacion"):
                return float(det["tipoCotizacion"]), fecha
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("No se pudo leer la cotización del BCRA: %s", e)
    raise ServiceError("No se pudo obtener la cotización del BCRA desde el servidor. Cargala a mano.")


def reporte(conn: sqlite3.Connection, cfg, mes: str) -> dict:
    """Horas, costo, ingreso y margen por cliente en un mes («AAAA-MM»)."""
    mes = mes_valido(mes)
    tar = tarifas(conn)
    por_glpi = {str(r["glpi_user_id"]): r for r in conn.execute(
        "SELECT username, nombre, glpi_user_id FROM users WHERE client_id IS NULL AND glpi_user_id IS NOT NULL AND glpi_user_id != ''")}
    clientes = [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY nombre")]
    por_entidad = {str(c["glpi_entity_id"]): c for c in clientes if c.get("glpi_entity_id")}

    with glpi_mod.cliente(cfg) as g:
        tareas = g.listar_tareas()
        entidad_de = {int(t["id"]): str(t.get("entities_id")) for t in g.listar_tickets(limit=3000)}
        nombres: dict[int, str] = {}
        def _nombre(uid: int) -> str:
            if uid not in nombres:
                nombres[uid] = g.nombre_usuario(uid) if uid else "(sin técnico)"
            return nombres[uid]
        acum: dict[str, dict] = {}
        sin_tarifa: set[str] = set()
        for t in tareas:
            if not str(t["date"]).startswith(mes):
                continue
            ent = entidad_de.get(int(t["tickets_id"]))
            cli = por_entidad.get(ent or "")
            if not cli:
                continue
            horas = int(t["actiontime"]) / 3600
            u = por_glpi.get(str(t["users_id_tech"]))
            clave = u["username"] if u else f"glpi:{t['users_id_tech']}"
            nombre = (u["nombre"] or u["username"]) if u else _nombre(int(t["users_id_tech"]))
            tarifa = tar.get(u["username"]) if u else None
            if tarifa is None:
                sin_tarifa.add(nombre)
            a = acum.setdefault(cli["slug"], {})
            d = a.setdefault(clave, {"nombre": nombre, "horas": 0.0, "tarifa": tarifa or 0.0})
            d["horas"] += horas

    fx = cfg.fx_ars_usd
    activos = [c for c in clientes if c.get("estado") == "activo"] or clientes
    plat_cli = (cfg.costo_plataforma_usd / len(activos)) if activos else 0.0
    filas = []
    for c in clientes:
        det = sorted(acum.get(c["slug"], {}).values(), key=lambda d: -d["horas"])
        horas = sum(d["horas"] for d in det)
        costo_h = sum(d["horas"] * d["tarifa"] for d in det)
        for d in det:
            d["costo"] = d["horas"] * d["tarifa"]
        ars = c.get("facturacion_ars")
        ingreso = (ars / fx) if (ars is not None and fx) else None
        plat = plat_cli if c in activos else 0.0
        costo = costo_h + plat
        margen = (ingreso - costo) if ingreso is not None else None
        filas.append({
            "slug": c["slug"], "nombre": c["nombre"], "horas": horas, "detalle": det, "costo_horas": costo_h,
            "plataforma": plat, "costo": costo, "facturacion_ars": ars, "ingreso": ingreso, "margen": margen,
            "margen_pct": (margen / ingreso * 100) if (margen is not None and ingreso) else None,
            "usd_hora_efectivo": (ingreso / horas) if (ingreso is not None and horas) else None,
        })
    tot_h = sum(f["horas"] for f in filas)
    tot_ing = sum(f["ingreso"] for f in filas if f["ingreso"] is not None)
    tot_costo = sum(f["costo"] for f in filas)
    por_persona: dict[str, float] = {}
    for f in filas:
        for d in f["detalle"]:
            por_persona[d["nombre"]] = por_persona.get(d["nombre"], 0.0) + d["horas"]
    return {
        "mes": mes, "filas": filas, "fx": fx, "sin_tarifa": sorted(sin_tarifa),
        "totales": {"horas": tot_h, "costo": tot_costo, "ingreso": tot_ing if fx else None,
                    "margen": (tot_ing - tot_costo) if fx else None},
        "por_persona": sorted(por_persona.items(), key=lambda x: -x[1]),
        "sin_facturacion": [f["nombre"] for f in filas if f["facturacion_ars"] is None],
    }


def registrar_tiempo(cfg, ticket_id: int, minutos: int, nota: str, users_id_tech: int) -> None:
    if not 1 <= minutos <= 24 * 60:
        raise ServiceError("Los minutos tienen que estar entre 1 y 1440.")
    with glpi_mod.cliente(cfg) as g:
        g.registrar_tarea(ticket_id, minutos * 60, (nota or "").strip()[:2000], users_id_tech)


def tiempo_de_ticket(cfg, ticket_id: int) -> list[dict]:
    with glpi_mod.cliente(cfg) as g:
        tareas = g.tareas_de_ticket(ticket_id)
        for t in tareas:
            t["minutos"] = round(int(t["actiontime"]) / 60)
            t["tecnico"] = g.nombre_usuario(int(t["users_id_tech"])) if t["users_id_tech"] else ""
        return tareas
