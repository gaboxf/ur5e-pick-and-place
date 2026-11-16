"""Editor visual de la calibración de la homografía (píxel → UR5).

Permite calibrar los 4 puntos de referencia desde los dashboards (web y de
escritorio) haciendo clic sobre la imagen, en lugar de editar números a mano
en config.json:

  - iniciar: activa el modo calibración (espera 4 clics)
  - punto:   registra un clic (coordenadas de imagen completa, "x,y")
  - deshacer: quita el último punto registrado
  - cancelar: aborta la calibración sin guardar
  - aplicar:  persiste los 4 puntos en vision/config.json y recalibra
  - reset:    restaura los valores de fábrica

IMPORTANTE sobre los sistemas de coordenadas:
    El detector trabaja sobre el RECORTE de la ROI, por lo que el centroide
    que entra a la homografía es relativo a la ROI (origen = esquina
    superior-izquierda del recorte). Los `puntos_px` de la homografía deben
    estar en ese MISMO sistema. Este editor recibe los clics en coordenadas
    de imagen completa y los convierte automáticamente restando el offset
    de la ROI, de modo que el usuario siempre clica sobre el video completo.

Si la ROI se mueve o redimensiona después de calibrar, la calibración queda
invalidada y hay que repetirla (los mismos marcadores caen en otros píxeles
del recorte).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from .config import ConfigHomografia

logger = logging.getLogger("EditorHomografia")

# Cian (BGR) para los puntos de calibración sobre el video
COLOR_HOM = (255, 255, 0)

# Orden fijo de los 4 puntos (igual que config.py: TL, TR, BR, BL)
ORDEN_PUNTOS = ("sup-izq", "sup-der", "inf-der", "inf-izq")
NOMBRES_LARGOS = {
    "sup-izq": "superior izquierdo",
    "sup-der": "superior derecho",
    "inf-der": "inferior derecho",
    "inf-izq": "inferior izquierdo",
}


class EditorHomografia:
    """Recolecta clics y calibra los puntos_px de la homografía."""

    def __init__(self, cfg_hom: ConfigHomografia, cfg_cam, transformador=None):
        self.cfg = cfg_hom              # ConfigHomografia (se edita en memoria)
        self.cfg_cam = cfg_cam          # ConfigCamara, para el offset de la ROI
        self.transformador = transformador  # TransformadorCoordenadas (recalibrar)
        self.calibrando = False
        self.puntos: list[list[float]] = []  # ROI-relativos, en orden TL/TR/BR/BL
        # Tamaño real del último frame; los dashboards lo actualizan en cada
        # tick (la cámara puede no honrar el ancho/alto pedidos en la config)
        self.tamano_frame: tuple[int, int] = (
            cfg_cam.ancho if cfg_cam is not None else 1280,
            cfg_cam.alto if cfg_cam is not None else 720,
        )

    # -- Conversión de coordenadas ------------------------------------------
    def _offset_roi(self) -> tuple[int, int]:
        """Offset (x1, y1) de la esquina de la ROI sobre la imagen completa."""
        if self.cfg_cam is None:
            return 0, 0
        ancho, alto = self.tamano_frame
        return (int(ancho * self.cfg_cam.roi_desde_x),
                int(alto * self.cfg_cam.roi_desde_y))

    def _tamano_roi(self) -> tuple[int, int]:
        """Tamaño (ancho, alto) del recorte ROI en píxeles."""
        ancho, alto = self.tamano_frame
        return (int(ancho * (self.cfg_cam.roi_hasta_x - self.cfg_cam.roi_desde_x)),
                int(alto * (self.cfg_cam.roi_hasta_y - self.cfg_cam.roi_desde_y)))

    # -- Edición -------------------------------------------------------------
    def mover(self, accion: str, valor: str = "") -> tuple[bool, str]:
        """Aplica una acción del asistente y devuelve (ok, mensaje)."""
        accion = (accion or "").strip().lower()
        valor = (valor or "").strip()

        if accion == "iniciar":
            self.calibrando = True
            self.puntos = []
            return True, ("Calibración de homografía iniciada. Marcá los 4 "
                          "puntos en orden: sup-izq, sup-der, inf-der, inf-izq.")

        if accion == "cancelar":
            n = len(self.puntos)
            self.calibrando = False
            self.puntos = []
            return True, f"Calibración cancelada ({n} puntos descartados)."

        if accion == "punto":
            if not self.calibrando:
                return False, "La calibración no está activa (iniciá con 'Iniciar')."
            if len(self.puntos) >= 4:
                return False, "Ya hay 4 puntos: aplicá con 'Aplicar' o cancelá."
            try:
                x_full, y_full = (float(v) for v in valor.split(","))
            except ValueError:
                return False, f"Coordenadas inválidas: {valor!r}"
            x1, y1 = self._offset_roi()
            punto = [x_full - x1, y_full - y1]
            self.puntos.append(punto)
            faltan = 4 - len(self.puntos)
            if faltan:
                return True, (f"Punto {len(self.puntos)}/4 registrado "
                              f"({punto[0]:.0f}, {punto[1]:.0f}). "
                              f"Falta el {NOMBRES_LARGOS[ORDEN_PUNTOS[len(self.puntos)]]}.")
            return True, "4/4 puntos registrados. Apretá 'Aplicar' para guardar."

        if accion == "deshacer":
            if self.puntos:
                self.puntos.pop()
                n = len(self.puntos)
                if n:
                    return True, (f"Punto quitado. Falta el "
                                  f"{NOMBRES_LARGOS[ORDEN_PUNTOS[n]]}.")
                return True, "Punto quitado. Volvió a empezar."
            return False, "No hay puntos registrados para quitar."

        if accion == "reset":
            base = ConfigHomografia()
            self.cfg.puntos_px = [list(p) for p in base.puntos_px]
            self._recalibrar()
            return True, "Puntos_px restaurados a los valores de fábrica."

        return False, f"Acción de homografía desconocida: {accion}"

    # -- Persistencia --------------------------------------------------------
    def aplicar(self, config_path: str | Path = "vision/config.json") -> str:
        """Valida los 4 puntos, los guarda en config.json y recalibra."""
        if len(self.puntos) != 4:
            return (f"Error: se necesitan 4 puntos y hay {len(self.puntos)}. "
                    "Continuá marcando o cancelá.")

        ancho_roi, alto_roi = self._tamano_roi()
        puntos_ajustados = []
        for i, (x, y) in enumerate(self.puntos):
            # Tolerar clics en los bordes (hasta 40 px) y clampearlos suavemente al recorte
            x_c = max(0.0, min(float(ancho_roi - 1), float(x)))
            y_c = max(0.0, min(float(alto_roi - 1), float(y)))
            if abs(x - x_c) > 40.0 or abs(y - y_c) > 40.0:
                return (f"Error: el punto {i + 1} ({x:.0f}, {y:.0f}) queda fuera "
                        f"del recorte ROI ({ancho_roi}x{alto_roi}).")
            puntos_ajustados.append([x_c, y_c])

        self.puntos = puntos_ajustados
        self.cfg.puntos_px = [list(p) for p in self.puntos]
        self.calibrando = False
        self.puntos = []

        try:
            ruta = Path(config_path)
            data = json.loads(ruta.read_text(encoding="utf-8"))
            data.setdefault("homografia", {})["puntos_px"] = [
                [round(x, 1), round(y, 1)] for x, y in self.cfg.puntos_px
            ]
            ruta.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            logger.exception("No se pudo guardar la homografía en %s", config_path)
            return "Error: no se pudo guardar la homografía."

        if self._recalibrar():
            logger.info("Homografía guardada y recalibrada: %s", self.cfg.puntos_px)
            return ("Homografía calibrada y guardada. Verificá el error con una "
                    "pieza en una posición no calibrada.")
        return "Homografía guardada, pero no se pudo recalcular la matriz."

    def _recalibrar(self) -> bool:
        """Recalcula la matriz del transformador con los puntos actuales."""
        if self.transformador is None:
            return False
        return bool(self.transformador.recalibrar())

    # -- Dibujo --------------------------------------------------------------
    def dibujar(self, frame: np.ndarray) -> np.ndarray:
        """Dibuja los puntos marcados (y el cuadrilátero) sobre el frame completo."""
        x1, y1 = self._offset_roi()

        # Puntos ya guardados en la config (siempre visibles en modo calibración)
        if self.calibrando:
            guardados = [np.array([p[0] + x1, p[1] + y1], int)
                         for p in self.cfg.puntos_px]
            if len(guardados) == 4:
                cv2.polylines(frame, [np.array(guardados)], True, COLOR_HOM, 1)
                for i, p in enumerate(guardados):
                    cv2.drawMarker(frame, tuple(p), COLOR_HOM,
                                   cv2.MARKER_CROSS, 14, 1)
                    cv2.putText(frame, ORDEN_PUNTOS[i], (p[0] + 8, p[1] - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_HOM, 1)

        # Puntos recién clickeados (más gruesos, con número)
        for i, p in enumerate(self.puntos):
            pf = (int(p[0] + x1), int(p[1] + y1))
            cv2.circle(frame, pf, 6, COLOR_HOM, -1)
            cv2.putText(frame, f"{i + 1}", (pf[0] + 10, pf[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_HOM, 2)

        if self.calibrando:
            ancho, alto = frame.shape[1], frame.shape[0]
            proximo = (ORDEN_PUNTOS[len(self.puntos)]
                       if len(self.puntos) < 4 else "aplicar")
            cv2.rectangle(frame, (0, alto - 130), (ancho, alto - 90), (40, 40, 40), -1)
            cv2.putText(
                frame,
                f"HOMOGRAFIA: punto {min(len(self.puntos) + 1, 4)}/4 -> "
                f"{NOMBRES_LARGOS.get(proximo, proximo)}  "
                f"[Backspace] deshacer  [Enter] aplicar  [Esc] cancelar",
                (12, alto - 104),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_HOM, 1,
            )
        return frame

    # -- Estado --------------------------------------------------------------
    def estado(self) -> dict:
        """Estado del asistente para el frontend / HUD."""
        ancho_roi, alto_roi = self._tamano_roi()
        return {
            "calibrando": self.calibrando,
            "registrados": len(self.puntos),
            "proximo": (ORDEN_PUNTOS[len(self.puntos)]
                        if len(self.puntos) < 4 else "listo"),
            "puntos_px": [list(p) for p in self.cfg.puntos_px],
            "puntos_ur5": [list(p) for p in self.cfg.puntos_ur5],
            "roi_tamano": [ancho_roi, alto_roi],
        }
