"""Detección y clasificación de piezas mediante segmentación HSV.

Categorías:
    A — Rojo (cilindro)
    B — Azul (cubo)

Pipeline por frame:
    1. Captura BGR
    2. Recorte ROI
    3. Conversión a HSV
    4. Máscara por color (rojo tiene dos rangos por el wrap-around del matiz)
    5. Operaciones morfológicas (apertura para ruido)
    6. Búsqueda de contornos
    7. Filtro por área y selección del contorno mayor
    8. Cálculo del centroide (píxeles)
    9. Dibujo: recuadro y contorno con el color de la categoría detectada
       (A=rojo, B=azul, C=verde, D=violeta)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .config import ConfigSegmentacion

logger = logging.getLogger("DetectorPiezas")

# Color (BGR) del recuadro/etiqueta según la categoría detectada
COLORES_CATEGORIA: dict[str, tuple[int, int, int]] = {
    "A": (0, 0, 255),      # Rojo (cilindro)
    "B": (255, 0, 0),      # Azul (cubo)
    "C": (0, 255, 0),      # Verde
    "D": (255, 0, 255),    # Violeta
}
COLOR_DESCONOCIDO: tuple[int, int, int] = (255, 255, 255)


@dataclass
class DeteccionPieza:
    """Resultado de la detección de una pieza."""
    ok: bool = False
    categoria: str = ""
    centro_x_px: int = 0
    centro_y_px: int = 0
    area_px: float = 0.0
    confianza: float = 0.0
    contorno: np.ndarray | None = None
    bbox: tuple[int, int, int, int] | None = None  # (x, y, w, h) del recuadro
    mascara: np.ndarray | None = None
    motivo_fallo: str = ""


class DetectorPiezas:
    """Segmenta piezas por color y calcula su centroide en píxeles."""

    def __init__(self, cfg: ConfigSegmentacion):
        self.cfg = cfg
        kernel = max(1, int(self.cfg.kernel_tamano))
        if kernel % 2 == 0:
            kernel += 1
        self._kernel = np.ones((kernel, kernel), np.uint8)

    # -- Máscaras -----------------------------------------------------------
    def _mascara_rojo(self, hsv: np.ndarray) -> np.ndarray:
        m1 = cv2.inRange(hsv, np.array(self.cfg.rojo_bajo1, np.uint8),
                         np.array(self.cfg.rojo_alto1, np.uint8))
        m2 = cv2.inRange(hsv, np.array(self.cfg.rojo_bajo2, np.uint8),
                         np.array(self.cfg.rojo_alto2, np.uint8))
        return cv2.bitwise_or(m1, m2)

    def _mascara_azul(self, hsv: np.ndarray) -> np.ndarray:
        return cv2.inRange(hsv, np.array(self.cfg.azul_bajo, np.uint8),
                           np.array(self.cfg.azul_alto, np.uint8))

    def _mascara_verde(self, hsv: np.ndarray) -> np.ndarray:
        return cv2.inRange(hsv, np.array(self.cfg.verde_bajo, np.uint8),
                           np.array(self.cfg.verde_alto, np.uint8))

    def _mascara_violeta(self, hsv: np.ndarray) -> np.ndarray:
        return cv2.inRange(hsv, np.array(self.cfg.violeta_bajo, np.uint8),
                           np.array(self.cfg.violeta_alto, np.uint8))

    # -- Procesamiento ------------------------------------------------------
    def procesar(self, frame_bgr: np.ndarray) -> DeteccionPieza:
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        # Evaluar ambos colores y quedarnos con el que dé mejor contorno
        candidatos: list[tuple[float, str, np.ndarray, np.ndarray]] = []

        for categoria, mascara_bruta in (
            ("A", self._mascara_rojo(hsv)),
            ("B", self._mascara_azul(hsv)),
            ("C", self._mascara_verde(hsv)),
            ("D", self._mascara_violeta(hsv)),
        ):
            # Limpiar la máscara
            masc = cv2.morphologyEx(mascara_bruta, cv2.MORPH_OPEN, self._kernel)
            # Cierre para llenar huecos internos
            masc = cv2.morphologyEx(masc, cv2.MORPH_CLOSE, self._kernel)

            contornos, _ = cv2.findContours(
                masc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contornos:
                continue

            # Elegir el contorno de mayor área
            contorno = max(contornos, key=cv2.contourArea)
            area = float(cv2.contourArea(contorno))

            if area < self.cfg.area_minima:
                continue
            if area > self.cfg.area_maxima:
                continue

            # Calcular confianza simple basada en la solidez del contorno
            x, y, w, h = cv2.boundingRect(contorno)
            area_bbox = w * h
            solidez = area / area_bbox if area_bbox > 0 else 0.0
            confianza = min(1.0, solidez)

            # Ruido: descartar contornos poco solidos (formas incompletas,
            # reflejos parciales, mezcla de piezas en la misma mascara)
            if confianza < self.cfg.confianza_minima:
                logger.debug(
                    "Categoria %s descartada: confianza %.2f < %.2f",
                    categoria, confianza, self.cfg.confianza_minima,
                )
                continue

            candidatos.append((area, categoria, contorno, masc))

        if not candidatos:
            return DeteccionPieza(
                motivo_fallo=(f"sin pieza con confianza >= "
                              f"{self.cfg.confianza_minima:.2f}")
            )

        # Elegir el candidato con mayor área (si hay conflicto, prioriza rojo)
        area, categoria, contorno, masc = max(candidatos, key=lambda c: c[0])

        momentos = cv2.moments(contorno)
        if momentos["m00"] == 0:
            x, y, w, h = cv2.boundingRect(contorno)
            cx = x + w // 2
            cy = y + h // 2
        else:
            cx = int(momentos["m10"] / momentos["m00"])
            cy = int(momentos["m01"] / momentos["m00"])

        return DeteccionPieza(
            ok=True,
            categoria=categoria,
            centro_x_px=cx,
            centro_y_px=cy,
            area_px=area,
            confianza=confianza,
            contorno=contorno,
            bbox=tuple(cv2.boundingRect(contorno)),
            mascara=masc,
        )

    # -- Dibujo -------------------------------------------------------------
    def dibujar(self, frame_bgr: np.ndarray, det: DeteccionPieza) -> np.ndarray:
        alto, ancho = frame_bgr.shape[:2]
        color = COLORES_CATEGORIA.get(det.categoria, COLOR_DESCONOCIDO)

        # Solo marcar detecciones por encima del umbral de confianza; los
        # contornos de ruido no se dibujan (det.ok es False cuando la pieza
        # no pasa el filtro de confianza, y el umbral cubre reconstrucciones
        # desde dict donde ok=False por fallo de coordenadas).
        marca = det.confianza >= self.cfg.confianza_minima
        if marca:
            if det.contorno is not None:
                cv2.drawContours(frame_bgr, [det.contorno], -1, color, 2)

            # Recuadro alrededor del objeto detectado, con el color de la categoría
            if det.bbox is not None:
                bx, by, bw, bh = det.bbox
                p1 = (max(0, int(bx)), max(0, int(by)))
                p2 = (min(ancho - 1, int(bx + bw)), min(alto - 1, int(by + bh)))
                cv2.rectangle(frame_bgr, p1, p2, color, 2)

            cv2.circle(frame_bgr, (det.centro_x_px, det.centro_y_px),
                       6, (0, 255, 0), -1)
            etiqueta = f"{det.categoria} {det.confianza:.2f}"
            cv2.putText(frame_bgr, etiqueta,
                        (det.centro_x_px + 10, det.centro_y_px - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return frame_bgr