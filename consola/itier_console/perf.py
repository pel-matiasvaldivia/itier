"""Medición de tiempos y utilidades de rendimiento.

Cada request acumula cuánto tardó hablando con GLPI, Zabbix y MeshCentral (contextvar) y lo expone en
la cabecera `Server-Timing` (DevTools → Network → Timing) y en el log si es lento. Así se distingue
«la app tarda» de «la red tarda». También: caché con vencimiento y ejecución en paralelo.
"""

from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

_acum: contextvars.ContextVar[dict | None] = contextvars.ContextVar("itier_perf", default=None)


def iniciar() -> dict:
    d: dict = {}
    _acum.set(d)
    return d


@contextmanager
def medir(sistema: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        d = _acum.get()
        if d is not None:
            x = d.setdefault(sistema, [0.0, 0])
            x[0] += time.perf_counter() - t0
            x[1] += 1


def server_timing(total_s: float, d: dict | None) -> str:
    partes = [f"app;dur={total_s * 1000:.0f}"]
    for k, (seg, n) in sorted((d or {}).items()):
        partes.append(f'{k};dur={seg * 1000:.0f};desc="{n} llamadas"')
    return ", ".join(partes)


def resumen(d: dict | None) -> str:
    return " ".join(f"{k}={seg * 1000:.0f}ms/{n}" for k, (seg, n) in sorted((d or {}).items()))


def paralelo(tareas: list, max_workers: int = 6) -> list[tuple[bool, object]]:
    """Corre las funciones sin argumentos en paralelo (propagando las mediciones del request).
    → [(True, resultado) | (False, excepción)] en el mismo orden."""
    if not tareas:
        return []

    def correr(f, ctx):
        try:
            return True, ctx.run(f)
        except Exception as e:  # noqa: BLE001
            return False, e

    ctx = contextvars.copy_context()
    if len(tareas) == 1:
        return [correr(tareas[0], ctx)]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tareas))) as ex:
        futs = [ex.submit(correr, f, ctx.copy()) for f in tareas]
        return [f.result() for f in futs]


class Cache:
    """Caché en memoria con vencimiento (seguro entre hilos)."""

    def __init__(self) -> None:
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, clave, ttl_s: float, fabricar):
        if ttl_s <= 0:
            return fabricar()
        ahora = time.monotonic()
        with self._lock:
            hit = self._d.get(clave)
            if hit and ahora - hit[0] < ttl_s:
                return hit[1]
        valor = fabricar()
        with self._lock:
            self._d[clave] = (time.monotonic(), valor)
        return valor

    def poner(self, clave, valor) -> None:
        with self._lock:
            self._d[clave] = (time.monotonic(), valor)

    def quitar(self, clave) -> None:
        with self._lock:
            self._d.pop(clave, None)

    def vaciar(self) -> None:
        with self._lock:
            self._d.clear()

    def fresco(self, clave, ttl_s: float):
        with self._lock:
            hit = self._d.get(clave)
            return hit[1] if hit and ttl_s > 0 and time.monotonic() - hit[0] < ttl_s else None
