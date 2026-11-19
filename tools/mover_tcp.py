"""Mueve el TCP del UR5e en línea recta, muy lento y sin cambiar la orientación.

Pensado para ayudar en la calibración de la homografía: lleva la punta de la
herramienta hasta un punto de la mesa para que el usuario haga clic sobre ella
en la ventana del asistente.

Secuencia de seguridad (siempre la misma):
    1. sube en vertical hasta `z_viaje` (8 cm por encima del destino);
    2. se desplaza en XY a esa altura;
    3. baja en vertical hasta la Z pedida.

Uso:
    python tools/mover_tcp.py --x -0.50 --y -0.20 --dry-run
    python tools/mover_tcp.py --x -0.50 --y -0.20 --z 0.128
    python tools/mover_tcp.py --x -0.50 --y -0.20 --solo-subir

Seguridad:
    - NO cambia rx/ry/rz: conserva la orientación actual de la herramienta.
    - Rechaza destinos a menos de `radio_minimo_base` del eje de la base.
    - Rechaza Z fuera de [0.10, 0.40] m y X/Y fuera de ±1.0 m.
    - Verifica la cinemática inversa antes de cada tramo.
    - Velocidad por defecto 0.03 m/s (muy lento), aceleración 0.2 m/s².
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

RUTA_CONFIG = Path(__file__).resolve().parent.parent / "vision" / "config.json"

Z_MIN = 0.10
Z_MAX = 0.40
XY_MAX = 1.00
Z_VIAJE_MARGEN = 0.08
Z_VIAJE_TOPE = 0.35


def leer_config() -> dict:
    try:
        return json.loads(RUTA_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Mueve el TCP muy lento (UR5e).")
    parser.add_argument("--x", type=float, required=True, help="X destino [m]")
    parser.add_argument("--y", type=float, required=True, help="Y destino [m]")
    parser.add_argument("--z", type=float, default=None, help="Z destino [m]")
    parser.add_argument("--vel", type=float, default=0.03, help="m/s (def. 0.03)")
    parser.add_argument("--acc", type=float, default=0.2, help="m/s² (def. 0.2)")
    parser.add_argument("--solo-subir", action="store_true",
                        help="solo sube en vertical, no se desplaza")
    parser.add_argument("--dry-run", action="store_true",
                        help="muestra los tramos sin mover el robot")
    args = parser.parse_args()

    if not (Z_MIN <= (args.z if args.z is not None else Z_MIN) <= Z_MAX):
        print(f"Z destino {args.z} fuera de [{Z_MIN}, {Z_MAX}] m: rechazado.")
        return 2
    if abs(args.x) > XY_MAX or abs(args.y) > XY_MAX:
        print("X/Y fuera del rango permitido (±1 m): rechazado.")
        return 2

    radio_min = float(leer_config().get("radio_minimo_base", 0.20))
    radio = math.hypot(args.x, args.y)
    if radio < radio_min:
        print(f"Destino a {radio * 1000:.0f} mm de la base "
              f"(mínimo {radio_min * 1000:.0f} mm): rechazado.")
        return 2

    load_dotenv()
    import rtde_control
    import rtde_receive

    ip = os.getenv("UR5_IP", "192.168.1.10")
    print(f"UR5 en {ip}")

    robot = rtde_receive.RTDEReceiveInterface(ip)
    control = rtde_control.RTDEControlInterface(ip)
    try:
        if not control.isConnected():
            print("No se pudo conectar al robot (¿control remoto deshabilitado?).")
            return 1

        pose = robot.getActualTCPPose()
        x0, y0, z0 = pose[0], pose[1], pose[2]
        rx, ry, rz = pose[3], pose[4], pose[5]
        print(f"Pose actual:   x={x0:+.4f} y={y0:+.4f} z={z0:+.4f}")
        print(f"Orientación:   rx={rx:+.4f} ry={ry:+.4f} rz={rz:+.4f} (no cambia)")

        z_obj = z0 if args.z is None else args.z
        z_viaje = min(max(z0, z_obj) + Z_VIAJE_MARGEN, Z_VIAJE_TOPE)

        if args.solo_subir:
            tramos = [(x0, y0, z_viaje)]
        else:
            tramos = [(x0, y0, z_viaje), (args.x, args.y, z_viaje)]
            if args.z is not None:
                tramos.append((args.x, args.y, z_obj))

        print("Tramos:")
        for i, (tx, ty, tz) in enumerate(tramos, 1):
            print(f"  {i}) x={tx:+.4f} y={ty:+.4f} z={tz:+.4f}")

        if args.dry_run:
            print("--dry-run: no se mueve nada.")
            return 0

        for i, (tx, ty, tz) in enumerate(tramos, 1):
            destino = [tx, ty, tz, rx, ry, rz]
            q = control.getInverseKinematics(destino)
            if not q or all(abs(v) < 1e-9 for v in q):
                print(f"Tramo {i}: cinemática inversa inválida (fuera de alcance). "
                      "Se detiene acá.")
                return 3

            print(f"Tramo {i}: moviendo a {args.vel:.3f} m/s...")
            ok = control.moveL(destino, args.vel, args.acc)
            actual = robot.getActualTCPPose()
            print(f"  moveL={'OK' if ok else 'RECHAZADO'} -> "
                  f"x={actual[0]:+.4f} y={actual[1]:+.4f} z={actual[2]:+.4f}")
            if not ok:
                print("  El robot no completó el tramo. Se detiene acá.")
                return 4

        print("Listo. El robot está detenido en el destino.")
        return 0
    finally:
        try:
            control.disconnect()
        except Exception:
            pass
        try:
            robot.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
