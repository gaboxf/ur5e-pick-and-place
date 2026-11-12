import logging
import queue
import threading
import time

try:
    import serial
except ImportError:
    raise ImportError("Falta pyserial: pip install pyserial")

logger = logging.getLogger("GripperClient")

# Respuestas que el firmware puede devolver por linea.
RESPUESTAS_VALIDAS = ("DONE", "READY", "BUSY", "ERROR", "EMERGENCY", "PROXIMITY_STOP")

# Eventos asincronicos que el firmware emite sin que se los pida.
EVENTOS_ASINCRONICOS = ("EMERGENCY", "PROXIMITY_STOP")

# Timeout corto por lectura: el timeout "de negocio" se maneja con un deadline.
_TIMEOUT_LECTURA = 0.2


class GripperClient:
    """Cliente Python para controlar el gripper de 4 servos conectado por USB (Arduino).

    Protocolo (ASCII, 115200 8N1, terminado en \\n):
      OPEN    -> BUSY si ya se mueve; si no, arranca y al terminar manda DONE
      CLOSE   -> idem
      STATUS  -> READY (quieto) | BUSY (moviendose)
      ERROR   -> comando desconocido
      EMERGENCY       -> evento asincronico: boton de parada fisico pulsado
      PROXIMITY_STOP  -> evento asincronico: objeto a menos de 15 cm

    Un hilo lector en segundo plano se encarga de leer el puerto. Las respuestas
    a comandos van a una cola que consume _enviar_comando(); los eventos de
    seguridad van a otra cola que el dashboard consulta con get_evento().
    """

    def __init__(self, port, baudrate=115200, timeout=4.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout  # deadline total por comando (segundos)
        self.ser = None
        self.simulado = False
        self.ultima_respuesta = None

        # Estado del gripper (READY, BUSY, ERROR, UNKNOWN)
        self._gripper_state = "UNKNOWN"

        # Colas de comunicacion con el hilo lector
        self._line_queue: queue.Queue[str] = queue.Queue()
        self._event_queue: queue.Queue[str] = queue.Queue()

        # Registro del ultimo evento de seguridad
        self.ultimo_evento = None
        self._timestamp_evento = 0.0

        # Hilo lector
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()

    # ------------------------------------------------------------------ setup
    def connect(self):
        """Abre el puerto, espera el reset del Arduino y confirma con STATUS."""
        if self.ser and self.ser.is_open:
            return True
        try:
            self.ser = serial.Serial(
                self.port, self.baudrate, timeout=_TIMEOUT_LECTURA
            )
        except Exception as e:
            logger.warning(
                "No se pudo abrir el puerto %s (%s). Entrando en MODO SIMULACIÓN.",
                self.port, e,
            )
            self.simulado = True
            self._gripper_state = "READY"
            return True  # permite probar cámara e interfaz sin Arduino

        time.sleep(2.0)  # el UNO se resetea al abrir el puerto (DTR)
        self.ser.reset_input_buffer()
        self.simulado = False

        # Arrancar el hilo lector ANTES de mandar STATUS, para no perder lineas.
        self._start_reader()

        resp = self._enviar_comando("STATUS", timeout=self.timeout)
        if resp in ("READY", "BUSY"):
            self._gripper_state = resp
            logger.info("Gripper conectado en %s (%s)", self.port, resp)
        else:
            self._gripper_state = "ERROR"
            logger.error(
                "El puerto %s abrió pero no respondió STATUS (respuesta: %s). "
                "¿Firmware cargado a 115200 baudios?",
                self.port,
                resp,
            )
        return True

    def close_connection(self):
        """Detiene el hilo lector y cierra el puerto serial."""
        self._stop_reader()
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    # ---------------------------------------------------- hilo lector serial
    def _start_reader(self):
        if self._reader_thread and self._reader_thread.is_alive():
            return
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="GripperReader", daemon=True
        )
        self._reader_thread.start()

    def _stop_reader(self):
        self._reader_stop.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None

    def _reader_loop(self):
        while not self._reader_stop.is_set():
            if not self.ser or not self.ser.is_open:
                time.sleep(0.1)
                continue
            try:
                raw = self.ser.readline()
            except Exception as e:
                logger.debug("Error leyendo serial: %s", e)
                time.sleep(0.1)
                continue
            if not raw:
                continue

            linea = raw.decode(errors="ignore").strip().upper()
            if not linea:
                continue
            self.ultima_respuesta = linea

            if linea in EVENTOS_ASINCRONICOS:
                # Evento de seguridad: registrar y encolar para el dashboard.
                self.actualizar_evento(linea)
                self._event_queue.put(linea)
                logger.warning("Evento de seguridad del gripper: %s", linea)
                continue

            if linea in RESPUESTAS_VALIDAS:
                self._line_queue.put(linea)

    # ------------------------------------------------------------- protocolo
    def _descartar_lineas(self):
        """Tira respuestas viejas pendientes en la cola."""
        while True:
            try:
                self._line_queue.get_nowait()
            except queue.Empty:
                break

    def _esperar_linea(self, timeout):
        """Espera la proxima respuesta valida del hilo lector."""
        fin = time.time() + timeout
        while time.time() < fin:
            restante = fin - time.time()
            try:
                linea = self._line_queue.get(timeout=min(0.2, max(0.01, restante)))
            except queue.Empty:
                continue
            return linea
        return "TIMEOUT"

    def _enviar_comando(self, comando, timeout=None):
        """Envia un comando y devuelve su respuesta (o TIMEOUT / ERROR)."""
        comando = comando.strip().upper()

        if self.simulado:
            logger.debug("[Gripper SIMULADO] Comando enviado: %s", comando)
            time.sleep(0.3)
            if comando == "STATUS":
                return "READY"
            if comando in ("OPEN", "CLOSE"):
                return "DONE"
            return "ERROR"

        if not self.ser or not self.ser.is_open:
            return "ERROR"

        try:
            self._descartar_lineas()
            self.ser.write((comando + "\n").encode())
            resp = self._esperar_linea(timeout or self.timeout)

            if resp in ("READY", "BUSY", "ERROR"):
                self._gripper_state = resp
            elif resp == "DONE":
                self._gripper_state = "READY"
            return resp
        except Exception as e:
            logger.error("Error en puerto serial: %s", e)
            return "ERROR"

    def _esperar_listo(self, timeout):
        """Espera a que el gripper termine el movimiento en curso (READY)."""
        fin = time.time() + timeout
        while time.time() < fin:
            resp = self._enviar_comando("STATUS", timeout=min(1.0, timeout))
            if resp == "READY":
                return True
            if resp in ("ERROR", "TIMEOUT"):
                return False
            time.sleep(0.05)
        return False

    def _mover(self, comando):
        """Manda OPEN/CLOSE y confirma el DONE final (no solo el eco)."""
        resp = self._enviar_comando(comando)
        if resp == "BUSY":
            if not self._esperar_listo(self.timeout):
                logger.warning("%s: el gripper sigue ocupado", comando)
                return False
            resp = self._enviar_comando(comando)

        if resp == "DONE":
            return True
        logger.warning("%s devolvió %s", comando, resp)
        return False

    # ------------------------------------------------------------- comandos
    def open(self):
        """Abre el gripper (OPEN) y espera el DONE de los 4 servos."""
        return self._mover("OPEN")

    def close(self):
        """Cierra el gripper (CLOSE) para agarrar la pieza y espera el DONE."""
        return self._mover("CLOSE")

    def status(self):
        """Consulta el estado (STATUS). Devuelve 'READY', 'BUSY' o 'ERROR'."""
        if self.simulado:
            return "READY"
        resp = self._enviar_comando("STATUS")
        if resp in ("READY", "DONE"):
            return "READY"
        if resp == "BUSY":
            return "BUSY"
        return "ERROR"

    # ---------------------------------------------------------- eventos
    def interpretar_respuesta(self, respuesta: str) -> str:
        """Convierte la respuesta cruda del firmware en un mensaje estructurado."""
        r = respuesta.strip().upper()
        if r == "EMERGENCY":
            return "emergencia_fisica"
        if r == "PROXIMITY_STOP":
            return "stop_proximidad"
        if r in RESPUESTAS_VALIDAS:
            return r
        return "desconocido"

    def actualizar_evento(self, respuesta: str):
        """Registrar una respuesta del firmware (para emergencia/proximidad)."""
        self.ultimo_evento = self.interpretar_respuesta(respuesta)
        self._timestamp_evento = time.time()

    def get_evento(self):
        """Devuelve el proximo evento de seguridad pendiente o None.

        Valores posibles: 'emergencia_fisica', 'stop_proximidad'.
        """
        try:
            bruto = self._event_queue.get_nowait()
        except queue.Empty:
            return None
        return self.interpretar_respuesta(bruto)

    def get_estado_completo(self) -> dict:
        """Devuelve un estado legible para el dashboard."""
        return {
            "gripper_status": self._gripper_state,
            "evento": self.ultimo_evento,
            "timestamp": self._timestamp_evento,
        }