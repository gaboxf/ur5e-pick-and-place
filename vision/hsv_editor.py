"""Editor visual de los rangos HSV de segmentación.

Permite calibrar los colores (rojo, azul, verde, violeta) desde los
dashboards (web y de escritorio) con botones / teclas, en lugar de editar
números a mano en config.json:

  - color: elige el color a editar (rojo / azul / verde / violeta)
  - matiz_izquierda / matiz_derecha: desplaza la ventana de matiz (H)
  - mas_amplio / mas_estrecho: ensancha o angosta la ventana de matiz
  - mas_sensible / menos_sensible: baja o sube los mínimos de S y V
  - area_mas / area_menos: sube o baja el área mínima (ruido vs piezas chicas)
  - mostrar: alterna la máscara en vivo (pinta de color los píxeles detectados)
  - reset: restaura los valores de fábrica del color seleccionado
  - aplicar: persiste los valores en vision/config.json

La máscara en vivo es la clave didáctica: poné una pieza real en la mesa,
tocá los botones y mirá qué píxeles se pintan hasta que solo quede la pieza.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from .config import ConfigSegmentacion

logger = logging.getLogger("EditorHSV")

# Nombre de los atributos de ConfigSegmentacion por color (pares bajo/alto).
# El rojo tiene DOS ventanas porque su matiz "envuelve" el rango 0-180.
VENTANAS_COLOR: dict[str, tuple[str, ...]] = {
    "rojo": ("rojo_bajo1", "rojo_alto1", "rojo_bajo2", "rojo_alto2"),
    "azul": ("azul_bajo", "azul_alto"),
    "verde": ("verde_bajo", "verde_alto"),
    "violeta": ("violeta_bajo", "violeta_alto"),
}

# Letra de categoría y color BGR para pintar la máscara (igual que detector.py)
CAT_COLOR: dict[str, str] = {"rojo": "A", "azul": "B", "verde": "C", "violeta": "D"}
COLOR_BGRA: dict[str, tuple[int, int, int]] = {
    "A": (0, 0, 255),     # rojo
    "B": (255, 0, 0),     # azul
    "C": (0, 255, 0),     # verde
    "D": (255, 0, 255),   # violeta
}
COLOR_TEXTO: dict[str, tuple[int, int, int]] = {
    "rojo": (80, 80, 255),
    "azul": (255, 120, 60),
    "verde": (80, 220, 80),
    "violeta": (230, 130, 220),
}

ORDEN_COLORES = ("rojo", "azul", "verde", "violeta")

HUE_MAX = 180      # matiz en OpenCV: 0..179 (usamos 180 como tope exclusivo)
PASO_HUE = 3       # desplazamiento del matiz por pulsación
PASO_ANCHO = 3     # ensanchar/angostar por pulsación
PASO_SV = 15       # subida/bajada de S y V mínimos
PASO_AREA = 1.3    # factor de la área mínima por pulsación
AREA_MIN_LIMITE = 50
PASO_CONFIANZA = 0.05  # subida/bajada del umbral de confianza por pulsación


class EditorHSV:
    """Edita los rangos HSV de un ConfigSegmentacion en memoria."""

    def __init__(self, cfg_seg: ConfigSegmentacion, cfg_cam=None):
        self.cfg = cfg_seg
        self.cfg_cam = cfg_cam  # ConfigCamara, para saber dónde está la ROI
        self.color_actual = "rojo"
        self.mostrar = False  # apagada por default; se activa con [h] / botón web
        self.pixeles: dict[str, int] = {}

    # -- Accesos a las ventanas de matiz ------------------------------------
    def _leer_ventanas(self, color: str) -> list[list[float]]:
        """Devuelve las ventanas (bajo, alto) del color como listas mutables."""
        claves = VENTANAS_COLOR[color]
        return [[list(getattr(self.cfg, b)), list(getattr(self.cfg, a))]
                for b, a in zip(claves[0::2], claves[1::2])]

    def _escribir_ventanas(self, color: str, ventanas: list[list[float]]) -> None:
        """Guarda las ventanas en la config; las que sobran se desactivan [0,0]."""
        claves = VENTANAS_COLOR[color]
        n_pares = len(claves) // 2
        for i in range(n_pares):
            if i < len(ventanas):
                bajo, alto = ventanas[i]
            else:
                # Ventana desactivada: no captura nada (evita rangos fantasma)
                bajo, alto = [0, 0, 0], [0, 0, 0]
            setattr(self.cfg, claves[2 * i], bajo)
            setattr(self.cfg, claves[2 * i + 1], alto)

    # -- Edición ------------------------------------------------------------
    def mover(self, accion: str, valor: str = "") -> tuple[bool, str]:
        """Aplica una acción de edición y devuelve (ok, mensaje para el usuario)."""
        accion = (accion or "").strip().lower()
        valor = (valor or "").strip().lower()

        if accion == "color":
            if valor in ORDEN_COLORES:
                self.color_actual = valor
            else:
                # Cicla al siguiente color (útil con teclado en el dashboard)
                i = ORDEN_COLORES.index(self.color_actual)
                self.color_actual = ORDEN_COLORES[(i + 1) % len(ORDEN_COLORES)]
            return True, f"Color seleccionado: {self.color_actual}."

        color = self.color_actual
        ventanas = self._leer_ventanas(color)

        if accion == "matiz_izquierda":
            ventanas = self._desplazar_hue(ventanas, -PASO_HUE)
            res = f"Matiz {color}: {self._texto_de(ventanas)}"
        elif accion == "matiz_derecha":
            ventanas = self._desplazar_hue(ventanas, +PASO_HUE)
            res = f"Matiz {color}: {self._texto_de(ventanas)}"
        elif accion == "mas_amplio":
            ventanas = self._escalar_hue(ventanas, +PASO_ANCHO)
            res = f"Ventana {color}: {self._texto_de(ventanas)}"
        elif accion == "mas_estrecho":
            ventanas = self._escalar_hue(ventanas, -PASO_ANCHO)
            res = f"Ventana {color}: {self._texto_de(ventanas)}"
        elif accion == "mas_sensible":
            self._ajustar_sv(ventanas, -PASO_SV)
            res = f"{color.capitalize()} más sensible (S/V mín. bajados)."
        elif accion == "menos_sensible":
            self._ajustar_sv(ventanas, +PASO_SV)
            res = f"{color.capitalize()} menos sensible (S/V mín. subidos)."
        elif accion == "area_mas":
            res = self._ajustar_area(+PASO_AREA)
        elif accion == "area_menos":
            res = self._ajustar_area(1 / PASO_AREA)
        elif accion == "confianza_mas":
            res = self._ajustar_confianza(+PASO_CONFIANZA)
        elif accion == "confianza_menos":
            res = self._ajustar_confianza(-PASO_CONFIANZA)
        elif accion == "mostrar":
            self.mostrar = not self.mostrar
            self.pixeles = {}
            estado = "activada" if self.mostrar else "desactivada"
            return True, f"Máscara HSV {estado}."
        elif accion == "reset":
            base = ConfigSegmentacion()
            for clave in VENTANAS_COLOR[color]:
                setattr(self.cfg, clave, list(getattr(base, clave)))
            res = f"Rangos de {color} restaurados: {self.texto()}"
        else:
            return False, f"Acción HSV desconocida: {accion}"

        self._escribir_ventanas(color, ventanas)
        return True, res

    def _desplazar_hue(self, ventanas: list[list[float]],
                       d: int) -> list[list[float]]:
        """Desplaza el matiz de todas las ventanas d unidades (con wrap-around).

        Las ventanas mantienen el formato [[H_bajo, S, V], [H_alto, S, V]].
        Una ventana que cruza el borde 0/180 se parte en dos; luego se fusionan
        las ventanas superpuestas para volver al formato de la config.
        """
        partes: list[list] = []
        for bajo, alto in ventanas:
            if bajo == [0, 0, 0] and alto == [0, 0, 0]:
                continue  # las ventanas desactivadas no se tocan
            nb, na = (bajo[0] + d) % HUE_MAX, (alto[0] + d) % HUE_MAX
            if nb <= na:
                partes.append([nb, na, bajo[1:], alto[1:]])
            else:  # la ventana cruzó el borde: se parte en dos
                partes.append([nb, HUE_MAX - 1, bajo[1:], alto[1:]])
                partes.append([0, na, bajo[1:], alto[1:]])

        if not partes:
            return ventanas

        # Ordenar por matiz y fusionar solapamientos
        partes.sort(key=lambda p: p[0])
        fusion: list[list] = []
        for b, a, s_bajo, s_alto in partes:
            if fusion and b <= fusion[-1][1] + 1:
                if a > fusion[-1][1]:
                    fusion[-1][1] = a
                    fusion[-1][3] = s_alto
            else:
                fusion.append([b, a, s_bajo, s_alto])

        # El rojo admite 2 ventanas; el resto, 1. Si sobran, quedarse con las
        # más anchas (caso raro de desplazamientos grandes)
        max_ventanas = len(VENTANAS_COLOR[self.color_actual]) // 2
        if len(fusion) > max_ventanas:
            fusion = sorted(fusion, key=lambda p: p[1] - p[0])[-max_ventanas:]
            fusion.sort(key=lambda p: p[0])

        return [[[b, s_bajo[0], s_bajo[1]], [a, s_alto[0], s_alto[1]]]
                for b, a, s_bajo, s_alto in fusion]

    def _escalar_hue(self, ventanas: list[list[float]],
                     s: int) -> list[list[float]]:
        """Ensancha (s>0) o angosta (s<0) cada ventana de matiz."""
        salida: list[list[float]] = []
        for bajo, alto in ventanas:
            if bajo == [0, 0, 0] and alto == [0, 0, 0]:
                continue  # ventana desactivada no se reactiva al angostar
            nb = max(0, bajo[0] - s)
            na = min(HUE_MAX, alto[0] + s)
            if na - nb < 2:  # nunca dejar una ventana de ancho cero
                centro = (nb + na) // 2
                nb, na = max(0, centro - 1), min(HUE_MAX, centro + 1)
            salida.append([[nb, bajo[1], bajo[2]], [na, alto[1], alto[2]]])
        if not salida:  # todo desactivado: restaurar la primera ventana
            salida = [[[0, 120, 70], [15, 255, 255]]]
        return salida

    def _ajustar_sv(self, ventanas: list[list[float]], d: int) -> None:
        """Baja (d<0) o sube (d>0) los mínimos de S y V de todas las ventanas."""
        for bajo, _ in ventanas:
            bajo[1] = int(min(254, max(0, bajo[1] + d)))
            bajo[2] = int(min(254, max(0, bajo[2] + d)))

    def _ajustar_area(self, factor: float) -> str:
        """Multiplica el área mínima por factor, con límites de cordura."""
        nuevo = int(round(max(AREA_MIN_LIMITE, min(self.cfg.area_maxima - 100,
                                                   self.cfg.area_minima * factor))))
        self.cfg.area_minima = nuevo
        return f"Área mínima: {nuevo} px."

    def _ajustar_confianza(self, d: float) -> str:
        """Sube (d>0) o baja (d<0) el umbral de confianza, con límites 0..1."""
        nuevo = round(min(1.0, max(0.0, self.cfg.confianza_minima + d)), 2)
        self.cfg.confianza_minima = nuevo
        efecto = "menos ruido" if d > 0 else "más detecciones"
        return f"Umbral de confianza: {nuevo:.2f} ({efecto})"

    # -- Persistencia -------------------------------------------------------
    def aplicar(self, config_path: str | Path = "vision/config.json") -> str:
        """Guarda todos los rangos HSV y las áreas en config.json."""
        try:
            ruta = Path(config_path)
            data = json.loads(ruta.read_text(encoding="utf-8"))
            seg = data.setdefault("segmentacion", {})
            for claves in VENTANAS_COLOR.values():
                for clave in claves:
                    seg[clave] = list(getattr(self.cfg, clave))
            seg["area_minima"] = int(self.cfg.area_minima)
            seg["area_maxima"] = int(self.cfg.area_maxima)
            seg["confianza_minima"] = float(self.cfg.confianza_minima)
            ruta.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("HSV guardado en %s", ruta)
            return f"HSV guardado en {ruta.name}."
        except Exception:
            logger.exception("No se pudo guardar el HSV en %s", config_path)
            return "Error: no se pudo guardar el HSV."

    # -- Máscara en vivo ----------------------------------------------------
    def dibujar(self, frame: np.ndarray) -> np.ndarray:
        """Pinta sobre el frame los píxeles que cada máscara HSV está capturando.

        La máscara se calcula dentro de la ROI (la misma zona que usa el
        detector) y los píxeles capturados se tiñen con el color de su
        categoría, mezclados con la imagen original.
        """
        self.pixeles = {}
        if not self.mostrar:
            return frame

        if self.cfg_cam is not None:
            h, w = frame.shape[:2]
            x1 = int(w * self.cfg_cam.roi_desde_x)
            x2 = int(w * self.cfg_cam.roi_hasta_x)
            y1 = int(h * self.cfg_cam.roi_desde_y)
            y2 = int(h * self.cfg_cam.roi_hasta_y)
        else:
            x1, y1, x2, y2 = 0, 0, frame.shape[1], frame.shape[0]

        if x2 - x1 < 2 or y2 - y1 < 2:
            return frame

        crop = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        for color in ORDEN_COLORES:
            cat = CAT_COLOR[color]
            mask = np.zeros(crop.shape[:2], np.uint8)
            for bajo, alto in self._leer_ventanas(color):
                if bajo == [0, 0, 0] and alto == [0, 0, 0]:
                    continue
                mask |= cv2.inRange(hsv, np.array(bajo, np.uint8),
                                    np.array(alto, np.uint8))
            self.pixeles[cat] = int(cv2.countNonZero(mask))

            mezcla = (crop * 0.35 + np.array(COLOR_BGRA[cat], np.float32) * 0.65)
            sel = mask > 0
            crop[sel] = mezcla[sel].astype(np.uint8)

        frame[y1:y2, x1:x2] = crop
        return frame

    # -- Estado -------------------------------------------------------------
    def _texto_de(self, ventanas: list[list[float]]) -> str:
        """Formatea una lista de ventanas (bajo, alto) en texto legible ASCII."""
        activas = [v for v in ventanas
                   if not (v[0] == [0, 0, 0] and v[1] == [0, 0, 0])]
        if not activas:
            return "(sin ventanas activas)"
        hues = ", ".join(f"{v[0][0]:.0f}-{v[1][0]:.0f}" for v in activas)
        s = activas[0][0][1]
        v = activas[0][0][2]
        return f"H {hues} | S>={s:.0f} V>={v:.0f}"

    def texto(self) -> str:
        """Rangos del color seleccionado, en formato legible (solo ASCII)."""
        color = self.color_actual
        ventanas = self._leer_ventanas(color)
        return f"{color}: {self._texto_de(ventanas)}"

    def estado(self) -> dict:
        """Estado del editor para el frontend / HUD."""
        return {
            "color_actual": self.color_actual,
            "texto": self.texto(),
            "mostrar": self.mostrar,
            "pixeles": dict(self.pixeles),
            "area_minima": int(self.cfg.area_minima),
            "confianza_minima": float(self.cfg.confianza_minima),
        }
