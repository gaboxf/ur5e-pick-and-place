"""Detecta el puerto serie del Arduino del gripper y lo guarda en vision/config.json.

El gripper se conecta por USB:
  - En Linux aparece como /dev/ttyACM* (Arduino UNO original) o /dev/ttyUSB*
    (clones con CH340/FTDI).
  - En Windows aparece como COM3, COM4, etc. (con frecuencia un CH340).

Este script busca esos puertos, prioriza los que declaran un VID de USB-serie
conocido y escribe el resultado en la clave "gripper_port" de vision/config.json.

Uso:
    python tools/detectar_puerto_gripper.py            # detecta y guarda
    python tools/detectar_puerto_gripper.py --dry-run  # solo muestra
    python tools/detectar_puerto_gripper.py COM3       # forzar un puerto
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from serial.tools import list_ports
except ImportError:
    print("Falta pyserial:  pip install pyserial")
    sys.exit(2)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "vision" / "config.json"

# VID de chips USB-serie usados por placas Arduino y clones.
VIDS_CONOCIDOS = {
    0x2341: "Arduino",
    0x2A03: "Arduino (org)",
    0x1A86: "CH340/CH341",
    0x0403: "FTDI",
    0x10C4: "CP210x",
}


def _es_candidato(puerto) -> bool:
    """True si el puerto parece ser el Arduino del gripper."""
    if puerto.vid in VIDS_CONOCIDOS:
        return True
    if puerto.device.startswith("/dev/ttyACM") or puerto.device.startswith("/dev/ttyUSB"):
        return True
    # En Windows los puertos aparecen como COM*. Aceptamos los que describen
    # un adaptador USB-serie, para no confundir con COM1 del motherboard.
    if sys.platform == "win32" and puerto.device.upper().startswith("COM"):
        desc = (puerto.description or "").lower()
        return any(
            k in desc for k in ("usb", "serial", "ch340", "ftdi", "cp210", "arduino")
        )
    return False


def _prioridad(puerto) -> int:
    """Menor número = más probable de ser el Arduino."""
    if puerto.vid in VIDS_CONOCIDOS:
        return 0
    if puerto.device.startswith("/dev/ttyACM"):
        return 1
    if puerto.device.startswith("/dev/ttyUSB"):
        return 2
    if sys.platform == "win32" and puerto.device.upper().startswith("COM"):
        return 2
    return 3


def detectar_puertos() -> list:
    """Devuelve los puertos candidatos ordenados por probabilidad."""
    candidatos = [p for p in list_ports.comports() if _es_candidato(p)]
    return sorted(candidatos, key=lambda p: (_prioridad(p), p.device))


def guardar_puerto(puerto: str, config_path: Path = CONFIG_PATH) -> None:
    """Escribe gripper_port preservando el resto de la configuración."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["gripper_port"] = puerto
    with open(config_path, "w", encoding="utf-8") as f:
        # Mismo estilo compacto que el archivo original.
        json.dump(cfg, f, ensure_ascii=False)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        actual = json.load(f).get("gripper_port")

    if args:
        puerto = args[0]
    else:
        candidatos = detectar_puertos()
        if not candidatos:
            print("No se detectó ningún puerto del Arduino.")
            print("¿Está conectado el Arduino por USB?")
            print("En Linux busca /dev/ttyACM* o /dev/ttyUSB*; en Windows, puertos COM*.")
            print("¿Está conectado el Arduino por USB?")
            print(f"Se mantiene gripper_port = {actual!r}")
            return 1
        print("Puertos detectados:")
        for p in candidatos:
            vid = f"0x{p.vid:04x}" if p.vid is not None else "?"
            print(f"  {p.device}  |  {p.description}  |  VID {vid}")
        puerto = candidatos[0].device

    print(f"\nPuerto elegido: {puerto}")
    if dry_run:
        print("(--dry-run: no se modificó vision/config.json)")
        return 0

    guardar_puerto(puerto)
    print(f"Guardado en {CONFIG_PATH} -> \"gripper_port\": \"{puerto}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
