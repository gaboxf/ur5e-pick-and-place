# probar_gripper.py - probador directo del firmware gripper_1signal
# 4 servos MG996R movidos por una sola senal (D3), ASCII 115200 8N1, "\n"
#
# Uso:  python probar_gripper.py COM3
#
# Teclas: o=OPEN  c=CLOSE  z=ZERO  s=STOP  t=STATUS  p=PING  a=ciclo  q=salir
# ZERO cierra despacio hasta que las mordazas se tocan y se queda ahi (tope
# mecanico = referencia comun de los 4 servos). Solo con la boca vacia.
# El heartbeat mantiene esa posicion hasta que mandes OPEN/STOP o salgas.
#
# Un hilo aparte manda PING cada 1.5 s para alimentar el watchdog del firmware
# (15 s): asi no se abre solo mientras pensas el proximo comando.
# No depende de gripper_client: habla el protocolo pelado con pyserial.

import argparse
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("Falta pyserial: pip install pyserial")

BAUD = 115200
HEARTBEAT_S = 1.5

RESPUESTAS = ("DONE", "READY", "BUSY", "ERROR")
EVENTOS = ("WATCHDOG_OPEN", "WATCHDOG_DONE")


class Probador:
    def __init__(self, puerto):
        self.ser = serial.Serial(puerto, BAUD, timeout=0.05)
        self._lock = threading.Lock()
        self._stop = threading.Event()

        time.sleep(2.0)  # el UNO se resetea al abrir el puerto (DTR)
        self.ser.reset_input_buffer()

        threading.Thread(target=self._latido, name="PING", daemon=True).start()

        print(f"Conectado a {puerto} @ {BAUD} 8N1")
        print("o=OPEN  c=CLOSE  z=ZERO  s=STOP  t=STATUS  p=PING  a=ciclo  q=salir")

    # ------------------------------------------------------------- escritura
    def _escribir(self, cmd):
        with self._lock:
            self.ser.write((cmd + "\n").encode())

    def _latido(self):
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self._escribir("PING")
            except Exception:
                return

    # --------------------------------------------------------------- lectura
    def _volcar(self):
        """Lee las lineas pendientes y devuelve los tokens recibidos."""
        tokens = []
        while True:
            raw = self.ser.readline()
            if not raw:
                break
            tk = raw.decode(errors="ignore").strip().upper()
            if not tk:
                continue
            tokens.append(tk)
            if tk in EVENTOS:
                print(f"   !! {tk}  (evento del firmware)")
            elif tk != "PONG":
                print(f"   <- {tk}")
        return tokens

    # -------------------------------------------------------------- comandos
    def comando(self, cmd, timeout=8.0):
        """Manda OPEN/CLOSE/STOP/STATUS y espera el token final."""
        self._volcar()  # descarta respuestas viejas
        t0 = time.time()
        self._escribir(cmd)
        print(f"-> {cmd}")

        primero = None
        while time.time() - t0 < timeout:
            for tk in self._volcar():
                if tk not in RESPUESTAS:
                    continue
                if primero is None:
                    primero = tk
                    if tk in ("ERROR", "READY"):
                        return tk
                    if tk == "BUSY" and cmd in ("CLOSE", "ZERO"):
                        print(f"   {cmd} rechazado mientras se movia: reintentar.")
                        return tk
                if tk == "DONE":
                    print(f"   ({time.time() - t0:.2f} s)")
                    return tk
            time.sleep(0.01)

        print("   TIMEOUT: no llego respuesta")
        return "TIMEOUT"

    def ping(self, timeout=2.0):
        t0 = time.time()
        self._escribir("PING")
        while time.time() - t0 < timeout:
            if "PONG" in self._volcar():
                print(f"   PONG ({time.time() - t0:.2f} s)")
                return True
            time.sleep(0.01)
        print("   sin PONG")
        return False

    def ciclo(self, n=5):
        """Abre y cierra n veces, midiendo cada carrera."""
        for i in range(1, n + 1):
            print(f"\n== ciclo {i}/{n} ==")
            if self.comando("OPEN") != "DONE":
                return
            time.sleep(0.3)
            if self.comando("CLOSE") != "DONE":
                return
            time.sleep(0.3)
        self.comando("OPEN")

    def cerrar(self):
        self._stop.set()
        time.sleep(0.2)
        if self.ser.is_open:
            self.ser.close()


def main():
    ap = argparse.ArgumentParser(description="Probador directo del gripper_1signal")
    ap.add_argument("puerto", nargs="?", default="COM3",
                    help="puerto serial (ej. COM3 o /dev/ttyUSB0)")
    args = ap.parse_args()

    probador = Probador(args.puerto)
    try:
        while True:
            tecla = input("\no/c/s/t/p/a/q > ").strip().lower()
            if tecla in ("q", "quit"):
                break
            elif tecla in ("o", "open"):
                probador.comando("OPEN")
            elif tecla in ("c", "close"):
                probador.comando("CLOSE")
            elif tecla in ("z", "zero"):
                print("   (cierra al tope mecanico: boca vacia; 'o' para abrir)")
                probador.comando("ZERO")
            elif tecla in ("s", "stop"):
                probador.comando("STOP")
            elif tecla in ("t", "status"):
                probador.comando("STATUS")
            elif tecla in ("p", "ping"):
                probador.ping()
            elif tecla in ("a", "ciclo"):
                n = input("ciclos [5] > ").strip()
                probador.ciclo(int(n) if n.isdigit() else 5)
            else:
                print("tecla invalida")
    except KeyboardInterrupt:
        print()
    finally:
        probador.cerrar()


if __name__ == "__main__":
    main()
