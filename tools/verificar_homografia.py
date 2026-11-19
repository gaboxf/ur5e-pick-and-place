"""Verifica la cadena píxel -> UR5 SIN mover el robot.

Sirve para comprobar, antes de ejecutar un ciclo real, que las coordenadas que
calcula la cámara caen donde uno espera sobre la mesa. También confirma que el
origen de trabajo (Home como punto cero) se esté aplicando.

Uso:
  python tools/verificar_homografia.py                  # resumen + grilla
  python tools/verificar_homografia.py foto.jpg         # detecta la pieza en la imagen
  python tools/verificar_homografia.py --px 269,200     # convierte un píxel de la ROI
  python tools/verificar_homografia.py --config otra/ruta.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from vision.pipeline import PipelineVision  # noqa: E402


def _mm(x: float, y: float) -> str:
    return f"({x * 1000:8.1f}, {y * 1000:8.1f}) mm"


# Radio horizontal mínimo recomendado para el TCP: por debajo de esto el
# brazo queda demasiado cerca de su propia columna (riesgo de colisión).
RADIO_BASE_MIN_M = 0.20


def _aviso_base(x: float, y: float) -> str:
    r = (x * x + y * y) ** 0.5
    if r < RADIO_BASE_MIN_M:
        return f"  [PELIGRO: a {r * 1000:.0f} mm de la base]"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("imagen", nargs="?", default=None,
                    help="Imagen para correr el detector (opcional)")
    ap.add_argument("--px", default=None,
                    help="Píxel ROI a convertir, formato 'x,y'")
    ap.add_argument("--config", default=str(RAIZ_PROYECTO / "vision" / "config.json"))
    args = ap.parse_args()

    ruta_cfg = Path(args.config).resolve()
    pipeline = PipelineVision(ruta_cfg)
    tr = pipeline.transformador
    cfg = pipeline.cfg

    with open(ruta_cfg, "r", encoding="utf-8") as f:
        crudo = json.load(f)

    print(f"Config: {ruta_cfg}")
    if cfg.usar_home_como_origen:
        print(f"Origen de trabajo = HOME (x, y) {_mm(cfg.origen_x, cfg.origen_y)}")
        print("  puntos_ur5, zona_trabajo y destinos se miden desde ese punto.")
    else:
        print("Origen de trabajo = BASE del robot (0, 0).")
    print(f"Zona de trabajo (relativa al origen): "
          f"x [{cfg.zona_trabajo_min_x:.3f}, {cfg.zona_trabajo_max_x:.3f}]  "
          f"y [{cfg.zona_trabajo_min_y:.3f}, {cfg.zona_trabajo_max_y:.3f}]")

    print("\nEsquinas calibradas (px ROI -> base del robot):")
    for i, (px, ur5) in enumerate(zip(cfg.homografia.puntos_px,
                                      cfg.homografia.puntos_ur5)):
        nombre = ("sup-izq", "sup-der", "inf-der", "inf-izq")[i]
        x, y = ur5[0] + tr.origen_x, ur5[1] + tr.origen_y
        print(f"  {nombre:8s} {tuple(px)} -> {_mm(x, y)}{_aviso_base(x, y)}")

    print("\nGrilla del área calibrada (px ROI -> base del robot):")
    xs = [p[0] for p in cfg.homografia.puntos_px]
    ys = [p[1] for p in cfg.homografia.puntos_px]
    for fy in (0.0, 0.5, 1.0):
        fila = []
        for fx in (0.0, 0.5, 1.0):
            cx = int(min(xs) + (max(xs) - min(xs)) * fx)
            cy = int(min(ys) + (max(ys) - min(ys)) * fy)
            c = tr.transformar(cx, cy)
            marca = "" if c.ok else "  [FUERA DE ZONA]"
            fila.append(f"px({cx:4d},{cy:3d}) -> {_mm(c.x, c.y)}{marca}"
                        f"{_aviso_base(c.x, c.y)}")
        print("  " + " | ".join(fila))

    if args.px:
        try:
            px, py = (float(v) for v in args.px.split(","))
        except ValueError:
            sys.exit(f"Formato de --px inválido: {args.px!r} (usá 'x,y')")
        c = tr.transformar(int(px), int(py))
        estado = "OK" if c.ok else f"RECHAZADA: {c.motivo_fallo}"
        print(f"\nPíxel ROI ({px:.0f}, {py:.0f}) -> {_mm(c.x, c.y)}  {estado}"
              f"{_aviso_base(c.x, c.y)}")

    if args.imagen:
        ruta_img = Path(args.imagen).resolve()
        if not pipeline.conectar_imagen(ruta_img):
            sys.exit(f"No se pudo leer la imagen: {ruta_img}")
        frame = pipeline.visor.leer()
        det = pipeline.procesar_frame(frame)
        print(f"\nDetección en {ruta_img.name}:")
        print(json.dumps(det, indent=2, default=str))
        dib = pipeline.dibujar(frame, det)
        cv2.imshow("Verificacion homografia (ESC para cerrar)", dib)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
