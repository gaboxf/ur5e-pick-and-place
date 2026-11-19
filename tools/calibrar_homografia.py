"""Calibra la homografía píxel -> UR5 midiendo los 4 puntos con el robot.

Por qué existe: `puntos_ur5` en vision/config.json son valores de fábrica
(un rectángulo supuesto), no medidos. Si no coinciden con la mesa real, la
homografía manda al robot a coordenadas equivocadas (por ejemplo, sobre la
base) y los movimientos se rechazan.

Qué hace:
  1. Abre la cámara y muestra el recorte ROI (el mismo que ve el detector).
  2. Para cada una de las 4 esquinas te pide que lleves el TCP (con el teach
     pendant / freedrive) hasta esa esquina de la mesa y hagas clic sobre ese
     punto en la imagen.
  3. Lee la pose real del TCP por RTDE (solo lectura: no mueve nada) y arma
     la correspondencia píxel <-> metros.
  4. Calcula la homografía, muestra el error de cada esquina en mm y permite
     guardar `puntos_px`, `puntos_ur5` y la zona de trabajo en config.json.

Seguridad:
  - El script NO mueve el robot. Solo lee `getActualTCPPose()`.
  - Antes de guardar hace un backup: config.json.bak-AAAAMMDD-HHMMSS.
  - Avisa si alguna esquina queda dentro de `radio_minimo_base`.
  - El robot se mueve con el teach pendant: dejalo en velocidad baja.
  - Con --destinos mide las zonas de depósito A y B (`destinos`), que
    también son valores de fábrica y suelen quedar en el lugar equivocado.

Requisitos:
  - Cerrar los dashboards: la cámara solo la puede abrir un proceso.
  - El TCP debe estar configurado en la punta de la herramienta (la misma
    punta que se ve en la imagen). Si el TCP está en la brida, las medidas
    quedan corridas por el largo del gripper.

Uso:
    python tools/calibrar_homografia.py
    python tools/calibrar_homografia.py --sin-guardar
    python tools/calibrar_homografia.py --margen 0.03
    python tools/calibrar_homografia.py --brillo 1.8      # imagen más clara
    python tools/calibrar_homografia.py --manual          # escribir x,y a mano
    python tools/calibrar_homografia.py --destinos        # medir zonas A y B

Teclas en la ventana:
    clic izquierdo  -> registrar el punto actual
    r               -> borrar el último punto y repetirlo
    s               -> guardar (cuando ya hay 4 puntos)
    q / ESC         -> salir sin guardar
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ_PROYECTO / ".env")
except ImportError:
    pass

CONFIG_PATH_DEFECTO = RAIZ_PROYECTO / "vision" / "config.json"

# Orden igual al de config.json: top-left, top-right, bottom-right, bottom-left.
ESQUINAS = [
    ("superior-izquierda", "arriba a la izquierda de la imagen"),
    ("superior-derecha", "arriba a la derecha de la imagen"),
    ("inferior-derecha", "abajo a la derecha de la imagen"),
    ("inferior-izquierda", "abajo a la izquierda de la imagen"),
]
# Quinto punto: no se guarda, sirve para medir el error real de la homografía.
PUNTO_VERIFICACION = ("centro", "un punto interior, idealmente el centro de la mesa")
PUNTOS = ESQUINAS + [PUNTO_VERIFICACION]

# Modo --destinos: se miden las zonas de depósito (coinciden con `destinos`).
PUNTOS_DESTINOS = [
    ("destino A", "el centro de la zona donde se deposita la categoría A"),
    ("destino B", "el centro de la zona donde se deposita la categoría B"),
]

VENTANA = "Calibracion homografia (UR5e)"


# --------------------------------------------------------------------- utils
def _leer_config(ruta: Path) -> dict:
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def _origen(config: dict) -> tuple[float, float]:
    """Origen de trabajo (Home) si `usar_home_como_origen` está activo."""
    if not config.get("usar_home_como_origen"):
        return 0.0, 0.0
    home = config.get("home_pose") or [0.0, 0.0]
    try:
        return float(home[0]), float(home[1])
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0


def _dibujar_texto(img, lineas, y0=22, alto=22):
    for i, (texto, color) in enumerate(lineas):
        y = y0 + i * alto
        cv2.putText(img, texto, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, texto, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, color, 1, cv2.LINE_AA)


def _robot_en_movimiento(rtde_r, umbral: float = 0.01) -> bool:
    """True si el TCP se está moviendo (evita medir con el robot en marcha)."""
    try:
        v = rtde_r.getActualTCPSpeed()
        return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) > umbral
    except Exception:
        return False


def _pedir_pose_manual(nombre: str) -> tuple[float, float]:
    print(f"\n  Punto '{nombre}': escribí las coordenadas del TCP en la base")
    print("  (las muestra el teach pendant en 'Position' / 'Pose').")
    while True:
        try:
            entrada = input("  x,y [m] (ej: -0.250,-0.400): ").strip()
            xs, ys = entrada.replace(";", ",").split(",")
            return float(xs), float(ys)
        except (ValueError, EOFError):
            print("  Formato inválido, probá de nuevo.")


def _ip() -> str:
    return os.getenv("UR5_IP", "192.168.1.10")


def _conectar_receive():
    """Abre RTDE receive (solo lectura). No usa los registros de entrada."""
    try:
        import rtde_receive
    except ImportError as e:
        print(f"  No se pudo importar ur_rtde ({e}).")
        return None
    try:
        return rtde_receive.RTDEReceiveInterface(_ip())
    except Exception as e:
        print(f"  No se pudo abrir RTDE receive en {_ip()}: {e}")
        return None


def _pose_fresca() -> tuple[list[float] | None, str | None]:
    """Lee la pose abriendo una conexión RTDE nueva (evita datos viejos).

    La conexión se cierra enseguida. Si el robot está apagado o cambió la IP,
    devuelve (None, motivo) para que el clic no registre una pose inválida.
    """
    try:
        import rtde_receive
    except ImportError as e:
        return None, f"sin ur_rtde ({e})"
    try:
        rtde_r = rtde_receive.RTDEReceiveInterface(_ip())
    except Exception as e:
        return None, f"no conecta ({e})"
    try:
        return rtde_r.getActualTCPPose(), None
    except Exception as e:
        return None, f"no lee pose ({e})"
    finally:
        try:
            rtde_r.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------- geometría
def calcular_homografia(
    puntos_px: list[list[float]], puntos_ur5: list[list[float]]
) -> tuple[np.ndarray, list[float]]:
    """Devuelve (H, errores_mm) ajustando con los 4 puntos exactos."""
    px = np.array(puntos_px, dtype=np.float64)
    ur5 = np.array(puntos_ur5, dtype=np.float64)
    h, _ = cv2.findHomography(px.reshape(-1, 1, 2), ur5.reshape(-1, 1, 2), 0)
    if h is None:
        raise ValueError("No se pudo calcular la homografía (puntos degenerados).")
    errores = []
    for (x, y), (rx, ry) in zip(px, ur5):
        p = cv2.perspectiveTransform(
            np.array([[[x, y]]], dtype=np.float64), h
        )[0][0]
        errores.append(float(math.hypot(p[0] - rx, p[1] - ry)) * 1000.0)
    return h, errores


def _error_punto(h: np.ndarray, px: list[float], base: list[float]) -> float:
    """Error (mm) entre la predicción de H y un punto medido."""
    p = cv2.perspectiveTransform(
        np.array([[px]], dtype=np.float64), h
    )[0][0]
    return float(math.hypot(p[0] - base[0], p[1] - base[1])) * 1000.0


def zona_desde_puntos(
    puntos: list[list[float]], margen: float
) -> dict[str, float]:
    """Rectángulo de seguridad que envuelve los 4 puntos + margen."""
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return {
        "zona_trabajo_min_x": round(min(xs) - margen, 4),
        "zona_trabajo_max_x": round(max(xs) + margen, 4),
        "zona_trabajo_min_y": round(min(ys) - margen, 4),
        "zona_trabajo_max_y": round(max(ys) + margen, 4),
    }


def _area_quad(pts: np.ndarray) -> float:
    """Área del cuadrilátero (fórmula del polígono)."""
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def _orientacion(pts: np.ndarray) -> str:
    """Sentido de giro del cuadrilátero en la imagen (horario/antihorario)."""
    area = float(
        np.dot(pts[:, 0], np.roll(pts[:, 1], 1))
        - np.dot(pts[:, 1], np.roll(pts[:, 0], 1))
    )
    return "horario" if area > 0 else "antihorario"


# ------------------------------------------------------------------- guardar
def guardar(
    ruta: Path,
    puntos_px: list[list[float]],
    puntos_ur5_base: list[list[float]],
    margen: float,
) -> None:
    data = _leer_config(ruta)
    origen_x, origen_y = _origen(data)
    puntos_ur5 = [
        [round(x - origen_x, 4), round(y - origen_y, 4)]
        for x, y in puntos_ur5_base
    ]
    zona = zona_desde_puntos(puntos_ur5, margen)

    respaldo = ruta.with_name(
        f"{ruta.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(ruta, respaldo)

    data.setdefault("homografia", {})
    data["homografia"]["puntos_px"] = [
        [round(float(x), 1), round(float(y), 1)] for x, y in puntos_px
    ]
    data["homografia"]["puntos_ur5"] = puntos_ur5
    data.update(zona)

    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nGuardado en {ruta}")
    print(f"  backup: {respaldo.name}")
    print(f"  puntos_px  = {data['homografia']['puntos_px']}")
    print(f"  puntos_ur5 = {puntos_ur5}   (relativos al origen)")
    print("  zona_trabajo = "
          f"x [{zona['zona_trabajo_min_x']}, {zona['zona_trabajo_max_x']}]  "
          f"y [{zona['zona_trabajo_min_y']}, {zona['zona_trabajo_max_y']}]")
    print("\nReiniciá el dashboard para que tome la calibración nueva.")


def guardar_destinos(
    ruta: Path,
    puntos_base: list[list[float]],
) -> None:
    """Guarda las zonas de depósito medidas (relativas al origen)."""
    data = _leer_config(ruta)
    origen_x, origen_y = _origen(data)
    destinos = {}
    for (nombre, _), (x, y) in zip(PUNTOS_DESTINOS, puntos_base):
        clave = nombre.upper()[-1]
        destinos[clave] = {
            "x": round(x - origen_x, 4),
            "y": round(y - origen_y, 4),
        }

    respaldo = ruta.with_name(
        f"{ruta.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(ruta, respaldo)
    data["destinos"] = destinos
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nGuardado en {ruta}")
    print(f"  backup: {respaldo.name}")
    for clave, d in destinos.items():
        print(f"  destino {clave}: trabajo=({d['x']:+.4f}, {d['y']:+.4f}) m")
    print("\nReiniciá el dashboard para que tome los destinos nuevos.")


# ---------------------------------------------------------------------- main
def main() -> int:
    sin_guardar = "--sin-guardar" in sys.argv
    manual = "--manual" in sys.argv
    margen = 0.02
    brillo = 1.5
    if "--margen" in sys.argv:
        margen = float(sys.argv[sys.argv.index("--margen") + 1])
    if "--brillo" in sys.argv:
        brillo = float(sys.argv[sys.argv.index("--brillo") + 1])

    cfg_path = CONFIG_PATH_DEFECTO
    if "--config" in sys.argv:
        cfg_path = Path(sys.argv[sys.argv.index("--config") + 1])
    config = _leer_config(cfg_path)

    print(f"Config: {cfg_path}")
    origen_x, origen_y = _origen(config)
    if origen_x or origen_y:
        print(f"Origen de trabajo = Home ({origen_x:.3f}, {origen_y:.3f}) m")
        print("  Las esquinas se guardan como offset desde Home.")
    else:
        print("Origen de trabajo = base del robot (0, 0).")

    radio_min = float(config.get("radio_minimo_base", 0.20))

    modo_destinos = "--destinos" in sys.argv
    puntos_pedidos = PUNTOS_DESTINOS if modo_destinos else PUNTOS

    # --- Robot (solo lectura) ---
    rtde_r = None
    if not manual:
        print(f"\nConectando RTDE receive a {_ip()}...")
        rtde_r = _conectar_receive()
        pose, motivo = _pose_fresca()
        if pose is not None:
            print("  OK. TCP actual: "
                  f"({pose[0]:.3f}, {pose[1]:.3f}, {pose[2]:.3f}) m")
        else:
            print(f"  Sin RTDE ({motivo}): los clics se descartarán. "
                  "Usá --manual para escribir las coordenadas.")
            rtde_r = None

    # --- Cámara ---
    from vision.config import load_config
    from vision.visor import VisorCamara

    cam_cfg = load_config(cfg_path).camara
    visor = VisorCamara(cam_cfg)
    print(f"\nAbriendo cámara {cam_cfg.indice_camara} "
          "(puede tardar ~20 s la primera vez)...")
    if not visor.conectar():
        print("\nNo se pudo abrir la cámara.")
        print("Cerrá el dashboard / Zoom / Teams / OBS y volvé a intentar.")
        return 2

    frame = None
    for _ in range(30):
        frame = visor.leer()
        if frame is not None:
            break
        time.sleep(0.1)
    if frame is None:
        print("La cámara no entregó imagen.")
        visor.desconectar()
        return 2

    nuevo_clic: list[tuple[int, int]] = []
    cv2.namedWindow(VENTANA, cv2.WINDOW_AUTOSIZE)
    try:
        # Que la ventana quede por encima del editor/navegador al abrirla.
        cv2.setWindowProperty(VENTANA, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass
    cv2.setMouseCallback(
        VENTANA,
        lambda evento, x, y, flags, param: (
            param.append((x, y))
            if evento == cv2.EVENT_LBUTTONDOWN
            else None
        ),
        nuevo_clic,
    )

    puntos_px: list[list[float]] = []
    puntos_base: list[list[float]] = []
    ultimo_registro = 0.0

    print("\nInstrucciones:")
    if modo_destinos:
        print("  1) Llevá el TCP al centro de la zona de depósito indicada,")
        print("     tocando la mesa con la punta de la herramienta y sin moverlo.")
        print("  2) Hacé clic en la imagen sobre ESA punta.\n")
    else:
        print("  1) Llevá el TCP (teach pendant) hasta la esquina indicada,")
        print("     tocando la mesa con la punta de la herramienta y sin moverlo.")
        print("  2) Hacé clic en la imagen sobre ESA punta (no sobre el brazo).")
        print("  3) Repetí para las 4 esquinas y después para el punto interior")
        print("     de verificación (con ese se mide el error real).\n")

    mostrar = visor.roi(frame)
    salir = False
    guardado = False

    while not salir:
        frame_actual = visor.leer()
        if frame_actual is not None:
            mostrar = visor.roi(frame_actual)
        vista = mostrar.copy()
        if brillo != 1.0:
            # Solo para ver mejor la imagen en pantalla; no afecta la calibración.
            vista = cv2.convertScaleAbs(vista, alpha=brillo, beta=10)

        # Puntos ya registrados
        for i, (px, py) in enumerate(puntos_px):
            cv2.circle(vista, (int(px), int(py)), 6, (0, 255, 0), -1)
            cv2.putText(vista, str(i + 1), (int(px) + 8, int(py) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if len(puntos_px) >= 2:
            cv2.polylines(
                vista,
                [np.array(puntos_px[:4], dtype=np.int32)],
                len(puntos_px) >= 4,
                (0, 255, 0),
                1,
            )

        if len(puntos_px) < len(puntos_pedidos):
            nombre, detalle = puntos_pedidos[len(puntos_px)]
            etiqueta = ("Punto de verificación (no se guarda)"
                        if len(puntos_px) == 4 and not modo_destinos else "Punto")
            lineas = [
                (f"{etiqueta} {len(puntos_px) + 1}/{len(puntos_pedidos)}: {nombre} ({detalle})",
                 (0, 255, 255)),
                ("Toca la mesa con la punta del TCP y hace clic sobre ella.", (255, 255, 255)),
                ("[r] borrar ultimo   [q] salir", (200, 200, 200)),
            ]
        else:
            lineas = [
                (f"{len(puntos_pedidos)} puntos registrados.", (0, 255, 0)),
                ("[s] guardar   [r] rehacer ultimo   [q] salir", (200, 200, 200)),
            ]
        _dibujar_texto(vista, lineas)
        cv2.imshow(VENTANA, vista)

        # Un clic por iteración (para no registrar dos veces)
        if nuevo_clic:
            px, py = nuevo_clic.pop(0)
            h_crop, w_crop = vista.shape[:2]
            if not (0 <= px < w_crop and 0 <= py < h_crop):
                print("  Clic fuera de la imagen; ignorado.")
            elif len(puntos_px) >= len(puntos_pedidos):
                print("  Ya hay todos los puntos: usá [r] para rehacer el último.")
            elif time.time() - ultimo_registro < 0.6:
                print("  Clic ignorado (doble clic).")
            elif rtde_r is not None and _robot_en_movimiento(rtde_r):
                print("  El robot se está moviendo: esperá a que se detenga "
                      "y volvé a hacer clic.")
            else:
                if manual:
                    x_b, y_b = _pedir_pose_manual(puntos_pedidos[len(puntos_px)][0])
                else:
                    pose, motivo = _pose_fresca()
                    if pose is None:
                        print(f"  Sin lectura del robot ({motivo}): clic descartado, "
                              "volvé a hacer clic.")
                        continue
                    x_b, y_b = float(pose[0]), float(pose[1])
                    if math.hypot(x_b, y_b) < 0.02 and abs(pose[2]) > 0.5:
                        print(f"  Pose sospechosa ({x_b:.3f}, {y_b:.3f}): "
                              "clic descartado.")
                        continue

                puntos_px.append([float(px), float(py)])
                puntos_base.append([x_b, y_b])
                ultimo_registro = time.time()
                radio = math.hypot(x_b, y_b)
                aviso = ""
                if radio < radio_min:
                    aviso = f"  [PELIGRO: a {radio * 1000:.0f} mm de la base]"
                print(f"  {len(puntos_px)}/{len(puntos_pedidos)} "
                      f"{puntos_pedidos[len(puntos_px) - 1][0]}: "
                      f"px=({px}, {py})  base=({x_b:.4f}, {y_b:.4f}) m"
                      f"  trabajo=({x_b - origen_x:.4f}, {y_b - origen_y:.4f}){aviso}")

        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            salir = True
        elif key == ord("r") and puntos_px:
            puntos_px.pop()
            puntos_base.pop()
            print("  Último punto borrado; repetilo.")
        elif key == ord("s") and len(puntos_px) == len(puntos_pedidos):
            if modo_destinos:
                if sin_guardar:
                    print("  --sin-guardar: no se escribe nada.")
                    guardado = True
                    salir = True
                    continue
                guardar_destinos(cfg_path, puntos_base)
                guardado = True
                salir = True
                continue
            try:
                h, _ = calcular_homografia(puntos_px[:4], puntos_base[:4])
            except ValueError as e:
                print(f"  {e}")
                continue
            err_centro = _error_punto(h, puntos_px[4], puntos_base[4])
            print(f"\n  Verificación (punto interior): error = {err_centro:.1f} mm")
            if err_centro > 10.0:
                print("  ATENCIÓN: error alto. Revisá los clics y que la mesa sea plana.")
            if sin_guardar:
                print("  --sin-guardar: no se escribe nada.")
                guardado = True
                salir = True
                continue
            guardar(cfg_path, puntos_px[:4], puntos_base[:4], margen)
            guardado = True
            salir = True

    visor.desconectar()
    cv2.destroyAllWindows()
    if rtde_r is not None:
        try:
            rtde_r.disconnect()
        except Exception:
            pass

    if len(puntos_px) < len(puntos_pedidos) or not (guardado or sin_guardar):
        print("\nSaliste sin guardar. La calibración anterior sigue vigente.")
        return 0

    if modo_destinos:
        print("\n=== Destinos medidos ===")
        for (nombre, _), (x_b, y_b) in zip(PUNTOS_DESTINOS, puntos_base):
            radio = math.hypot(x_b, y_b)
            aviso = ""
            if radio < radio_min:
                aviso = (f"  [PELIGRO: a {radio * 1000:.0f} mm de la base]")
            print(f"  {nombre:10s} base=({x_b:7.3f}, {y_b:7.3f})  "
                  f"trabajo=({x_b - origen_x:7.3f}, {y_b - origen_y:7.3f}){aviso}")
        print("\nRecordá que el ciclo también verifica la zona de trabajo:")
        print("  python tools/verificar_homografia.py")
        return 0

    # --- Informe final ---
    px = np.array(puntos_px[:4])
    ur5 = np.array(puntos_base[:4])
    print("\n=== Resultado ===")
    try:
        h, _ = calcular_homografia(puntos_px[:4], puntos_base[:4])
    except ValueError as e:
        print(e)
        return 1

    for (nombre, _), (x_px, y_px), (x_b, y_b) in zip(
        ESQUINAS, puntos_px[:4], puntos_base[:4]
    ):
        tx, ty = x_b - origen_x, y_b - origen_y
        print(f"  {nombre:20s} px=({x_px:5.0f}, {y_px:5.0f})  "
              f"base=({x_b:7.3f}, {y_b:7.3f})  "
              f"trabajo=({tx:7.3f}, {ty:7.3f})")

    err_centro = _error_punto(h, puntos_px[4], puntos_base[4])
    print(f"\n  Error en el punto de verificación: {err_centro:.1f} mm")
    if err_centro > 10.0:
        print("  ATENCIÓN: más de 10 mm. Revisá que hayas hecho clic sobre la")
        print("  punta real del TCP, que la mesa esté plana y que el TCP esté")
        print("  definido en la punta del gripper (no en la brida).")

    area_m2 = _area_quad(ur5)
    print(f"  Área del cuadrilátero medido: {area_m2 * 10000:.0f} cm²")
    if area_m2 < 0.01:
        print("  ATENCIÓN: el área es muy chica (<100 cm²): ¿elegiste bien las esquinas?")

    orient_img = _orientacion(px)
    orient_ur5 = _orientacion(ur5)
    print(f"  Giro imagen: {orient_img} | giro en base: {orient_ur5}")
    if orient_img != orient_ur5:
        print("  ATENCIÓN: el mapeo queda espejado. Revisá el orden de las esquinas")
        print("  (1=sup-izq, 2=sup-der, 3=inf-der, 4=inf-izq).")

    cerca = [
        (i + 1, math.hypot(x, y))
        for i, (x, y) in enumerate(puntos_base[:4])
        if math.hypot(x, y) < radio_min
    ]
    for num, radio in cerca:
        print(f"  [PELIGRO] La esquina {num} está a {radio * 1000:.0f} mm de la "
              f"base (mínimo {radio_min * 1000:.0f} mm).")
    if cerca:
        print("  El robot va a rechazar los movimientos hacia esa zona.")

    print("\nAntes de mover el robot, verificá con:")
    print("  python tools/verificar_homografia.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
