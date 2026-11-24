import logging
import os
import queue
import threading
import time

try:
    import serial
except ImportError:
    raise ImportError("Falta pyserial: pip install pyserial")

logger = logging.getLogger("GripperClient")

# Logger dedicado al trafico serie crudo (Opcion A de logs del Arduino):
# por aca pasa lo que el Arduino emite y lo que la PC le manda (comandos y
# respuestas), con timestamps del PC. Se activa con GRIPPER_LOG_SERIE=1 en
# .env. El heartbeat PING/PONG NO se registra aca: es ruido periodico (cada
# 1.5 s) y no aporta diagnostico; los cambios de estado reales se loguean con
# el logger "GripperClient". Como el logger raiz engancha el handler de
# archivo de corrida, las lineas caen automaticamente en
# logs/corrida_FECHA_HORA.log cuando LOG_ARCHIVO=1.
logger_serie = logging.getLogger("GripperSerial")

# Respuestas que el firmware puede devolver por linea.
RESPUESTAS_VALIDAS = ("DONE", "READY", "BUSY", "ERROR")

# Eventos asincronicos que el firmware emite sin que se los pida.
# WATCHDOG_OPEN / WATCHDOG_DONE: el firmware se abrio solo porque la PC dejo
# de mandar comandos validos (watchdog interno de 15 s). No es un error de
# la PC: es la evidencia de que algo se quedo colgado.
EVENTOS_ASINCRONICOS = (
    "WATCHDOG_OPEN",
    "WATCHDOG_DONE",
)

# Timeout corto por lectura: el timeout "de negocio" se maneja con un deadline.
_TIMEOUT_LECTURA = 0.2

# Heartbeat: intervalo con el que la PC manda "PING" al firmware.
# El firmware reinicia su watchdog interno de 15 s con cada comando valido,
# asi que este latido es lo que mantiene el gripper cerrado durante los
# tramos largos del UR5 (transporte con la pieza sujetada). Tiene que ser
# bastante menor que 15 s para dejar margen ante un retardo del sistema.
HEARTBEAT_S = 1.5

# Cuanto espera el heartbeat la respuesta PONG antes de asumir que el puerto
# no responde (no bloquea el bucle principal).
_TIMEOUT_PONG = 2.0

# Fallos consecutivos del heartbeat antes de abandonar el hilo. Un error
# transitorio no puede matar el latido: si el heartbeat muere, el firmware
# abre solo en 15 s y suelta la pieza.
_FALLOS_HEARTBEAT_MAX = 3


class GripperClient:
    """Cliente Python para controlar el gripper de 4 servos conectado por USB (Arduino).

    Protocolo (ASCII, 115200 8N1, terminado en \\n):
      OPEN    -> DONE al terminar; si ya se mueve, redirige a abierto y
                 responde BUSY
      CLOSE   -> DONE al terminar; si ya se mueve, responde BUSY
      STOP    -> redirige a abierto desde cualquier estado; DONE al terminar
      STATUS  -> READY (quieto) | BUSY (moviendose)
      PING    -> PONG (latido del heartbeat; token propio para no mezclarse
                 con la respuesta a un OPEN/CLOSE en vuelo)
      ERROR   -> comando desconocido
      WATCHDOG_OPEN   -> evento asincronico: el firmware se abrio solo porque
                         la PC dejo de mandar comandos validos
      WATCHDOG_DONE   -> evento asincronico: termino esa apertura de watchdog

    Un hilo lector en segundo plano se encarga de leer el puerto. Las respuestas
    a comandos van a una cola que consume _enviar_comando(); los eventos de
    seguridad van a otra cola que el dashboard consulta con get_evento().

    Un hilo de heartbeat manda "PING" cada HEARTBEAT_S segundos: el firmware
    reinicia su watchdog interno de 15 s con cada comando valido, asi que este
    latido es lo que mantiene el gripper cerrado durante los tramos largos del
    UR5 (transporte con la pieza sujetada). Sin el, el firmware abriria solo a
    los 15 s y soltaria la pieza en el aire.
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
        self._pong_queue: queue.Queue[str] = queue.Queue()

        # Registro del ultimo evento de seguridad
        self.ultimo_evento = None
        self._timestamp_evento = 0.0

        # Hilo lector
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()

        # Hilo de heartbeat (latido PING)
        self._hb_thread: threading.Thread | None = None
        self._hb_stop = threading.Event()
        self._ultimo_ping = 0.0
        self._ultima_respuesta_valida: float | None = None

        # Serializa las escrituras al puerto: el heartbeat y los comandos
        # comparten el mismo cable y no pueden intercalarse bytes.
        self._write_lock = threading.Lock()

        # Serializa los comandos completos (escribir + esperar respuesta).
        # Sin esto, dos llamadas concurrentes (p. ej. el dashboard web y el
        # hilo del ciclo) se pueden robar la respuesta del otro: el DONE de
        # un OPEN seria leido como respuesta de un CLOSE.
        # RLock (reentrante) por si un comando anida a otro.
        self._cmd_lock = threading.RLock()

        # Log serie crudo (GRIPPER_LOG_SERIE=1 en .env para activarlo). Se
        # resuelve en el primer uso y no aca: el dashboard web crea este
        # cliente en el cuerpo del modulo, ANTES de que configurar_logging()
        # cargue el .env, asi que leerlo en __init__ daria falso negativo y
        # no apareceria ningun log del Arduino.
        self._log_serie_activo: bool | None = None

    # ------------------------------------------------------------------ setup
    def _log_serie(self, direccion: str, mensaje: str) -> None:
        """Registra una linea de trafico serie si GRIPPER_LOG_SERIE esta activo."""
        if self._log_serie_activo is None:
            self._log_serie_activo = os.getenv(
                "GRIPPER_LOG_SERIE", ""
            ).strip().lower() in ("1", "true", "si", "on", "yes")
        if self._log_serie_activo:
            logger_serie.info("[%s] %s", direccion, mensaje)

    def _set_estado(self, nuevo: str) -> None:
        """Actualiza el estado del gripper y lo registra SOLO si cambio.

        El logger serie crudo muestra comandos/respuestas, pero el heartbeat
        y los STATUS repetidos no deben inundar el log. Aca solo se emite una
        linea INFO por transicion real (p. ej. READY -> BUSY -> READY).
        """
        if nuevo == self._gripper_state:
            return
        anterior = self._gripper_state
        self._gripper_state = nuevo
        logger.info("Estado del gripper: %s -> %s", anterior, nuevo)

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
            self._ultima_respuesta_valida = None
            self.simulado = True
            self._set_estado("READY")
            return True  # permite probar cámara e interfaz sin Arduino

        # Arrancar el hilo lector ANTES del sleep del reset: asi el banner
        # "READY" que el setup() manda al bootlear entra por el log serie
        # (queda encolado, pero _descartar_lineas lo limpia antes de cada
        # comando, asi que no ensucia las respuestas).
        self._start_reader()

        time.sleep(2.0)  # el UNO se resetea al abrir el puerto (DTR)
        self.ser.reset_input_buffer()
        self.simulado = False
        self._ultima_respuesta_valida = None

        resp = self._enviar_comando("STATUS", timeout=self.timeout)
        if resp in ("READY", "BUSY"):
            self._ultima_respuesta_valida = time.monotonic()
            self._set_estado(resp)
            logger.info("Gripper conectado en %s (%s)", self.port, resp)
            # Heartbeat activo: es lo que mantiene el watchdog del firmware
            # (15 s) alimentado durante los tramos largos del UR5, donde la
            # PC no manda ningun comando. Sin el, el firmware abriria solo y
            # soltaria la pieza en pleno transporte.
            self._start_heartbeat()
        else:
            self._set_estado("ERROR")
            logger.error(
                "El puerto %s abrió pero no respondió STATUS (respuesta: %s). "
                "¿Firmware cargado a 115200 baudios?",
                self.port,
                resp,
            )
        return True

    def close_connection(self):
        """Detiene el heartbeat, el hilo lector y cierra el puerto serial."""
        self._stop_heartbeat()
        self._stop_reader()
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    # ------------------------------------------------------ hilo de heartbeat
    def _start_heartbeat(self):
        """Levanta el hilo que manda PING cada HEARTBEAT_S segundos.

        Es lo que mantiene vivo el watchdog del firmware (15 s) durante los
        tramos largos del UR5: sin PING, el firmware abre solo a los 15 s y
        suelta la pieza en pleno transporte.
        """
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="GripperHeartbeat", daemon=True
        )
        self._hb_thread.start()

    def _stop_heartbeat(self):
        self._hb_stop.set()
        if self._hb_thread and self._hb_thread.is_alive():
            self._hb_thread.join(timeout=2.0)
        self._hb_thread = None

    def _heartbeat_loop(self):
        """Envia PING periodicamente y espera el PONG de confirmacion.

        No usa _enviar_comando(): el PONG va a su propia cola para que no se
        confunda con la respuesta a un OPEN/CLOSE que este en vuelo, y asi un
        PING perdido no puede hacer que un comando se lea como BUSY/DONE.

        Un error transitorio del puerto NO puede matar este hilo: si el
        heartbeat muere, el watchdog del firmware (15 s) abre el gripper y
        suelta la pieza en pleno transporte. Solo se abandona tras varios
        fallos seguidos.
        """
        fallos_consecutivos = 0
        while not self._hb_stop.wait(HEARTBEAT_S):
            ser = self.ser
            if not ser or not ser.is_open or self.simulado:
                continue
            try:
                with self._write_lock:
                    ser.write(b"PING\n")
                self._ultimo_ping = time.time()
                # El PING/PONG NO se loguea: se repite cada 1.5 s y solo
                # inundaria el log serie. Su salud se vigila con los debug/error
                # de abajo (PONG faltante / fallos consecutivos).
                # Consume el PONG si llega. Se descarta primero lo que haya
                # quedado de un ciclo anterior (un PONG tardio no debe
                # hacer creer que este PING ya tiene respuesta).
                while True:
                    try:
                        self._pong_queue.get_nowait()
                    except queue.Empty:
                        break
                try:
                    self._pong_queue.get(timeout=_TIMEOUT_PONG)
                except queue.Empty:
                    logger.debug("Heartbeat: no llegó PONG a tiempo")
                fallos_consecutivos = 0
            except Exception as e:
                fallos_consecutivos += 1
                logger.debug("Error en heartbeat (%d seguidos): %s", fallos_consecutivos, e)
                if fallos_consecutivos >= _FALLOS_HEARTBEAT_MAX:
                    logger.error(
                        "Heartbeat del gripper detenido tras %d fallos: el "
                        "firmware puede abrir solo en 15 s.",
                        fallos_consecutivos,
                    )
                    return

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

            # PONG va a su propia cola: es la respuesta del heartbeat y no
            # debe mezclarse con las respuestas a comandos en vuelo. Tampoco
            # se loguea (seria ruido periodico cada 1.5 s).
            if linea == "PONG":
                self._ultima_respuesta_valida = time.monotonic()
                self._pong_queue.put(linea)
                continue

            # Log de linea cruda ANTES de filtrar: asi el log guarda tambien
            # basura o respuestas desconocidas, util para diagnosticar baud
            # incorrecto, cable flojo o un firmware desactualizado.
            self._log_serie("PC<-Arduino", linea)

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
            self._log_serie("PC->Arduino", f"{comando} (SIMULADO)")
            logger.debug("[Gripper SIMULADO] Comando enviado: %s", comando)
            time.sleep(0.3)
            if comando == "STATUS":
                return "READY"
            if comando in ("OPEN", "CLOSE", "STOP"):
                return "DONE"
            return "ERROR"

        if not self.ser or not self.ser.is_open:
            return "ERROR"

        try:
            # _cmd_lock serializa comando completo (escribir + esperar
            # respuesta) entre llamadas concurrentes: sin el, el DONE de un
            # OPEN podria ser leido como respuesta de un CLOSE.
            # _write_lock evita que el heartbeat intercale bytes a mitad de
            # linea. Son locks distintos a proposito: el heartbeat solo pide
            # el de escritura, asi que nunca queda bloqueado por un comando
            # largo y el watchdog del firmware sigue alimentandose.
            with self._cmd_lock:
                with self._write_lock:
                    self._descartar_lineas()
                    self._log_serie("PC->Arduino", comando)
                    self.ser.write((comando + "\n").encode())
                resp = self._esperar_linea(timeout or self.timeout)

            if resp in ("READY", "BUSY", "DONE") and self._ultima_respuesta_valida is not None:
                self._ultima_respuesta_valida = time.monotonic()
            if resp in ("READY", "BUSY", "ERROR"):
                self._set_estado(resp)
            elif resp == "DONE":
                self._set_estado("READY")
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

    def stop(self):
        """STOP: redirige el gripper a abierto desde cualquier estado.

        Es la parada suave: no espera a que termine el movimiento en curso,
        lo invierte. Si el gripper esta quieto y cerrado, arranca la apertura
        y devuelve el DONE al completarla.
        """
        # Un solo lock para todo el comando (respuesta BUSY + DONE final):
        # si otro comando se cuela entremedio, se puede robar el DONE.
        with self._cmd_lock:
            resp = self._enviar_comando("STOP")
            if resp == "BUSY":
                # El firmware redirigio a abierto y sigue moviendose: el DONE
                # llega al completar la carrera.
                return self._esperar_linea(self.timeout) == "DONE"
            return resp == "DONE"

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
        if r == "WATCHDOG_OPEN":
            return "apertura_watchdog"
        if r == "WATCHDOG_DONE":
            return "apertura_watchdog_completada"
        if r in RESPUESTAS_VALIDAS:
            return r
        return "desconocido"

    def actualizar_evento(self, respuesta: str):
        """Registrar una respuesta del firmware (para el watchdog)."""
        self.ultimo_evento = self.interpretar_respuesta(respuesta)
        self._timestamp_evento = time.time()

    def get_evento(self):
        """Devuelve el proximo evento de seguridad pendiente o None.

        Valores posibles: 'apertura_watchdog', 'apertura_watchdog_completada'.
        """
        try:
            bruto = self._event_queue.get_nowait()
        except queue.Empty:
            return None
        return self.interpretar_respuesta(bruto)

    def conexion_disponible(self) -> bool:
        """Confirma puerto abierto y respuesta reciente de un Arduino inicializado."""
        ultima = self._ultima_respuesta_valida
        return (
            not self.simulado and self.ser is not None and self.ser.is_open
            and ultima is not None and time.monotonic() - ultima < 4 * HEARTBEAT_S
        )

    def get_estado_completo(self) -> dict:
        """Devuelve un estado legible para el dashboard."""
        return {
            "gripper_status": self._gripper_state,
            "evento": self.ultimo_evento,
            "timestamp": self._timestamp_evento,
        }