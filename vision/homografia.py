"""Transformación píxel → plano de trabajo del UR5 mediante homografía.

Punto crítico del proyecto (indicador con 8 XP).

Estrategia:
    1. Definir 4 puntos de referencia visibles en la superficie.
    2. Calibrar la homografía con OpenCV (findHomography + RANSAC).
    3. Mapear el centroide de la pieza (píxels) a coordenadas del UR5 (metros).
    4. Verificar el error con piezas en posiciones no calibradas.
    5. Rechazar coordenadas que caigan fuera de la zona de trabajo autorizada.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import ConfigHomografia, ConfigVision

logger = logging.getLogger("TransformadorCoordenadas")


@dataclass
class CoordUR5:
    """Coordenadas transformadas en el plano de trabajo del UR5."""
    ok: bool = False
    x: float = 0.0
    y: float = 0.0
    motivo_fallo: str = ""


class TransformadorCoordenadas:
    """Aplica la homografía píxel -> plano del UR5.

    El resultado se expresa en el **sistema de trabajo**: si la config define
    un origen (Home como punto cero), las coordenadas de `puntos_ur5` y de la
    zona de trabajo se toman relativas a ese origen y acá se les suma para
    devolver la coordenada real de la base del robot.
    """

    def __init__(self, cfg: ConfigHomografia, zona_cfg: ConfigVision):
        self.cfg = cfg
        self.zona_cfg = zona_cfg
        # Origen de trabajo en coordenadas de base (0, 0 = base del robot)
        self.origen_x = float(getattr(zona_cfg, "origen_x", 0.0))
        self.origen_y = float(getattr(zona_cfg, "origen_y", 0.0))
        # Matriz de homografía 3x3 (se calcula al init)
        self.matriz_h: np.ndarray | None = None
        self._inicializar()
        if self.origen_x or self.origen_y:
            logger.info(
                "Origen de trabajo = Home: (%.3f, %.3f) m. Los puntos_ur5, "
                "zona_trabajo y destinos se miden desde ahí.",
                self.origen_x, self.origen_y,
            )
        else:
            logger.info("Origen de trabajo = base del robot (0, 0).")

    def _inicializar(self) -> None:
        """Calcula la homografía a partir de los puntos de referencia."""
        pts_px = np.array(self.cfg.puntos_px, dtype=np.float32)
        pts_ur5 = np.array(self.cfg.puntos_ur5, dtype=np.float32)

        if len(pts_px) != 4 or len(pts_ur5) != 4:
            logger.error("Se necesitan exactamente 4 puntos de referencia")
            return

        # findHomography con RANSAC para robustez
        self.matriz_h, _ = cv2.findHomography(pts_px, pts_ur5, cv2.RANSAC, 5.0)
        if self.matriz_h is None:
            logger.error("No se pudo calcular la homografía")
            return
        logger.debug("Homografía calculada:\n%s", self.matriz_h)

    def recalibrar(self) -> bool:
        """Recalcula la matriz con los puntos actuales (tras editarlos).

        La devuelve True si la matriz quedó usable. Se llama desde el editor
        de homografía al aplicar una nueva calibración, para no tener que
        reiniciar el pipeline.
        """
        self.matriz_h = None
        self._inicializar()
        if self.matriz_h is not None:
            logger.info("Homografía recalibrada con puntos_px=%s",
                        self.cfg.puntos_px)
            return True
        return False

    # -- Transformación -----------------------------------------------------
    def transformar(self, x_px: int, y_px: int) -> CoordUR5:
        """Convierte un punto de píxeles a coordenadas del UR5.

        La zona de seguridad se evalúa en el sistema de trabajo (el mismo de
        `puntos_ur5`), y recién después se suma el origen para devolver la
        coordenada de base que espera el robot.
        """
        if self.matriz_h is None:
            return CoordUR5(motivo_fallo="homografia no inicializada")

        # Aplicar homografía: [x', y', w']^T = H * [x, y, 1]^T
        punto = np.array([[[float(x_px), float(y_px)]]], dtype=np.float32)
        transformado = cv2.perspectiveTransform(punto, self.matriz_h)
        x_trabajo = float(transformado[0][0][0])
        y_trabajo = float(transformado[0][0][1])

        x_ur5 = x_trabajo + self.origen_x
        y_ur5 = y_trabajo + self.origen_y

        # Verificar que está dentro de la zona de trabajo autorizada
        if not self._en_zona(x_trabajo, y_trabajo):
            return CoordUR5(
                ok=False,
                x=x_ur5,
                y=y_ur5,
                motivo_fallo="pieza fuera de la zona de trabajo autorizada",
            )

        return CoordUR5(ok=True, x=x_ur5, y=y_ur5)

    def _en_zona(self, x: float, y: float) -> bool:
        """Comprueba que las coordenadas están dentro de los límites de seguridad.

        Se evalúa en el sistema de trabajo (relativo a Home si hay origen).
        """
        return (
            self.zona_cfg.zona_trabajo_min_x <= x <= self.zona_cfg.zona_trabajo_max_x
            and self.zona_cfg.zona_trabajo_min_y <= y <= self.zona_cfg.zona_trabajo_max_y
        )

    # -- Utilidades ---------------------------------------------------------
    def guardar(self, ruta: str | Path = "vision/homografia.json") -> None:
        """Guarda la matriz de homografía y los puntos de referencia en JSON."""
        if self.matriz_h is None:
            logger.error("No hay homografía para guardar")
            return
        p = Path(ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "matriz_h": self.matriz_h.tolist(),
            "puntos_px": self.cfg.puntos_px,
            "puntos_ur5": self.cfg.puntos_ur5,
        }
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Homografía guardada en %s", p)

    def cargar(self, ruta: str | Path = "vision/homografia.json") -> bool:
        """Carga una homografía previamente guardada."""
        p = Path(ruta)
        if not p.exists():
            logger.warning("No existe el archivo de homografía %s", p)
            return False
        with open(p, "r") as f:
            data = json.load(f)
        self.matriz_h = np.array(data["matriz_h"], dtype=np.float32)
        logger.info("Homografía cargada desde %s", p)
        return True

    def calcular_error_calibracion(self, puntos_prueba: list[tuple[int, int, float, float]]) -> float:
        """Calcula el error medio de transformación para puntos de prueba.
        puntos_prueba: lista de (x_px, y_px, x_ur5_real, y_ur5_real).
        """
        if self.matriz_h is None or not puntos_prueba:
            return 0.0
        errores = []
        for x_px, y_px, x_real, y_real in puntos_prueba:
            coord = self.transformar(x_px, y_px)
            if coord.ok:
                err = np.sqrt((coord.x - x_real) ** 2 + (coord.y - y_real) ** 2)
                errores.append(err)
        return float(np.mean(errores)) if errores else 0.0