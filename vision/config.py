"""Configuración compartida para el subsystem de Visión (Persona A).

Define parámetros de la cámara, segmentación HSV, homografía y zona de trabajo.
Todas las constantes que cruce módulos viven aquí: una sola fuente de verdad.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
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


    def contexto_homografia(self, ancho: int, alto: int) -> list:
        """Identifica la cámara y el recorte usados para calibrar."""
        return [
            self.indice_camara, ancho, alto,
            self.roi_desde_x, self.roi_hasta_x,
            self.roi_desde_y, self.roi_hasta_y,
        ]


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
    contexto: list | None = None


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

    # Origen del sistema de trabajo. Con `usar_home_como_origen = true` los
    # valores de `puntos_ur5`, `zona_trabajo_*` y `destinos` se interpretan
    # como desplazamientos (dx, dy) desde Home en vez de coordenadas absolutas
    # de la base del robot. Así Home queda como "punto cero" del laboratorio.
    usar_home_como_origen: bool = False
    origen_x: float = 0.0
    origen_y: float = 0.0

    def fijar_origen_desde_home(self, home_pose) -> bool:
        """Toma la XY de Home como origen del sistema de trabajo.

        Devuelve True si home_pose es válida ([x, y, z, rx, ry, rz]); en caso
        contrario deja el origen en la base (0, 0).
        """
        try:
            self.origen_x = float(home_pose[0])
            self.origen_y = float(home_pose[1])
        except (TypeError, ValueError, IndexError, KeyError):
            self.origen_x = 0.0
            self.origen_y = 0.0
            return False
        return True


def load_config(path: str | Path = "vision/config.json") -> ConfigVision:
    """Carga la configuración desde un archivo JSON, si existe.
    Si no existe, devuelve la configuración por defecto.
    """
    p = Path(path)
    if not p.exists():
        return ConfigVision()
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    claves_hsv = {campo.name for campo in fields(ConfigSegmentacion)}
    cfg = ConfigVision(
        camara=ConfigCamara(**data.get("camara", {})),
        segmentacion=ConfigSegmentacion(**{
            clave: valor for clave, valor in data.get("segmentacion", {}).items()
            if clave in claves_hsv
        }),
        homografia=ConfigHomografia(**data.get("homografia", {})),
    )
    # Los límites de seguridad viven en el nivel raíz del JSON: se copian a
    # mano porque ConfigVision agrupa cámaras/segmentación/homografía.
    for clave in ("zona_trabajo_min_x", "zona_trabajo_max_x",
                  "zona_trabajo_min_y", "zona_trabajo_max_y"):
        if clave in data:
            setattr(cfg, clave, float(data[clave]))
    # Home como punto cero: el origen (dx, dy) sale de la misma home_pose que
    # usan los dashboards, así no hay dos fuentes de verdad que se desincronicen.
    if bool(data.get("usar_home_como_origen", False)):
        cfg.usar_home_como_origen = cfg.fijar_origen_desde_home(
            data.get("home_pose")
        )
    return cfg


@contextmanager
def _bloquear_config(path: Path):
    """Coordina escrituras del dashboard, los editores y las herramientas."""
    clave = hashlib.sha256(os.fsencode(os.path.normcase(str(path)))).hexdigest()
    bloqueo = Path(tempfile.gettempdir()) / f"ur5-config-{clave}.lock"
    with bloqueo.open("a+b") as archivo:
        if os.name == "nt":
            import msvcrt

            archivo.seek(0)
            msvcrt.locking(archivo.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                archivo.seek(0)
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(archivo, fcntl.LOCK_EX)
            yield


def _fusionar_config(data: dict, cambios: dict) -> None:
    for clave, valor in cambios.items():
        if isinstance(valor, dict) and isinstance(data.get(clave), dict):
            _fusionar_config(data[clave], valor)
        else:
            data[clave] = valor


def actualizar_config(path: str | Path, cambios: dict) -> dict:
    """Fusiona solo las claves editadas y reemplaza el JSON de forma atómica."""
    ruta = Path(path).resolve()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with _bloquear_config(ruta):
        data = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
        if "camara" in cambios and data:
            vieja = ConfigCamara(**data.get("camara", {}))
            nueva = ConfigCamara(**{**data.get("camara", {}), **cambios["camara"]})
            contexto = vieja.contexto_homografia(vieja.ancho, vieja.alto)
            if contexto != nueva.contexto_homografia(nueva.ancho, nueva.alto):
                hom = data.setdefault("homografia", {})
                if hom.get("contexto") is None:
                    hom["contexto"] = contexto
        _fusionar_config(data, cambios)
        temporal = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=ruta.parent, delete=False,
            ) as archivo:
                temporal = Path(archivo.name)
                json.dump(data, archivo, indent=2, ensure_ascii=False, allow_nan=False)
                archivo.write("\n")
            os.replace(temporal, ruta)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)
    return data


def save_config(cfg: ConfigVision, path: str | Path = "vision/config.json") -> None:
    """Guarda la sección de visión conservando los parámetros del robot."""
    actualizar_config(path, {
        "camara": vars(cfg.camara),
        "segmentacion": vars(cfg.segmentacion),
        "homografia": {k: v for k, v in vars(cfg.homografia).items() if v is not None},
    })
