"""Configuración compartida para el subsystem de Visión (Persona A).

Define parámetros de la cámara, segmentación HSV, homografía y zona de trabajo.
Todas las constantes que cruce módulos viven aquí: una sola fuente de verdad.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConfigCamara:
    """Parámetros de adquisición de imágenes."""
    indice_camara: int = 0
    ancho: int = 1280
    alto: int = 720
    fps: int = 30
    # ROI de interés: recorte de la zona de trabajo (fracción de la imagen)
    roi_desde_x: float = 0.15
    roi_hasta_x: float = 0.85
    roi_desde_y: float = 0.35
    roi_hasta_y: float = 0.95


@dataclass
class ConfigSegmentacion:
    """Parámetros de segmentación HSV por color."""
    # Categoría A — Rojo (cilindro)
    # El rojo envuelve el rango de matiz [0, 10] y [170, 180]
    rojo_bajo1: tuple[int, int, int] = (0, 50, 50)
    rojo_alto1: tuple[int, int, int] = (15, 255, 255)
    rojo_bajo2: tuple[int, int, int] = (165, 50, 50)
    rojo_alto2: tuple[int, int, int] = (180, 255, 255)

    # Categoría B — Azul (cubo)
    azul_bajo: tuple[int, int, int] = (100, 50, 50)
    azul_alto: tuple[int, int, int] = (135, 255, 255)

    # Categoría C — Verde
    verde_bajo: tuple[int, int, int] = (35, 50, 50)
    verde_alto: tuple[int, int, int] = (85, 255, 255)

    # Categoría D — Violeta
    violeta_bajo: tuple[int, int, int] = (140, 50, 50)
    violeta_alto: tuple[int, int, int] = (160, 255, 255)

    # Umbrales de area minima/maxima para aceptar un contorno como pieza (pixeles)
    area_minima: int = 500
    area_maxima: int = 50000

    # Kernel para operaciones morfológicas
    kernel_tamano: int = 5

    # Umbral de confianza minima para aceptar y dibujar una deteccion (0..1).
    # La confianza mide la solidez del contorno (area contorno / area bbox);
    # detecciones por debajo del umbral se descartan y NO se marcan en video.
    confianza_minima: float = 0.6


@dataclass
class ConfigHomografia:
    """Parámetros de la homografía píxel -> plano de trabajo del UR5.

    Los 4 puntos de referencia forman un rectángulo en el plano de trabajo.
    El orden es: top-left, top-right, bottom-right, bottom-left.

    IMPORTANTE: los puntos_px están en el sistema de coordenadas del RECORTE
    ROI (origen = esquina superior-izquierda del recorte), porque es el mismo
    sistema en el que el detector reporta el centroide. Los valores de
    fábrica corresponden a la ROI por defecto (0.15/0.85/0.35/0.95 sobre
    1280x720 -> recorte de 896x432). Si se cambia la ROI, hay que
    recalibrar (asistente de homografía en los dashboards).
    """
    # Coordenadas en píxeles (ROI-relativas) de los 4 puntos de referencia
    puntos_px: list[list[float]] = field(default_factory=lambda: [
        [224.0, 108.0],   # top-left
        [672.0, 108.0],   # top-right
        [672.0, 324.0],   # bottom-right
        [224.0, 324.0],   # bottom-left
    ])
    # Coordenadas en metros en el plano del UR5 (mismo orden)
    puntos_ur5: list[list[float]] = field(default_factory=lambda: [
        [0.10, 0.40],     # top-left
        [0.50, 0.40],     # top-right
        [0.50, 0.00],     # bottom-right
        [0.10, 0.00],     # bottom-left
    ])


@dataclass
class ConfigVision:
    """Configuración principal del subsystem de visión."""
    camara: ConfigCamara = field(default_factory=ConfigCamara)
    segmentacion: ConfigSegmentacion = field(default_factory=ConfigSegmentacion)
    homografia: ConfigHomografia = field(default_factory=ConfigHomografia)

    # Zona de trabajo permitida (en coords UR5, para rechazar detecciones fuera)
    zona_trabajo_min_x: float = 0.05
    zona_trabajo_max_x: float = 0.55
    zona_trabajo_min_y: float = -0.05
    zona_trabajo_max_y: float = 0.45


def load_config(path: str | Path = "vision/config.json") -> ConfigVision:
    """Carga la configuración desde un archivo JSON, si existe.
    Si no existe, devuelve la configuración por defecto.
    """
    p = Path(path)
    if not p.exists():
        return ConfigVision()
    with open(p, "r") as f:
        data = json.load(f)
    return ConfigVision(
        camara=ConfigCamara(**data.get("camara", {})),
        segmentacion=ConfigSegmentacion(**data.get("segmentacion", {})),
        homografia=ConfigHomografia(**data.get("homografia", {})),
    )


def save_config(cfg: ConfigVision, path: str | Path = "vision/config.json") -> None:
    """Guarda la configuración en un archivo JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "camara": cfg.camara.__dict__,
        "segmentacion": cfg.segmentacion.__dict__,
        "homografia": cfg.homografia.__dict__,
    }
    with open(p, "w") as f:
        json.dump(data, f, indent=2, default=list)