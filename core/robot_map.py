"""Panel lateral "Mapa de trabajo" para el dashboard de escritorio (OpenCV).

Dibuja, al costado del video, una vista cenital (X-Y) a escala real del área
de trabajo del UR5 con:

  * retícula de coordenadas y límites de seguridad (``zona_trabajo_*``),
  * rastro de la trayectoria recorrida (con degradado de antigüedad),
  * posición actual del TCP y destino (línea punteada entre ambos),
  * piezas detectadas por visión (mismo color que su categoría),
  * perfil de altura Z con marcas de ``z_seguro`` / ``z_pick`` / ``z_deposit``.

La escala es la misma en ambos ejes (metros por píxel), por lo que las
distancias del mapa son proporcionales a las reales.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# -- Paleta (BGR) coherente con el dashboard web ----------------------------
FONDO = (30, 28, 26)
PANEL = (42, 40, 38)
REGLA = (66, 62, 58)
TEXTO = (238, 236, 232)
TEXTO_2 = (150, 146, 140)
ACENTO = (225, 215, 120)
OK = (110, 205, 95)
WARN = (90, 190, 235)
BAD = (80, 80, 225)

COLOR_CATEGORIA = {
    "A": (60, 60, 235),    # rojo
    "B": (235, 130, 55),   # azul
}

# -- Layout -----------------------------------------------------------------
HEADER_H = 52
MARGEN = 14


def dibujar_panel_robot(
    alto: int,
    datos: dict | None,
    cfg: dict | None = None,
    estado: str = "",
    det: dict | None = None,
    ancho: int = 360,
) -> np.ndarray:
    """Devuelve una imagen ``alto x ancho`` con el panel de instrumentos."""
    datos = datos or {}
    cfg = cfg or {}
    det = det or {}

    ancho = max(int(ancho), 240)
    alto = max(int(alto), 320)

    panel = np.full((alto, ancho, 3), FONDO, dtype=np.uint8)

    _cabecera(panel, estado, datos)

    zona = _zona_trabajo(cfg)
    mapa_w = ancho - 2 * MARGEN
    mapa_h = max(150, min(mapa_w, alto - HEADER_H - 250))
    mapa_y0 = HEADER_H + 12

    _mapa(panel, MARGEN, mapa_y0, mapa_w, mapa_h, zona, datos, det)

    y = mapa_y0 + mapa_h + 18
    y = _perfil_z(panel, MARGEN, y, mapa_w, cfg, datos)

    y = _telemetria(panel, MARGEN, y + 10, mapa_w, datos, estado)

    if y + 34 <= alto:
        _leyenda(panel, MARGEN, alto - 30, mapa_w)

    return panel


# ---------------------------------------------------------------- cabecera
def _cabecera(panel: np.ndarray, estado: str, datos: dict) -> None:
    ancho = panel.shape[1]
    cv2.rectangle(panel, (0, 0), (ancho, HEADER_H), PANEL, -1)
    cv2.line(panel, (0, HEADER_H), (ancho, HEADER_H), REGLA, 1)
    cv2.rectangle(panel, (0, 0), (4, HEADER_H), ACENTO, -1)

    _texto(panel, "MAPA DE TRABAJO", (MARGEN, 22), TEXTO, 0.52, 1)
    _texto(panel, "TCP UR5 · vista cenital X-Y · escala real", (MARGEN, 41), TEXTO_2, 0.36, 1)

    # Chip de estado (derecha)
    edad = datos.get("edad_s")
    if edad is None:
        chip_txt, chip_col = "SIN DATOS", TEXTO_2
    elif edad > 2.0:
        chip_txt, chip_col = "SIN TELEMETRIA", BAD
    elif datos.get("moviendose"):
        chip_txt, chip_col = "MOVIENDO", WARN
    else:
        chip_txt, chip_col = "QUIETO", OK

    (tw, _), _ = cv2.getTextSize(chip_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    x1 = ancho - MARGEN
    x0 = x1 - tw - 14
    cv2.rectangle(panel, (x0, 12), (x1, 32), chip_col, 1)
    _texto(panel, chip_txt, (x0 + 7, 27), chip_col, 0.38, 1)


# ------------------------------------------------------------------- mapa
def _mapa(
    panel: np.ndarray,
    x0: int,
    y0: int,
    w: int,
    h: int,
    zona: dict,
    datos: dict,
    det: dict,
) -> None:
    cv2.rectangle(panel, (x0, y0), (x0 + w, y0 + h), PANEL, -1)
    cv2.rectangle(panel, (x0, y0), (x0 + w, y0 + h), REGLA, 1)

    esc, cx, cy = _escala(_limites(zona, datos, det), w, h)

    def a_px(x: float, y: float) -> tuple[int, int]:
        return (
            int(round(x0 + w / 2.0 + (x - cx) * esc)),
            int(round(y0 + h / 2.0 - (y - cy) * esc)),
        )

    rect = (x0 + 1, y0 + 1, w - 2, h - 2)
    _reticula(panel, rect, zona, esc, cx, cy, a_px)

    # Zona de trabajo autorizada (punteada)
    p1 = a_px(zona["min_x"], zona["max_y"])
    p2 = a_px(zona["max_x"], zona["min_y"])
    _rect_punteado(panel, p1, p2, _tenue(ACENTO, 0.55), 1, 6)

    # Rastro
    tray = datos.get("trayectoria") or []
    n = len(tray)
    for i in range(1, n):
        t = i / max(1, n - 1)
        color = (
            int(55 + (60 - 55) * t),
            int(65 + (150 - 65) * t),
            int(80 + (245 - 80) * t),
        )
        grosor = 3 if i > n - 12 else 2
        q1 = a_px(tray[i - 1][0], tray[i - 1][1])
        q2 = a_px(tray[i][0], tray[i][1])
        # Muestras casi coincidentes (robot quieto) dibujan un punto azul
        # pegado al borde del TCP que parece un segundo marcador.
        if math.hypot(q2[0] - q1[0], q2[1] - q1[1]) < 2:
            continue
        _linea_recortada(panel, rect, q1, q2, color, grosor)

    pos = datos.get("pos")
    destino = datos.get("destino")

    # Línea al destino (punteada verde tenue)
    if pos and destino:
        _linea_punteada(panel, a_px(pos[0], pos[1]), a_px(destino[0], destino[1]),
                        _tenue(OK, 0.5), 1, 7)

    # Destino
    if destino:
        px = a_px(destino[0], destino[1])
        if _dentro(rect, px):
            cv2.drawMarker(panel, px, OK, cv2.MARKER_TILTED_CROSS, 12, 2, cv2.LINE_AA)
            _anillo(panel, px, 7, _tenue(OK, 0.6), 1)

    # Pieza detectada (coordenada de visión)
    if det.get("ok") and det.get("x_ur5") is not None:
        cat = det.get("categoria", "A")
        px = a_px(float(det["x_ur5"]), float(det["y_ur5"]))
        if _dentro(rect, px):
            col = COLOR_CATEGORIA.get(cat, TEXTO)
            cv2.rectangle(panel, (px[0] - 5, px[1] - 5), (px[0] + 5, px[1] + 5), col, -1)
            cv2.putText(panel, str(cat), (px[0] + 8, px[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)

    # Posición actual (blanca, con halo)
    if pos:
        px = a_px(pos[0], pos[1])
        if _dentro(rect, px):
            # Fondo opaco bajo el marcador: evita que el rastro asome en el borde.
            cv2.circle(panel, px, 7, FONDO, -1, cv2.LINE_AA)
            _anillo(panel, px, 9, (120, 120, 120), 1)
            cv2.circle(panel, px, 5, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.putText(panel, "TCP", (px[0] + 9, px[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.34, TEXTO, 1, cv2.LINE_AA)

    # Origen de coordenadas
    origen = a_px(0.0, 0.0)
    if _dentro(rect, origen):
        cv2.drawMarker(panel, origen, _tenue(TEXTO_2, 0.7), cv2.MARKER_CROSS, 9, 1, cv2.LINE_AA)


def _reticula(panel, rect, zona, esc, cx, cy, a_px) -> None:
    x_ini, y_ini, w, h = rect
    paso = _paso_grilla(esc)

    # Rango visible del mundo
    x0_w = cx - (w / 2.0) / esc
    x1_w = cx + (w / 2.0) / esc
    y0_w = cy - (h / 2.0) / esc
    y1_w = cy + (h / 2.0) / esc

    gx = math.floor(x0_w / paso) * paso
    while gx <= x1_w:
        p1 = a_px(gx, y0_w)
        p2 = a_px(gx, y1_w)
        _linea_recortada(panel, rect, p1, p2, _tenue(REGLA, 0.85), 1)
        # Evitar que las etiquetas de X choquen con las de Y en el borde izquierdo
        if p1[0] > x_ini + 24:
            etiqueta = f"{gx:.2f}".rstrip("0").rstrip(".")
            _texto(panel, etiqueta, (p1[0] + 2, y_ini + h - 18), _tenue(TEXTO_2, 0.85), 0.3, 1)
        gx = round(gx + paso, 6)

    gy = math.floor(y0_w / paso) * paso
    while gy <= y1_w:
        p1 = a_px(x0_w, gy)
        p2 = a_px(x1_w, gy)
        _linea_recortada(panel, rect, p1, p2, _tenue(REGLA, 0.85), 1)
        # Evitar etiquetas de Y sobre el borde inferior (zona de las de X)
        if p1[1] < y_ini + h - 26:
            etiqueta = f"{gy:.2f}".rstrip("0").rstrip(".")
            _texto(panel, etiqueta, (x_ini + 4, p1[1] - 4), _tenue(TEXTO_2, 0.85), 0.3, 1)
        gy = round(gy + paso, 6)


# ------------------------------------------------------------- perfil Z
def _perfil_z(panel, x0: int, y0: int, w: int, cfg: dict, datos: dict) -> int:
    z_seguro = float(cfg.get("z_seguro", 0.25))
    z_pick = float(cfg.get("z_pick", 0.12))
    z_dep = float(cfg.get("z_deposit", z_pick))
    pos = datos.get("pos")
    z_actual = float(pos[2]) if pos else 0.0
    z_max = max(z_seguro, z_pick, z_actual, 0.05) * 1.18

    _texto(panel, "ALTURA Z", (x0, y0 + 10), TEXTO_2, 0.36, 1)
    z_txt = f"{z_actual * 1000:.0f} mm"
    (tw, _), _ = cv2.getTextSize(z_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    _texto(panel, z_txt, (x0 + w - tw, y0 + 11), TEXTO, 0.5, 1)

    by = y0 + 26
    cv2.rectangle(panel, (x0, by), (x0 + w, by + 13), PANEL, -1)
    cv2.rectangle(panel, (x0, by), (x0 + w, by + 13), REGLA, 1)

    def zx(z: float) -> int:
        return int(round(x0 + w * min(max(z / z_max, 0.0), 1.0)))

    # Relleno de altura actual
    if z_actual > 0:
        cv2.rectangle(panel, (x0 + 1, by + 1), (zx(z_actual), by + 12), _tenue(ACENTO, 0.75), -1)

    # Marcas de referencia
    for z, nombre, color in (
        (z_pick, "pick", WARN),
        (z_dep, "dep", WARN),
        (z_seguro, "seg", ACENTO),
    ):
        if z <= z_max:
            xm = zx(z)
            cv2.line(panel, (xm, by - 4), (xm, by + 17), color, 1)
            (tw2, _), _ = cv2.getTextSize(nombre, cv2.FONT_HERSHEY_SIMPLEX, 0.3, 1)
            _texto(panel, nombre, (min(max(xm - tw2 // 2, x0), x0 + w - tw2), by + 29),
                   _tenue(color, 0.85), 0.3, 1)

    return by + 36


# ------------------------------------------------------------- telemetría
def _telemetria(panel, x0: int, y0: int, w: int, datos: dict, estado: str) -> int:
    pos = datos.get("pos") or [None, None, None]
    filas = [
        ("X", _fmt_m(pos[0]) + " m"),
        ("Y", _fmt_m(pos[1]) + " m"),
        ("Z", _fmt_m(pos[2]) + " m"),
        ("VEL", f"{datos.get('velocidad', 0.0):.3f} m/s"),
        ("DESTINO", _fmt_dist(datos.get("dist_destino"))),
    ]

    y = y0
    for clave, valor in filas:
        _texto(panel, clave, (x0, y + 13), TEXTO_2, 0.38, 1)
        (tw, _), _ = cv2.getTextSize(valor, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        _texto(panel, valor, (x0 + w - tw, y + 13), TEXTO, 0.42, 1)
        cv2.line(panel, (x0, y + 20), (x0 + w, y + 20), _tenue(REGLA, 0.7), 1)
        y += 24

    if estado:
        _texto(panel, "ESTADO", (x0, y + 13), TEXTO_2, 0.38, 1)
        (tw, _), _ = cv2.getTextSize(estado, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        _texto(panel, estado, (x0 + w - tw, y + 13), ACENTO, 0.42, 1)
        y += 24

    return y


# --------------------------------------------------------------- leyenda
def _leyenda(panel, x0: int, y: int, w: int) -> None:
    items = (("TCP", (255, 255, 255)), ("destino", OK), ("rastro", (60, 150, 245)))
    x = x0
    for texto, color in items:
        cv2.circle(panel, (x + 4, y), 4, color, -1, cv2.LINE_AA)
        _texto(panel, texto, (x + 12, y + 4), TEXTO_2, 0.36, 1)
        x += 20 + int(len(texto) * 7.2)
        if x > x0 + w - 40:
            break


# ----------------------------------------------------------------- utils
def _zona_trabajo(cfg: dict) -> dict:
    # Los límites se guardan relativos a Home cuando usar_home_como_origen está
    # activo; el mapa dibuja el TCP en base, así que hay que sumar el origen.
    ox = oy = 0.0
    if bool(cfg.get("usar_home_como_origen", False)):
        home = cfg.get("home_pose") or []
        if len(home) >= 2:
            try:
                ox, oy = float(home[0]), float(home[1])
            except (TypeError, ValueError):
                pass
    return {
        "min_x": float(cfg.get("zona_trabajo_min_x", 0.05)) + ox,
        "max_x": float(cfg.get("zona_trabajo_max_x", 0.55)) + ox,
        "min_y": float(cfg.get("zona_trabajo_min_y", -0.05)) + oy,
        "max_y": float(cfg.get("zona_trabajo_max_y", 0.45)) + oy,
    }


def _limites(zona: dict, datos: dict, det: dict) -> tuple[float, float, float, float]:
    """Auto-fit: rectángulo que contiene la zona, el TCP, el destino y la detección.

    El rastro se excluye a propósito: se purga con el tiempo y su extremo haría
    cambiar el zoom de forma intempestiva.
    """
    xs = [zona["min_x"], zona["max_x"]]
    ys = [zona["min_y"], zona["max_y"]]

    def incluir(p) -> None:
        if not p or len(p) < 2:
            return
        try:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
        except (TypeError, ValueError):
            pass

    incluir(datos.get("pos"))
    incluir(datos.get("destino"))
    if det.get("ok") and det.get("x_ur5") is not None:
        incluir((det["x_ur5"], det["y_ur5"]))

    return min(xs), max(xs), min(ys), max(ys)


def _escala(limites: tuple[float, float, float, float], w: int, h: int) -> tuple[float, float, float]:
    """Metros por píxel (igual en X e Y) y centro del mundo visible."""
    min_x, max_x, min_y, max_y = limites
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    span_x = max((max_x - min_x) * 1.15, 0.05)
    span_y = max((max_y - min_y) * 1.15, 0.05)
    esc = min((w - 4) / span_x, (h - 4) / span_y)
    return esc, cx, cy


def _paso_grilla(esc: float) -> float:
    for paso in (0.05, 0.1, 0.2, 0.5):
        if paso * esc >= 34:
            return paso
    return 1.0


def _texto(img, txt: str, org, color, escala: float = 0.4, grosor: int = 1) -> None:
    cv2.putText(img, str(txt), org, cv2.FONT_HERSHEY_SIMPLEX, escala, color,
                grosor, cv2.LINE_AA)


def _tenue(color, factor: float):
    return tuple(int(c * factor) for c in color)


def _dentro(rect, p) -> bool:
    x, y, w, h = rect
    return x <= p[0] < x + w and y <= p[1] < y + h


def _anillo(img, centro, radio: int, color, grosor: int = 1) -> None:
    cv2.circle(img, centro, radio, color, grosor, cv2.LINE_AA)


def _linea_recortada(img, rect, p1, p2, color, grosor: int = 1) -> None:
    ok, q1, q2 = cv2.clipLine(rect, p1, p2)
    if ok:
        cv2.line(img, q1, q2, color, grosor, cv2.LINE_AA)


def _linea_punteada(img, p1, p2, color, grosor: int = 1, paso: int = 8) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1:
        return
    n = max(1, int(dist / paso))
    for i in range(n):
        t0 = i / n
        t1 = min(1.0, (i + 0.55) / n)
        a = (int(x1 + (x2 - x1) * t0), int(y1 + (y2 - y1) * t0))
        b = (int(x1 + (x2 - x1) * t1), int(y1 + (y2 - y1) * t1))
        cv2.line(img, a, b, color, grosor, cv2.LINE_AA)


def _rect_punteado(img, p1, p2, color, grosor: int = 1, paso: int = 6) -> None:
    x1, y1 = p1
    x2, y2 = p2
    _linea_punteada(img, (x1, y1), (x2, y1), color, grosor, paso)
    _linea_punteada(img, (x2, y1), (x2, y2), color, grosor, paso)
    _linea_punteada(img, (x2, y2), (x1, y2), color, grosor, paso)
    _linea_punteada(img, (x1, y2), (x1, y1), color, grosor, paso)


def _fmt_m(valor) -> str:
    return "—" if valor is None else f"{float(valor):.3f}"


def _fmt_dist(valor) -> str:
    if valor is None:
        return "—"
    return f"{float(valor) * 1000:.0f} mm"
