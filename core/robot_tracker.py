"""Rastreador de la posición del TCP del UR5 para visualización en vivo.

Mantiene un historial corto de poses ``(x, y, z)`` que los dashboards dibujan
como "mapa de trabajo": rastro de la trayectoria recorrida, posición actual y
destino. Es thread-safe: el hilo de muestreo (RobotController) escribe y los
hilos de UI/Flask leen sin bloquearse.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

# Umbral de velocidad (m/s) por debajo del cual se considera que el robot
# está quieto. 5 mm/s es un buen compromiso entre ruido de RTDE y movimiento
# real (el robot de la celda se mueve a ~0.25 m/s).
UMBRAL_QUIETO = 0.005


class RastreadorMovimiento:
    """Historial de poses del TCP con muestreo periódico y destino conocido."""

    def __init__(self, max_puntos: int = 600, max_segundos: float = 30.0):
        self._lock = threading.Lock()
        self._max_segundos = float(max_segundos)
        # Cada punto es (x, y, z, timestamp)
        self._trayectoria: deque[tuple[float, float, float, float]] = deque(
            maxlen=int(max_puntos)
        )
        self._pos: tuple[float, float, float] | None = None
        self._pos_t: float = 0.0
        self._destino: tuple[float, float, float] | None = None
        self._velocidad: float = 0.0
        self._ultima_actualizacion: float = 0.0

    # -- Escritura (hilo de muestreo) ---------------------------------------
    def actualizar(self, x: float, y: float, z: float) -> None:
        """Registra una nueva muestra de la pose del TCP."""
        try:
            x, y, z = float(x), float(y), float(z)
        except (TypeError, ValueError):
            return

        ahora = time.time()
        with self._lock:
            if self._pos is not None and self._pos_t > 0:
                dt = ahora - self._pos_t
                if dt > 1e-4:
                    dist = math.dist((x, y, z), self._pos)
                    v = dist / dt
                    # Suavizado exponencial: evita que el indicador vibre
                    self._velocidad = 0.65 * self._velocidad + 0.35 * v
            self._pos = (x, y, z)
            self._pos_t = ahora
            self._ultima_actualizacion = ahora
            self._trayectoria.append((x, y, z, ahora))
            self._purgar(ahora)

    def fijar_destino(self, x: float, y: float, z: float) -> None:
        """Fija la pose objetivo (se dibuja como punto verde en el mapa)."""
        try:
            self._destino = (float(x), float(y), float(z))
        except (TypeError, ValueError):
            self._destino = None

    def limpiar(self) -> None:
        """Borra el rastro recorrido (la posición actual se conserva)."""
        with self._lock:
            self._trayectoria.clear()

    # -- Lectura (hilos de UI) ----------------------------------------------
    def datos(self, max_puntos: int = 400, incluir_trayectoria: bool = True) -> dict:
        """Snapshot listo para serializar a JSON o dibujar.

        ``incluir_trayectoria=False`` devuelve solo posición/destino/velocidad
        (para el flujo SSE general, que no necesita el rastro completo).
        """
        ahora = time.time()
        with self._lock:
            trayectoria = []
            if incluir_trayectoria:
                crudo = list(self._trayectoria)[-max_puntos:]
                # Decimar: puntos a menos de 1 mm no aportan al dibujo (a la
                # escala del mapa son fracciones de píxel) y engordan el JSON.
                ultimo = None
                for p in crudo:
                    if ultimo is None or math.dist(p[:2], ultimo[:2]) >= 0.001:
                        trayectoria.append([round(p[0], 4), round(p[1], 4), round(p[2], 4)])
                        ultimo = p
                if crudo and (not trayectoria or math.dist(crudo[-1][:2], ultimo[:2]) > 1e-6):
                    trayectoria.append([round(crudo[-1][0], 4), round(crudo[-1][1], 4), round(crudo[-1][2], 4)])
            pos = (
                [round(self._pos[0], 4), round(self._pos[1], 4), round(self._pos[2], 4)]
                if self._pos
                else None
            )
            destino = (
                [round(self._destino[0], 4), round(self._destino[1], 4), round(self._destino[2], 4)]
                if self._destino
                else None
            )
            velocidad = round(self._velocidad, 4)
            edad = (ahora - self._ultima_actualizacion) if self._ultima_actualizacion else None

            moviendose = velocidad > UMBRAL_QUIETO
            dist_destino = None
            if pos and destino:
                dist_destino = math.dist(pos, destino)

            return {
                "pos": pos,
                "destino": destino,
                "trayectoria": trayectoria,
                "velocidad": velocidad,
                "moviendose": bool(moviendose),
                "dist_destino": None if dist_destino is None else round(dist_destino, 4),
                "edad_s": None if edad is None else round(edad, 2),
                "puntos": len(trayectoria),
            }

    # -- Interno ------------------------------------------------------------
    def _purgar(self, ahora: float) -> None:
        """Descarta muestras más viejas que la ventana configurada."""
        limite = ahora - self._max_segundos
        while self._trayectoria and self._trayectoria[0][3] < limite:
            self._trayectoria.popleft()
