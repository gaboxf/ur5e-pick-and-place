import math
import os
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

# Intentar importar ur_rtde (si falla, estaremos en modo simulación/mock)
try:
    import rtde_control
    import rtde_receive
    HAS_UR_RTDE = True
except ImportError:
    HAS_UR_RTDE = False

from core.logging_config import get_logger

logger = get_logger("UR5Controller")
_INTERVALO_LOG_FALLO_POSE_S = 30.0

# Modos del robot (RTDE getRobotMode). Sirven para explicar por qué el robot
# rechaza un movimiento en vez de mostrar un "no se pudo mover" sin motivo.
_MODOS_ROBOT = {
    -1: "sin controlador",
    0: "desconectado",
    1: "confirmar seguridad",
    2: "arrancando",
    3: "apagado",
    4: "encendido",
    5: "en reposo",
    6: "freedrive/backdrive",
    7: "ejecutando",
    8: "actualizando firmware",
}


class UR5Client:
    def __init__(self):
        load_dotenv()
        self.mode = os.getenv("ROBOT_MODE", "mock")
        self.ip = os.getenv("UR5_IP", "192.168.1.10")
        self.vel = float(os.getenv("ROBOT_VEL", 0.25))
        self.acc = float(os.getenv("ROBOT_ACC", 1.25))
        # Tolerancia milimétrica para el handshake de llegada (en metros)
        self.tolerancia_pos = float(os.getenv("ROBOT_TOL_M", 0.003))

        self.rtde_c = None
        self.rtde_r = None
        self.connected = False
        # Última causa de fallo, legible por los dashboards (para mostrarla en
        # pantalla en vez de un "no se pudo mover" sin explicación).
        self.ultimo_error: str | None = None
        self._lock_log_pose = threading.Lock()
        self._ultimo_log_fallo_pose = -math.inf

        # Bandera de aborto: la usan stop() y move_l() para cortar un movimiento.
        self._abort = threading.Event()
        self._parada_retenida = threading.Event()
        # Pose simulada para modo mock
        self._mock_pose = [0.30, 0.0, 0.25, 3.14, 0.0, 0.0]

    def connect(self):
        if self.mode != "real":
            logger.info("Modo %s: Simulando conexión UR5.", self.mode)
            self.connected = True
            self.ultimo_error = None
            return True

        if not HAS_UR_RTDE:
            self.ultimo_error = (
                "Falta la librería ur_rtde (pip install ur-rtde) con "
                "ROBOT_MODE=real."
            )
            logger.error(self.ultimo_error)
            self.connected = False
            return False

        try:
            self.rtde_c = rtde_control.RTDEControlInterface(self.ip)
            self.rtde_r = rtde_receive.RTDEReceiveInterface(self.ip)
            self.connected = True
            self.ultimo_error = None
            logger.info("Conectado al UR5 real en %s", self.ip)
            return True
        except Exception as e:
            self.ultimo_error = f"No se pudo conectar al UR5 ({self.ip}): {e}"
            logger.error(self.ultimo_error)
            self.connected = False
            return False

    def get_actual_tcp_pose(self) -> list[float] | None:
        """Pose actual del TCP [x, y, z, rx, ry, rz], o None si no hay lectura válida.

        En modo REAL nunca se devuelve la pose simulada: si RTDE falla, eso
        sería peor que no tener dato (una calibración guardó una vez la pose
        mock [0.30, 0, 0.25] como si fuera el TCP y mandó el robot 94 cm al
        lado contrario). Los llamadores deben chequear `if pose:`.
        """
        if self.mode == "real":
            if not (self.connected and self.rtde_r):
                self._registrar_fallo_pose("No hay conexión RTDE para leer la pose del TCP.")
                return None
            try:
                return self.rtde_r.getActualTCPPose()
            except Exception as e:
                self._registrar_fallo_pose(f"Error leyendo pose TCP por RTDE: {e}")
                return None
        # Modos mock/sim: pose virtual para el mapa y las pruebas.
        return list(self._mock_pose)

    def _registrar_fallo_pose(self, mensaje: str) -> None:
        """Limita los avisos de pose que comparten el rastreo y los dashboards."""
        with self._lock_log_pose:
            ahora = time.monotonic()
            if ahora - self._ultimo_log_fallo_pose < _INTERVALO_LOG_FALLO_POSE_S:
                return
            self._ultimo_log_fallo_pose = ahora
        logger.error(mensaje)

    def is_steady(self, vel_umbral: float = 0.01) -> bool:
        """Verifica si el robot está completamente quieto mediante RTDE."""
        if self.mode == "real" and self.connected and self.rtde_r:
            try:
                speeds = self.rtde_r.getActualTCPSpeed()
                v_lin = math.sqrt(speeds[0] ** 2 + speeds[1] ** 2 + speeds[2] ** 2)
                return v_lin < vel_umbral
            except Exception as e:
                logger.debug("Error comprobando quietud del robot: %s", e)
                return True
        return True

    # ------------------------------------------------------- diagnósticos
    def _estado_legible(self) -> str:
        """Resumen del estado del robot para acompañar los mensajes de error.

        Con ur_rtde se puede saber si el pendant está en Teach/Freedrive, si
        hay un paro protector o de emergencia y en qué modo está el robot.
        Antes, todo eso se resumía en "moveL cancelado o abortado", sin pistas.
        """
        if not (self.rtde_c and self.rtde_r):
            return ""

        partes: list[str] = []
        try:
            status = self.rtde_c.getRobotStatus()
            if not status & 0x01:
                partes.append("robot sin alimentación")
            if status & 0x04:  # bit 2: botón Teach/Freedrive del pendant
                partes.append("botón Teach/Freedrive del pendant activo")
        except Exception:
            pass  # el diagnóstico nunca debe tapar el error original

        try:
            modo = self.rtde_r.getRobotMode()
            partes.append(f"modo {_MODOS_ROBOT.get(modo, modo)}")
        except Exception:
            pass

        try:
            if self.rtde_c.getFreedriveStatus() > 0:
                partes.append("freedrive activo")
        except Exception:
            pass

        try:
            if self.rtde_r.isEmergencyStopped():
                partes.append("PARO DE EMERGENCIA")
            elif self.rtde_r.isProtectiveStopped():
                partes.append("PARO PROTECTOR")
        except Exception:
            pass

        return ", ".join(partes)

    def _motivo_bloqueo(self) -> str | None:
        """Devuelve por qué el robot no aceptaría un movimiento, o None.

        Se consulta antes de llamar a moveL para no perder segundos en un
        movimiento que el controlador va a rechazar igual: el caso típico es
        haber quedado en Freedrive después de calibrar a mano con el pendant.
        """
        if not (self.rtde_c and self.rtde_r):
            return None

        try:
            status = self.rtde_c.getRobotStatus()
            if status & 0x04:  # bit 2: botón Teach/Freedrive del pendant
                return ("el botón Teach/Freedrive del pendant está activo: "
                        "salí de Freedrive antes de mover")
        except Exception:
            pass

        try:
            if self.rtde_r.isEmergencyStopped():
                return "hay un paro de emergencia activo"
            if self.rtde_r.isProtectiveStopped():
                return ("hay un paro protector activo: liberalo/resetealo "
                        "desde el pendant")
        except Exception:
            pass

        try:
            if self.rtde_c.getFreedriveStatus() > 0:
                return "el robot está en Freedrive: salí de ese modo antes de mover"
        except Exception:
            pass

        try:
            modo = self.rtde_r.getRobotMode()
            if modo in (0, 1, 2, 3, 4, 8):
                return f"el robot está en modo {_MODOS_ROBOT.get(modo, modo)}"
        except Exception:
            pass

        return None

    def _explicar_rechazo(self, target_pose: list[float]) -> str:
        """Arma un mensaje concreto cuando moveL devuelve False."""
        motivos = ["el robot rechazó el moveL"]

        estado = self._estado_legible()
        if estado:
            motivos.append(estado)

        try:
            if not self.rtde_c.isPoseWithinSafetyLimits(target_pose):
                motivos.append(
                    "la pose objetivo está fuera del alcance o de los límites "
                    "de seguridad del robot (revisá home_pose / límites)"
                )
        except Exception:
            pass

        try:
            if not self.rtde_c.getInverseKinematicsHasSolution(target_pose):
                motivos.append(
                    "no hay solución de cinemática inversa para esa pose "
                    "(posición/orientación inalcanzable)"
                )
        except Exception:
            pass

        return "moveL rechazado: " + "; ".join(motivos) + "."

    def move_l(self, x, y, z, rx=None, ry=None, rz=None, tol_m: float | None = None) -> bool:
        """Mueve el UR5 a (x, y, z) en metros con handshake determinista.

        La orientación (rx, ry, rz) es opcional: si no se indica, se conserva
        la orientación actual del TCP.
        Valida que el robot haya alcanzado la pose dentro de tol_m antes de retornar True.
        """
        self._abort.clear()
        self.ultimo_error = None
        if self._parada_retenida.is_set():
            self.ultimo_error = "Parada activa. Reiniciá la parada antes de mover."
            return False
        target_pose = [x, y, z, rx, ry, rz]
        tol = tol_m if tol_m is not None else self.tolerancia_pos

        if self.mode == "real" and not (self.connected and self.rtde_c and self.rtde_r):
            self.ultimo_error = (
                "Modo real sin conexión activa al UR5: revisá UR5_IP en .env "
                "y que el robot esté encendido."
            )
            logger.error(self.ultimo_error)
            return False

        if self.mode == "real" and self.connected:
            if rx is None or ry is None or rz is None:
                actual = self.rtde_r.getActualTCPPose()
                target_pose = [x, y, z, actual[3], actual[4], actual[5]]
            if self._abort.is_set() or self._parada_retenida.is_set():
                self.ultimo_error = "Movimiento abortado antes de iniciar."
                logger.warning(self.ultimo_error)
                return False
            bloqueo = self._motivo_bloqueo()
            if bloqueo:
                self.ultimo_error = f"El robot no puede moverse: {bloqueo}."
                logger.error(self.ultimo_error)
                return False
            try:
                # moveL es bloqueante en ur_rtde; stop() puede interrumpirlo via stopJ.
                res = self.rtde_c.moveL(target_pose, self.vel, self.acc)
                if not res or self._abort.is_set() or self._parada_retenida.is_set():
                    if self._abort.is_set() or self._parada_retenida.is_set():
                        self.ultimo_error = "moveL cancelado por una parada de seguridad."
                    else:
                        self.ultimo_error = self._explicar_rechazo(target_pose)
                    logger.warning(self.ultimo_error)
                    return False

                # Handshake determinista por RTDE: verificar posición alcanzada
                actual = self.rtde_r.getActualTCPPose()
                dist_err = math.sqrt(
                    (actual[0] - x) ** 2 + (actual[1] - y) ** 2 + (actual[2] - z) ** 2
                )
                logger.debug(
                    "Handshake UR5 alcanzado: pos=(%.3f, %.3f, %.3f), err=%.2f mm (tol=%.2f mm)",
                    actual[0], actual[1], actual[2], dist_err * 1000, tol * 1000
                )

                if dist_err > tol:
                    self.ultimo_error = (
                        f"El robot no alcanzó el objetivo: error "
                        f"{dist_err * 1000:.1f} mm (tolerancia {tol * 1000:.1f} mm)."
                    )
                    logger.warning(self.ultimo_error)
                    return False

                return True
            except Exception as e:
                self.ultimo_error = f"moveL interrumpido o con error: {e}"
                logger.warning(self.ultimo_error)
                return False
        else:
            logger.debug("[SIMULACIÓN UR5] moveL: %s (tol=%.2f mm)", target_pose, tol * 1000)
            # En simulación se interpola la pose para que el "mapa de trabajo"
            # de los dashboards muestre una trayectoria realista y no un salto.
            duracion = max(float(os.getenv("MOCK_DELAY", 1.0)), 0.05)
            inicio = list(self._mock_pose[:3])
            t0 = time.time()
            while True:
                if self._abort.is_set() or self._parada_retenida.is_set():
                    logger.warning("[SIMULACIÓN UR5] movimiento abortado.")
                    return False
                t = min(1.0, (time.time() - t0) / duracion)
                e = t * t * (3.0 - 2.0 * t)  # suavizado (ease in-out)
                self._mock_pose[0] = inicio[0] + (x - inicio[0]) * e
                self._mock_pose[1] = inicio[1] + (y - inicio[1]) * e
                self._mock_pose[2] = inicio[2] + (z - inicio[2]) * e
                if t >= 1.0:
                    break
                time.sleep(0.02)
            # Pose final exacta
            self._mock_pose = [
                x, y, z,
                rx if rx is not None else self._mock_pose[3],
                ry if ry is not None else self._mock_pose[4],
                rz if rz is not None else self._mock_pose[5],
            ]
            return True

    def conexion_disponible(self) -> bool:
        if not self.connected:
            return False
        if self.mode != "real":
            return True
        try:
            return bool(
                self.rtde_c and self.rtde_r
                and self.rtde_c.isConnected() and self.rtde_r.isConnected()
            )
        except Exception:
            return False

    def stop(self, retener: bool = False) -> bool:
        """Bloquea nuevos movimientos y devuelve si se pudo enviar la parada."""
        if retener:
            self._parada_retenida.set()
        self._abort.set()
        if self.mode != "real":
            logger.warning("[SIMULACIÓN UR5] parada solicitada.")
            return True
        if not (self.connected and self.rtde_c):
            logger.warning("Parada local activa. Sin conexión RTDE para enviarla al UR5.")
            return False
        try:
            self.rtde_c.stopJ(5.0)
            logger.warning("UR5: parada enviada (stopJ).")
            return True
        except Exception as e:
            logger.error("No se pudo detener el UR5: %s", e)
            return False

    def reiniciar_parada(self) -> None:
        self._parada_retenida.clear()

    def disconnect(self):
        if self.rtde_c:
            self.rtde_c.disconnect()
        self.connected = False
