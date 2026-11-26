"""Editor visual de la ROI (Region of Interest).

Permite ajustar la ROI desde los dashboards (web y de escritorio) con
botones / teclas, en lugar de editar números a mano en config.json:

  - Mover el rectángulo: izquierda, derecha, arriba, abajo
  - Cambiar tamaño: mas_grande, mas_pequena
  - mostrar: alterna la visualización del rectángulo amarillo en el video
  - aplicar: persiste los valores en vision/config.json
  - reset: vuelve a los valores iniciales de fábrica

La ROI se expresa en fracciones de la imagen (0.0 = borde izquierdo/superior,
1.0 = borde derecho/inferior), igual que en vision/config.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import actualizar_config

import cv2
import numpy as np

logger = logging.getLogger("EditorROI")

# Amarillo fuerte (formato BGR de OpenCV)
COLOR_ROI = (0, 255, 255)

ACCIONES_MOVER = ("izquierda", "derecha", "arriba", "abajo")
ACCIONES_TAMANO = ("mas_grande", "mas_pequena")

# Límites de cordura para no dejar la ROI inutilizable
MIN_TAMANO = 0.10  # la ROI no puede quedar menor al 10% de la imagen
VALORES_INICIALES = (0.15, 0.85, 0.35, 0.95)  # desde_x, hasta_x, desde_y, hasta_y


class EditorROI:
    """Edita la ROI de un ConfigCamara en memoria y la dibuja sobre el frame."""

    def __init__(self, cfg_camara, paso: float = 0.02):
        self.cfg = cfg_camara  # ConfigCamara (se edita en memoria)
        self.paso = float(paso)
        self.mostrar = False  # oculta por default; se activa con [i] / botón web

    # -- Edición ------------------------------------------------------------
    def mover(self, accion: str) -> tuple[bool, str]:
        """Aplica una acción de edición y devuelve (ok, mensaje para el usuario)."""
        accion = (accion or "").strip().lower()
        d = self.paso

        if accion in ACCIONES_MOVER:
            if accion == "izquierda":
                return self._desplazar(-d, 0)
            if accion == "derecha":
                return self._desplazar(+d, 0)
            if accion == "arriba":
                return self._desplazar(0, -d)
            return self._desplazar(0, +d)

        if accion in ACCIONES_TAMANO:
            s = d if accion == "mas_grande" else -d
            return self._escalar(s)

        if accion == "mostrar":
            self.mostrar = not self.mostrar
            estado = "visible" if self.mostrar else "oculta"
            return True, f"ROI {estado}."

        if accion == "reset":
            (self.cfg.roi_desde_x, self.cfg.roi_hasta_x,
             self.cfg.roi_desde_y, self.cfg.roi_hasta_y) = VALORES_INICIALES
            return True, "ROI restaurada a los valores iniciales."

        return False, f"Acción de ROI desconocida: {accion}"

    def _desplazar(self, dx: float, dy: float) -> tuple[bool, str]:
        """Mueve todo el rectángulo sin cambiar su tamaño."""
        x1, x2 = self.cfg.roi_desde_x + dx, self.cfg.roi_hasta_x + dx
        y1, y2 = self.cfg.roi_desde_y + dy, self.cfg.roi_hasta_y + dy
        if x1 < 0 or x2 > 1 or y1 < 0 or y2 > 1:
            return False, "La ROI ya está en el borde de la imagen."
        self.cfg.roi_desde_x, self.cfg.roi_hasta_x = x1, x2
        self.cfg.roi_desde_y, self.cfg.roi_hasta_y = y1, y2
        return True, self.texto()

    def _escalar(self, s: float) -> tuple[bool, str]:
        """Agranda (s>0) o achica (s<0) el rectángulo desde su centro."""
        x1, x2 = self.cfg.roi_desde_x - s, self.cfg.roi_hasta_x + s
        y1, y2 = self.cfg.roi_desde_y - s, self.cfg.roi_hasta_y + s
        # No permitir cruzar los bordes ni achicar de más
        if x1 < 0 or x2 > 1 or y1 < 0 or y2 > 1:
            return False, "No se puede agrandar más: la ROI tocaría el borde."
        if x2 - x1 < MIN_TAMANO or y2 - y1 < MIN_TAMANO:
            return False, "No se puede achicar más (mínimo 10% de la imagen)."
        self.cfg.roi_desde_x, self.cfg.roi_hasta_x = x1, x2
        self.cfg.roi_desde_y, self.cfg.roi_hasta_y = y1, y2
        return True, self.texto()

    # -- Persistencia -------------------------------------------------------
    def aplicar(self, config_path: str | Path = "vision/config.json") -> str:
        """Guarda los valores actuales de la ROI en config.json.

        Nota: se guarda en config.json (y no en config.py) porque es el archivo
        que el sistema lee al arrancar. config.py solo define los valores por
        defecto que se usan si config.json no existe.
        """
        try:
            ruta = Path(config_path)
            actualizar_config(ruta, {"camara": {
                "roi_desde_x": self.cfg.roi_desde_x,
                "roi_hasta_x": self.cfg.roi_hasta_x,
                "roi_desde_y": self.cfg.roi_desde_y,
                "roi_hasta_y": self.cfg.roi_hasta_y,
            }})
            logger.info("ROI guardada en %s: %s", ruta, self.texto())
            return f"ROI guardada en {ruta.name}: {self.texto()}"
        except Exception:
            logger.exception("No se pudo guardar la ROI en %s", config_path)
            return "Error: no se pudo guardar la ROI."

    # -- Dibujo y estado ----------------------------------------------------
    def dibujar(self, frame: np.ndarray) -> np.ndarray:
        """Dibuja el rectángulo de la ROI (amarillo fuerte) sobre el frame."""
        if not self.mostrar:
            return frame
        h, w = frame.shape[:2]
        x1 = int(w * self.cfg.roi_desde_x)
        x2 = int(w * self.cfg.roi_hasta_x)
        y1 = int(h * self.cfg.roi_desde_y)
        y2 = int(h * self.cfg.roi_hasta_y)
        cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_ROI, 2)
        cv2.putText(frame, "ROI", (x1 + 6, y1 + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_ROI, 2)
        return frame

    def texto(self) -> str:
        """Representación legible de la ROI, para mensajes y el HUD."""
        return (f"x {self.cfg.roi_desde_x:.2f}->{self.cfg.roi_hasta_x:.2f} | "
                f"y {self.cfg.roi_desde_y:.2f}->{self.cfg.roi_hasta_y:.2f}")

    def valores(self) -> dict:
        """Valores de la ROI (y del editor) para enviar al frontend."""
        return {
            "desde_x": self.cfg.roi_desde_x,
            "hasta_x": self.cfg.roi_hasta_x,
            "desde_y": self.cfg.roi_desde_y,
            "hasta_y": self.cfg.roi_hasta_y,
            "mostrar": self.mostrar,
            "texto": self.texto(),
        }
