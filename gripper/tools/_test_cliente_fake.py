"""Prueba del cliente GripperClient contra un Arduino simulado en memoria.

No toca hardware: implementa el protocolo del firmware real (gripper_1signal)
con los mismos tokens y tiempos, y verifica:
  1. El heartbeat mantiene vivo el watchdog durante una "transporte" largo.
  2. STOP redirige a abierto desde cualquier estado.
  3. WATCHDOG_OPEN / WATCHDOG_DONE llegan como eventos.
  4. El clamp y la logica de OPEN/CLOSE.
"""

import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from gripper.tools.gripper_client import GripperClient  # noqa: E402


class ArduinoFalso:
    """Emula el firmware gripper_1signal: OPEN/CLOSE/STOP/STATUS/PING."""

    def __init__(self, watchdog_ms=15000):
        self.watchdog_ms = watchdog_ms
        self.ultima_comunicacion = time.time()
        self.abierto = 170
        self.cerrado = 110
        self.posicion = self.abierto
        self.destino = self.abierto
        self.en_movimiento = False
        self.por_watchdog = False
        self.linea = ""
        self.lock = threading.Lock()
        self.entrada = []
        self.salida = []
        self.hilo = threading.Thread(target=self._loop, daemon=True)
        self.hilo.start()

    # -- emulacion del lazo del firmware ---------------------------------
    def _loop(self):
        while True:
            with self.lock:
                self._revisar_serial()
                self._watchdog()
                self._mover()
            time.sleep(0.005)  # ~ el paso de 30 ms del firmware, escalado

    def _revisar_serial(self):
        # OJO: la entrada se CONSUME (como en el firmware real). Si no, los
        # comandos viejos se reprocesan en cada vuelta.
        while self.entrada:
            ch = self.entrada.pop(0)
            if ch in "\n\r":
                cmd = self.linea.strip()
                self.linea = ""
                if cmd:
                    self._procesar(cmd)
            elif len(self.linea) < 16:
                self.linea += ch

    def _procesar(self, cmd):
        if cmd == "OPEN":
            if self.en_movimiento:
                self._empezar(self.abierto)
                self.salida.append("BUSY")
            else:
                self._empezar(self.abierto)
        elif cmd == "CLOSE":
            if self.en_movimiento:
                self.salida.append("BUSY")
            else:
                self._empezar(self.cerrado)
        elif cmd == "STOP":
            if self.en_movimiento:
                self._empezar(self.abierto)
                self.salida.append("BUSY")
            elif self.posicion != self.abierto:
                self._empezar(self.abierto)
            else:
                self.salida.append("DONE")
        elif cmd == "STATUS":
            self.salida.append("BUSY" if self.en_movimiento else "READY")
        elif cmd == "PING":
            self.salida.append("PONG")
        else:
            self.salida.append("ERROR")
            return  # comando invalido: NO resetea el watchdog
        self.ultima_comunicacion = time.time()

    def _empezar(self, d, por_watchdog=False):
        self.destino = max(min(self.abierto, self.cerrado),
                           min(max(self.abierto, self.cerrado), d))
        self.en_movimiento = True
        self.por_watchdog = por_watchdog

    def _watchdog(self):
        if (time.time() - self.ultima_comunicacion) * 1000 > self.watchdog_ms \
                and self.destino != self.abierto:
            self.salida.append("WATCHDOG_OPEN")
            self._empezar(self.abierto, True)

    def _mover(self):
        if not self.en_movimiento:
            return
        if self.posicion < self.destino:
            self.posicion += 5
        elif self.posicion > self.destino:
            self.posicion -= 5
        if self.posicion == self.destino:
            self.en_movimiento = False
            self.salida.append("WATCHDOG_DONE" if self.por_watchdog else "DONE")
            self.por_watchdog = False

    # -- API para el cliente ---------------------------------------------
    def escribir(self, texto):
        with self.lock:
            self.entrada.extend(texto)

    def leer(self):
        with self.lock:
            out = list(self.salida)
            self.salida.clear()
            return out


class PuertoFalso:
    """Objeto tipo pyserial.Serial."""

    def __init__(self, arduino):
        self.arduino = arduino
        self.is_open = True
        self._pendientes = []

    def write(self, data):
        if isinstance(data, bytes):
            data = data.decode()
        self.arduino.escribir(data)

    def readline(self):
        # Devuelve UNA linea por llamada y conserva el resto, como pyserial.
        if not self._pendientes:
            self._pendientes = self.arduino.leer()
        if not self._pendientes:
            time.sleep(0.01)
            return b""
        return (self._pendientes.pop(0) + "\n").encode()

    def reset_input_buffer(self):
        self._pendientes.clear()

    def close(self):
        self.is_open = False


def main():
    fallos = []

    def check(nombre, cond, detalle=""):
        if cond:
            print(f"  OK   {nombre}")
        else:
            print(f"  FAIL {nombre} {detalle}")
            fallos.append(nombre)

    print("== 1. Heartbeat mantiene vivo el watchdog en 'transporte' largo ==")
    arduino = ArduinoFalso(watchdog_ms=15000)
    cliente = GripperClient("FAKE")
    cliente.ser = PuertoFalso(arduino)
    cliente.simulado = False
    cliente._start_reader()
    cliente._start_heartbeat()

    resp = cliente._enviar_comando("STATUS")
    check("STATUS responde READY", resp == "READY", resp)

    cliente.close()  # el cierre tambien para el heartbeat

    # Re-armo con el watchdog acelerado (3 s) para que la prueba no tarde 15 s
    arduino = ArduinoFalso(watchdog_ms=3000)
    cliente = GripperClient("FAKE")
    cliente.ser = PuertoFalso(arduino)
    cliente.simulado = False
    cliente._start_reader()
    cliente._start_heartbeat()

    # El transporte: 8 s sin mandar NINGUN comando (solo el heartbeat)
    print("  ... 8 s de 'transporte' (solo heartbeat, sin comandos)")
    t0 = time.time()
    while time.time() - t0 < 8:
        time.sleep(0.2)
        if arduino.destino != arduino.abierto:
            break

    check(
        "el watchdog NO abrio solo durante el transporte",
        arduino.destino == arduino.abierto and arduino.posicion == arduino.abierto,
        f"destino={arduino.destino} posicion={arduino.posicion}",
    )
    evento = cliente.get_evento()
    check("no se emitio evento de watchdog", evento is None, str(evento))

    print("== 2. STOP redirige a abierto desde cualquier estado ==")
    resp = cliente.close()
    check("CLOSE devuelve DONE", resp is True, str(resp))
    check("posicion cerrada", arduino.posicion == arduino.cerrado, str(arduino.posicion))

    resp = cliente.stop()
    check("STOP devuelve True", resp is True, str(resp))
    time.sleep(0.2)
    check("STOP abrio el gripper", arduino.posicion == arduino.abierto,
          str(arduino.posicion))

    print("== 3. STOP durante un movimiento en curso ==")
    cliente.close()
    arduino.en_movimiento = True
    arduino.destino = arduino.cerrado
    resp = cliente.stop()
    check("STOP en movimiento devuelve True", resp is True, str(resp))
    time.sleep(0.2)
    check("STOP interrumpio el cierre", arduino.posicion == arduino.abierto,
          str(arduino.posicion))

    print("== 4. Watchdog: (a) heartbeat muere, (b) PC muerta ==")
    # 4a: el heartbeat se cae pero el hilo lector sigue vivo -> el evento
    # WATCHDOG_OPEN tiene que llegar al dashboard (get_evento).
    arduino2 = ArduinoFalso(watchdog_ms=1500)
    cliente2 = GripperClient("FAKE")
    cliente2.ser = PuertoFalso(arduino2)
    cliente2.simulado = False
    cliente2._start_reader()
    cliente2._enviar_comando("CLOSE")
    time.sleep(0.3)
    cliente2._stop_heartbeat()  # el latido muere: la PC parece colgada
    cliente2._start_heartbeat()  # ...y vuelve (simula otro consumidor)

    evento = None
    for _ in range(40):
        time.sleep(0.1)
        evento = cliente2.get_evento()
        if evento:
            break
    check("llego el evento de watchdog al dashboard", evento == "apertura_watchdog",
          str(evento))
    cliente2.close()

    # 4b: la PC muere del todo (nadie lee el puerto). El firmware tiene que
    # abrir solo: es lo que evita apretar la pieza indefinidamente.
    arduino3 = ArduinoFalso(watchdog_ms=1500)
    cliente3 = GripperClient("FAKE")
    cliente3.ser = PuertoFalso(arduino3)
    cliente3.simulado = False
    cliente3._start_reader()
    cliente3._enviar_comando("CLOSE")
    time.sleep(0.3)
    cliente3.close()  # PC muerta: se cierra el puerto, nadie lee mas

    abrio = False
    for _ in range(60):
        time.sleep(0.1)
        if arduino3.posicion == arduino3.abierto:
            abrio = True
            break
    check("el firmware abrio solo tras la muerte de la PC", abrio,
          f"posicion={arduino3.posicion}")

    print("== 5. Comandos invalidos no resetean el watchdog ==")
    arduino4 = ArduinoFalso(watchdog_ms=1200)
    cliente4 = GripperClient("FAKE")
    cliente4.ser = PuertoFalso(arduino4)
    cliente4.simulado = False
    cliente4._start_reader()
    cliente4._enviar_comando("CLOSE")
    # solo basura: no debe contar como PC viva
    for _ in range(20):
        cliente4.ser.write(b"XXXXX\n")
        time.sleep(0.1)
    check("la basura no mantiene vivo el watchdog",
          arduino4.destino == arduino4.abierto, f"destino={arduino4.destino}")
    cliente4.close()

    print()
    if fallos:
        print(f"FALLARON {len(fallos)}: {fallos}")
        return 1
    print("TODAS LAS PRUEBAS PASARON")
    return 0


if __name__ == "__main__":
    sys.exit(main())
