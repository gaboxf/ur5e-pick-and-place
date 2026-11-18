"""Adquisición de imágenes: captura desde la cámara fija (cenital).

La cámara se monta mirando hacia abajo sobre la zona de trabajo.
Se aplica un recorte (ROI) para descartar el fondo y ganar velocidad.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from .config import ConfigCamara

logger = logging.getLogger("VisorCamara")


class VisorCamara:
    """Wrapper sobre cv2.VideoCapture con soporte para imágenes estáticas."""

    def __init__(self, cfg: ConfigCamara):
        self.cfg = cfg
        self.cap: cv2.VideoCapture | None = None
        self._es_imagen = False
        self._imagen_actual: np.ndarray | None = None
        # Serializa el acceso al VideoCapture: el dashboard de escritorio y el
        # web pueden leer la cámara desde hilos distintos (bucle de UI, ciclo
        # de pick & place, bucle de captura del servidor Flask).
        self._lock = threading.Lock()

    # -- Conexión -----------------------------------------------------------
    def conectar(self) -> bool:
        """Abre la cámara por índice utilizando DirectShow en Windows para evitar bloqueos."""
        with self._lock:
            # Usar CAP_DSHOW para mayor estabilidad en Windows, con fallback a CAP_ANY
            self.cap = cv2.VideoCapture(self.cfg.indice_camara, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                logger.warning("Fallo al abrir con DirectShow, intentando con backend por defecto...")
                self.cap = cv2.VideoCapture(self.cfg.indice_camara)

            if not self.cap.isOpened():
                logger.error("No se pudo abrir la cámara índice %s. Verifique que no esté en uso.", self.cfg.indice_camara)
                return False

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.ancho)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.alto)
            self.cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        logger.info("Cámara conectada correctamente.")
        return True

    @staticmethod
    def abrir_disponibles(max_indice: int = 9) -> dict[int, cv2.VideoCapture]:
        """Sondea los índices 0..max_indice y devuelve {índice: VideoCapture abierto}.

        Solo se consideran disponibles los índices que responden con un frame
        válido. Los captures devueltos quedan abiertos: el llamador debe
        liberarlos (release) cuando termine de usarlos.
        """
        encontrados: dict[int, cv2.VideoCapture] = {}
        for i in range(max_indice + 1):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            ok = False
            if cap.isOpened():
                ok, frame = cap.read()
                ok = ok and frame is not None
            if ok:
                encontrados[i] = cap
                logger.info("Cámara detectada en índice %d.", i)
            else:
                cap.release()
        return encontrados

    def cambiar_indice(self, indice: int) -> None:
        """Cambia el índice de cámara en la configuración en memoria (sin reconectar)."""
        self.cfg.indice_camara = int(indice)

    def cargar_imagen(self, ruta: str | Path) -> bool:
        """Carga una imagen estática como fuente de entrada."""
        img = cv2.imread(str(ruta))
        if img is None:
            logger.error("No se pudo leer la imagen %s", ruta)
            return False
        self._imagen_actual = img
        self._es_imagen = True
        logger.info("Imagen cargada: %s", ruta)
        return True

    def desconectar(self) -> None:
        with self._lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None

    # -- Lectura ------------------------------------------------------------
    def leer(self) -> np.ndarray | None:
        """Devuelve el siguiente frame BGR, o None si no hay."""
        if self._es_imagen:
            return self._imagen_actual.copy() if self._imagen_actual is not None else None
        with self._lock:
            if self.cap is None or not self.cap.isOpened():
                return None
            ok, frame = self.cap.read()
        return frame if ok else None

    def roi(self, frame: np.ndarray) -> np.ndarray:
        """Recorta la zona de interés (ROI) definida en la config."""
        alto, ancho = frame.shape[:2]
        x1 = int(ancho * self.cfg.roi_desde_x)
        x2 = int(ancho * self.cfg.roi_hasta_x)
        y1 = int(alto * self.cfg.roi_desde_y)
        y2 = int(alto * self.cfg.roi_hasta_y)
        return frame[y1:y2, x1:x2]