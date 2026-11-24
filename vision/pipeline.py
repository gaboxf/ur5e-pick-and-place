"""Pipeline de Visión Artificial completo (Persona A).

Integra los módulos:
    1. Adquisición (visor.py)
    2. Detección de piezas (detector.py)
    3. Transformación de coordenadas (homografia.py)

Entrega un dict con el contrato definido en plan_trabajo_equipo_ur5.md §4.1:
{
    "ok": true|false,
    "categoria": "A"|"B",
    "x_px": int, "y_px": int,
    "x_ur5": float, "y_ur5": float,
    "conf": float,
    "motivo_fallo": str
}
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from .config import ConfigVision, load_config
from .detector import DetectorPiezas, DeteccionPieza
from .homografia import CoordUR5, TransformadorCoordenadas
from .visor import VisorCamara

logger = logging.getLogger("PipelineVision")


class PipelineVision:
    """Orquesta la captura, detección y transformación de coordenadas."""

    def __init__(self, config_path: str | Path = "vision/config.json"):
        self.cfg = load_config(config_path)
        cam = self.cfg.camara
        self._contexto_inicial = cam.contexto_homografia(cam.ancho, cam.alto)
        self.visor = VisorCamara(cam)
        self.detector = DetectorPiezas(self.cfg.segmentacion)
        self.transformador = TransformadorCoordenadas(
            self.cfg.homografia, self.cfg
        )

    # -- Inicialización -----------------------------------------------------
    def conectar_camara(self) -> bool:
        return self.visor.conectar()

    def conectar_imagen(self, ruta: str | Path) -> bool:
        return self.visor.cargar_imagen(ruta)

    def desconectar(self) -> None:
        self.visor.desconectar()

    def calibracion_vigente(self, ancho: int, alto: int) -> bool:
        """Comprueba que el frame corresponda a la calibración disponible."""
        contexto = self.cfg.homografia.contexto
        if contexto is None:
            contexto = self._contexto_inicial
        return (
            self.transformador.matriz_h is not None
            and contexto == self.cfg.camara.contexto_homografia(ancho, alto)
        )

    # -- Ejecución ----------------------------------------------------------
    def procesar_frame(self, frame_bgr: np.ndarray | None = None) -> dict:
        """Procesa un frame (o toma uno nuevo de la cámara) y devuelve el dict de detección."""
        if frame_bgr is None:
            frame_bgr = self.visor.leer()
        if frame_bgr is None:
            return {"ok": False, "estado_vision": "sin_frame", "motivo_fallo": "sin frame disponible"}

        alto, ancho = frame_bgr.shape[:2]
        if not self.calibracion_vigente(ancho, alto):
            return {
                "ok": False,
                "estado_vision": "calibracion_pendiente",
                "motivo_fallo": "Verificá la homografía para esta cámara, resolución y ROI.",
            }

        # Recortar ROI para working faster
        roi = self.visor.roi(frame_bgr)

        # Detectar pieza
        det = self.detector.procesar(roi)

        if not det.ok:
            return {
                "ok": False,
                "estado_vision": "sin_pieza",
                "motivo_fallo": det.motivo_fallo,
            }

        # Transformar a coordenadas del UR5
        coord = self.transformador.transformar(
            det.centro_x_px, det.centro_y_px
        )

        if not coord.ok:
            return {
                "ok": False,
                "categoria": det.categoria,
                "x_px": det.centro_x_px,
                "y_px": det.centro_y_px,
                "x_ur5": coord.x,
                "y_ur5": coord.y,
                "conf": det.confianza,
                "bbox": det.bbox,
                "estado_vision": "localizacion_invalida",
                "motivo_fallo": coord.motivo_fallo,
            }

        return {
            "ok": True,
            "estado_vision": "pieza_detectada",
            "categoria": det.categoria,
            "x_px": det.centro_x_px,
            "y_px": det.centro_y_px,
            "x_ur5": coord.x,
            "y_ur5": coord.y,
            "conf": det.confianza,
            "bbox": det.bbox,
        }

    # -- Dibujo -------------------------------------------------------------
    def dibujar(self, frame_bgr: np.ndarray, det: dict) -> np.ndarray:
        """Dibuja la detección sobre el frame para monitoreo."""
        roi = self.visor.roi(frame_bgr)
        # Reconstruir un DeteccionPieza mínimo para el dibujador
        det_temp = DeteccionPieza(
            ok=det.get("ok", False),
            categoria=det.get("categoria", ""),
            centro_x_px=det.get("x_px", 0),
            centro_y_px=det.get("y_px", 0),
            confianza=det.get("conf", 0.0),
            bbox=tuple(det["bbox"]) if det.get("bbox") is not None else None,
        )
        roi = self.detector.dibujar(roi, det_temp)
        # Pegar el ROI de vuelta en la posición original
        alto, ancho = frame_bgr.shape[:2]
        x1 = int(ancho * self.cfg.camara.roi_desde_x)
        x2 = int(ancho * self.cfg.camara.roi_hasta_x)
        y1 = int(alto * self.cfg.camara.roi_desde_y)
        y2 = int(alto * self.cfg.camara.roi_hasta_y)
        frame_bgr[y1:y2, x1:x2] = roi
        return frame_bgr


# -- Ejemplo de uso ---------------------------------------------------------
if __name__ == "__main__":
    import sys
    # Ejecución directa (python vision/pipeline.py): la raíz del proyecto
    # (un nivel arriba de vision/) debe estar en el path para importar "core".
    RAIZ = Path(__file__).resolve().parent.parent
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    from core.logging_config import configurar_logging
    configurar_logging("DEBUG")

    pipeline = PipelineVision()

    # Modo imagen estática (útil para depuración)
    if len(sys.argv) > 1:
        ruta_imagen = sys.argv[1]
        if not pipeline.conectar_imagen(ruta_imagen):
            sys.exit(1)
    else:
        if not pipeline.conectar_camara():
            logger.error("No se pudo conectar la cámara")
            sys.exit(1)

    # Procesar un frame
    frame = pipeline.visor.leer()
    if frame is None:
        logger.error("No se obtuvo frame")
        sys.exit(1)

    det = pipeline.procesar_frame(frame)
    print(json.dumps(det, indent=2, default=str))

    frame = pipeline.dibujar(frame, det)
    cv2.imshow("Vision", frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    pipeline.desconectar()