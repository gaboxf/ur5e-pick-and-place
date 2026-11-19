"""Diagnóstico de hardware del sistema: cámaras, Arduino del gripper y UR5.

No mueve nada: solo abre puertos/cámaras, consulta STATUS del gripper y
verifica que el robot responda en la red. Útil cuando "no se ve la cámara"
o "no se mueve el gripper".

Uso:
    python tools/diagnostico_hardware.py
    python tools/diagnostico_hardware.py --sin-camaras   # saltea el escaneo
    python tools/diagnostico_hardware.py --camaras 0,1,2

Cerrá el dashboard antes de correrlo: si una app ya tiene la cámara o el
puerto COM abiertos, el diagnóstico va a fallar igual que el dashboard.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

# Igual que el resto del proyecto: las variables (ROBOT_MODE, UR5_IP, ...)
# vienen de .env. Sin esto el diagnóstico reportaría valores por defecto.
try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ_PROYECTO / ".env")
except ImportError:
    pass

CONFIG_PATH = RAIZ_PROYECTO / "vision" / "config.json"


def _leer_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def diagnosticar_camaras(indices: list[int]) -> None:
    import cv2

    print("\n== Cámaras ==")
    validadas = []
    for i in indices:
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        abierta = cap.isOpened()
        leyo = False
        forma = None
        if abierta:
            for _ in range(5):
                ok, frame = cap.read()
                if ok and frame is not None:
                    leyo = True
                    forma = frame.shape
                    break
        cap.release()
        estado = ("OK " if leyo else "SIN IMAGEN") if abierta else "no abre"
        detalle = f" {forma[1]}x{forma[0]}" if forma else ""
        print(f"  índice {i}: {estado}{detalle}")
        if leyo:
            validadas.append(i)

    if validadas:
        print(f"  -> Cámaras que entregan imagen: {validadas}")
    else:
        print("  -> Ninguna cámara entrega imagen.")
        print("     Revisá cable/USB y que Zoom/Teams/OBS no la estén usando.")


def diagnosticar_puertos(previsto: str | None) -> None:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("\n== Puertos serie ==\n  Falta pyserial (pip install pyserial)")
        return

    print("\n== Puertos serie ==")
    puertos = list(list_ports.comports())
    if not puertos:
        print("  No hay puertos COM detectados (¿Arduino desconectado?).")
    for p in puertos:
        vid = f"0x{p.vid:04x}" if p.vid else "?"
        marca = "  <-- gripper_port" if p.device == previsto else ""
        print(f"  {p.device} | {p.description} | VID {vid}{marca}")

    if not previsto:
        print("  config.json no define gripper_port.")
        return
    if previsto not in [p.device for p in puertos]:
        print(f"  ATENCIÓN: '{previsto}' no está en la lista de puertos.")
        print("  Corregilo con: python tools/detectar_puerto_gripper.py")


def diagnosticar_gripper(puerto: str | None) -> None:
    print("\n== Gripper (Arduino) ==")
    if not puerto:
        print("  Sin gripper_port configurado.")
        return
    try:
        from gripper.tools.gripper_client import GripperClient
    except Exception as e:  # pragma: no cover - dependencia opcional
        print(f"  No se pudo importar GripperClient: {e}")
        return

    g = GripperClient(puerto)
    try:
        g.connect()
        if g.simulado:
            print(f"  {puerto}: no se pudo abrir -> MODO SIMULADO.")
            return
        estado = g._gripper_state
        print(f"  {puerto}: conectado, STATUS = {estado}")
        if estado not in ("READY", "BUSY"):
            print("  El puerto abrió pero el firmware no respondió STATUS.")
            print("  ¿Firmware cargado y a 115200 baudios?")
    finally:
        g.close_connection()


def diagnosticar_robot() -> None:
    print("\n== Robot UR5 ==")
    modo = os.getenv("ROBOT_MODE", "mock")
    ip = os.getenv("UR5_IP", "192.168.1.10")
    print(f"  ROBOT_MODE = {modo} | UR5_IP = {ip}")
    if modo != "real":
        print("  Modo no real: el robot no se va a mover (es lo esperado).")
        return
    try:
        with socket.create_connection((ip, 30004), timeout=3.0):
            print(f"  {ip}:30004 (RTDE) responde.")
    except Exception as e:
        print(f"  {ip}:30004 (RTDE) NO responde: {e}")
        print("  Revisá la IP, el cable de red y que el robot esté encendido.")


def main() -> None:
    cfg = _leer_config()
    indice = cfg.get("camara", {}).get("indice_camara", 0)
    puerto = cfg.get("gripper_port")

    print(f"Config: {CONFIG_PATH}")
    print(f"  cámara configurada: índice {indice}")
    print(f"  gripper_port: {puerto}")

    indices = [indice]
    if "--camaras" in sys.argv:
        valor = sys.argv[sys.argv.index("--camaras") + 1]
        indices = [int(v) for v in valor.split(",") if v.strip()]
    else:
        indices = sorted(set(indices + [0, 1, 2, 3]))

    if "--sin-camaras" not in sys.argv:
        diagnosticar_camaras(indices)
    diagnosticar_puertos(puerto)
    diagnosticar_gripper(puerto)
    diagnosticar_robot()


if __name__ == "__main__":
    main()
