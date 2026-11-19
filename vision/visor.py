"""Adquisición de imágenes: captura desde la cámara fija (cenital).

La cámara se monta mirando hacia abajo sobre la zona de trabajo.
Se aplica un recorte (ROI) para descartar el fondo y ganar velocidad.
"""

from __future__ import annotations

import logging
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
        self.conectado = False
        self._es_imagen = False
        self._imagen_actual: np.ndarray | None = None

    # -- Conexión -----------------------------------------------------------
    def conectar(self) -> bool:
        """Abre la cámara configurada y confirma que entregue imagen real.

        `isOpened()` no alcanza: en Windows hay cámaras que "abren" pero nunca
        entregan un frame. Por eso se valida leyendo varios frames antes de
        dar la conexión por buena.

        IMPORTANTE: no se cambia de índice automáticamente. Otra cámara
        invalida la calibración de la homografía; si la configurada no anda,
        se informa y el operario elige con [k] (o "Escanear" en la web).
        """
        indice = int(self.cfg.indice_camara)
        self._es_imagen = False
        self._imagen_actual = None
        return self._abrir_indice(indice)

    def _abrir_indice(self, indice: int, intentos: int = 6) -> bool:
        """Abre un índice y valida que entregue al menos un frame.

        La primera apertura de algunas cámaras (webcam interna) puede tardar
        decenas de segundos; por eso se reintenta la lectura varias veces.
        """
        self._liberar()
        logger.info(
            "Abriendo cámara %d... (la primera vez puede tardar unos segundos)",
            indice,
        )
        # Usar CAP_DSHOW en Windows para mayor estabilidad; fallback a CAP_ANY
        cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.debug("Cámara %d: no abre con DirectShow, probando backend por defecto.", indice)
            cap.release()
            cap = cv2.VideoCapture(indice)
        if not cap.isOpened():
            cap.release()
            logger.error(
                "No se pudo abrir la cámara índice %d. Revisá que esté "
                "conectada y que otra aplicación no la esté usando.",
                indice,
            )
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.ancho)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.alto)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)

        for _ in range(max(1, intentos)):
            ok, frame = cap.read()
            if ok and frame is not None:
                self.cap = cap
                self.conectado = True
                alto, ancho = frame.shape[:2]
                logger.info("Cámara %d conectada (%dx%d).", indice, ancho, alto)
                return True

        cap.release()
        logger.error(
            "La cámara %d abrió pero no entregó imagen. Probá con [k] "
            "(dashboard) o el botón 'Escanear' (web) para elegir otra.",
            indice,
        )
        return False

    def _liberar(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.conectado = False

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
            frame = None
            if cap.isOpened():
                # Varios intentos: algunas cámaras tardan en entregar el
                # primer frame (y en una sola lectura se perderían).
                for _ in range(5):
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        break
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
        self._liberar()
        self._es_imagen = False

    # -- Lectura ------------------------------------------------------------
    def leer(self) -> np.ndarray | None:
        """Devuelve el siguiente frame BGR, o None si no hay."""
        if self._es_imagen:
            return self._imagen_actual.copy() if self._imagen_actual is not None else None
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