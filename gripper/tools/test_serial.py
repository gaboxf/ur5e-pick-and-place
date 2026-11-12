# test_simple_gripper.py – probador del gripper 4 servos
# ---------------------------------------------------------------
# Uso: python test_serial.py COM3   # Windows: "COM3"
# ---------------------------------------------------------------

import time
import argparse
import sys

try:
    from gripper_client import GripperClient
    GEOFIDDLE_OK = True
except ImportError:
    print("ERROR: no se pudo importar gripper_client → instala la librería")
    sys.exit(1)

class Tester:
    """Interfaz simple para probar el gripper."""

    def __init__(self, port="COM3"):
        self.client = GripperClient(port)
        self.client.connect()

    def ciclo(self):
        """Permite escribir comandos en un REPL simple."""
        print("\n=== Probador del gripper (OPEN/CLOSE/STATUS/HELP/QUIT) ===\n")
        while True:
            cmd = input("\[Comando] > ").strip().lower()
            if cmd == "quit":
                break
            elif cmd == "help":
                self._mostrar_ayuda()
            elif cmd == "status":
                self._mostrar_estado()
            else:
                if not self._ejecutar(cmd):
                    print("(no se pudo ejecutar \"{}– ignorado)".format(cmd))

    def _mostrar_ayuda(self):
        print("Comandos:")
        print("  open    – abrir gripper (continuar presionando para probar)")
        print("  close   – cerrar gripper (agarrar)")
        print("  status  – consultar estado")
        print("  help    – repetir esta ayuda")
        print("  quit    – salir")

    def _mostrar_estado(self):
        estado = self.client.status()
        print(f"Estado actual: {estado}")

    def _ejecutar(self, cmd):
        if cmd == "open":
            if self.client.open():
                print("Abriendo... (esperando DONE)")
                return True
            else:
                print("Fallo: no se pudo abrir (¿ocupado o error?)")
                return False
        elif cmd == "close":
            if self.client.close():
                print("Cerrando... (esperando DONE)")
                return True
            else:
                print("Fallo: no se pudo cerrar (¿ocupado o error?)")
                return False
        elif cmd in ("status", "help"):
            # Ya mostrados arriba
            return True
        else:
            print(f"Comando desconocido: {cmd}")
            return False

    def detener(self):
        self.client.close_connection()
def main():
    parser = argparse.ArgumentParser(description="Probador simple del gripper 4 servos")
    parser.add_argument("port", nargs="?", default="COM3", help="Puerto serial (ej. COM3 o /dev/ttyUSB0)")
    args = parser.parse_args()

    tester = Tester(args.port)
    try:
        tester.ciclo()
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
    finally:
        tester.detener()
if __name__ == "__main__":
    main()