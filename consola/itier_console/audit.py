"""Auditoría append-only encadenada por hash.

Mismo principio que el agente de borde (reference/edge-agent/itier_agent/audit.py):
cada entrada incluye el hash de la anterior, así reescribir el pasado invalida
toda la cadena posterior y la manipulación es detectable (OWASP ASI10 — Rogue
Agents). Acá se persiste en la tabla audit_log de SQLite.

Toda acción que cambia estado (login, alta de cliente, vinculación de agente,
alta/asignación de ticket) deja una entrada.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

GENESIS = "0" * 64


def _canonico(obj: Any) -> str:
    """Serialización determinista: sin esto la cadena no es verificable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def registrar(conn: sqlite3.Connection, actor: str, tipo: str, payload: dict) -> dict:
    """Agrega una entrada encadenada y la devuelve."""
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    entrada = {
        "decision_id": f"dec_{uuid.uuid4().hex[:20]}",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
        "tipo": tipo,
        "payload": payload,
        "prev_hash": prev,
    }
    entrada["hash"] = hashlib.sha256(_canonico(entrada).encode()).hexdigest()
    conn.execute(
        "INSERT INTO audit_log(decision_id, ts, actor, tipo, payload, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            entrada["decision_id"],
            entrada["ts"],
            actor,
            tipo,
            _canonico(payload),
            prev,
            entrada["hash"],
        ),
    )
    conn.commit()
    return entrada


def listar(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Entradas más recientes primero, con el payload ya deserializado."""
    filas = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    salida = []
    for f in filas:
        d = dict(f)
        try:
            d["payload"] = json.loads(f["payload"])
        except (ValueError, TypeError):
            d["payload"] = {}
        salida.append(d)
    return salida


def verificar(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Recalcula la cadena completa en orden. Devuelve (ok, motivo)."""
    filas = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
    prev = GENESIS
    for i, f in enumerate(filas):
        if f["prev_hash"] != prev:
            return False, f"entrada {i} ({f['decision_id']}): prev_hash no encadena"
        cuerpo = {
            "decision_id": f["decision_id"],
            "ts": f["ts"],
            "actor": f["actor"],
            "tipo": f["tipo"],
            "payload": json.loads(f["payload"]),
            "prev_hash": f["prev_hash"],
        }
        esperado = hashlib.sha256(_canonico(cuerpo).encode()).hexdigest()
        if esperado != f["hash"]:
            return False, f"entrada {i} ({f['decision_id']}): hash no coincide"
        prev = f["hash"]
    return True, f"cadena íntegra ({len(filas)} entradas)"
