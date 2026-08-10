"""Auditoría append-only encadenada por hash.

Cada entrada incluye el hash de la anterior. Reescribir una entrada del pasado
invalida toda la cadena posterior, lo que hace detectable la manipulación.

La cadena se duplica al plano de control en tiempo casi real, de modo que un
appliance comprometido no puede reescribir su propia historia sin que se note
(OWASP ASI10 — Rogue Agents).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def _canonico(obj: Any) -> str:
    """Serialización determinista: sin esta propiedad la cadena no es verificable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Auditoria:
    """Log append-only. En producción persiste en disco y se replica al control."""

    tenant: str
    ruta: Path | None = None
    _entradas: list[dict] = field(default_factory=list)

    # -- escritura -----------------------------------------------------------

    def registrar(self, tipo: str, payload: dict) -> dict:
        prev = self._entradas[-1]["hash"] if self._entradas else GENESIS
        entrada = {
            "decision_id": f"dec_{uuid.uuid4().hex[:20]}",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tenant": self.tenant,
            "tipo": tipo,
            **payload,
            "prev_hash": prev,
        }
        entrada["hash"] = hashlib.sha256(_canonico(entrada).encode()).hexdigest()
        self._entradas.append(entrada)
        if self.ruta:
            with self.ruta.open("a", encoding="utf-8") as f:
                f.write(_canonico(entrada) + "\n")
        return entrada

    def digest_evidencia(self, tool: str, args: dict, resultado: Any) -> dict:
        """Referencia compacta a una consulta de evidencia.

        Se guarda el digest, no el contenido: la evidencia cruda puede contener
        datos del cliente y no debe duplicarse fuera de su sistema de origen.
        """
        blob = _canonico({"tool": tool, "args": args, "resultado": resultado})
        return {
            "tool": tool,
            "args": args,
            "digest": "sha256:" + hashlib.sha256(blob.encode()).hexdigest(),
        }

    # -- verificación --------------------------------------------------------

    def verificar(self) -> tuple[bool, str]:
        """Recalcula la cadena completa. Devuelve (ok, motivo)."""
        prev = GENESIS
        for i, e in enumerate(self._entradas):
            if e["prev_hash"] != prev:
                return False, f"entrada {i} ({e['decision_id']}): prev_hash no encadena"
            cuerpo = {k: v for k, v in e.items() if k != "hash"}
            esperado = hashlib.sha256(_canonico(cuerpo).encode()).hexdigest()
            if esperado != e["hash"]:
                return False, f"entrada {i} ({e['decision_id']}): hash no coincide"
            prev = e["hash"]
        return True, f"cadena íntegra ({len(self._entradas)} entradas)"

    # -- lectura -------------------------------------------------------------

    def __iter__(self) -> Iterator[dict]:
        return iter(self._entradas)

    def __len__(self) -> int:
        return len(self._entradas)

    def ultima(self) -> dict | None:
        return self._entradas[-1] if self._entradas else None
