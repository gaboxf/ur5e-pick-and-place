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

        # Bandera de aborto: la usan stop() y move_l() para cortar un movimiento.
        self._abort = threading.Event()
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

    def get_actual_tcp_pose(self) -> list[float]:
        """Devuelve la pose actual del TCP [x, y, z, rx, ry, rz] en metros y radianes."""
        if self.mode == "real" and self.connected and self.rtde_r:
            try:
                return self.rtde_r.getActualTCPPose()
            except Exception as e:
                logger.error("Error leyendo pose TCP: %s", e)
        return list(self._mock_pose)

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

    def move_l(self, x, y, z, rx=None, ry=None, rz=None, tol_m: float | None = None) -> bool:
        """Mueve el UR5 a (x, y, z) en metros con handshake determinista.

        La orientación (rx, ry, rz) es opcional: si no se indica, se conserva
        la orientación actual del TCP.
        Valida que el robot haya alcanzado la pose dentro de tol_m antes de retornar True.
        """
        self._abort.clear()
        self.ultimo_error = None
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
            if self._abort.is_set():
                self.ultimo_error = "Movimiento abortado antes de iniciar."
                logger.warning(self.ultimo_error)
                return False
            try:
                # moveL es bloqueante en ur_rtde; stop() puede interrumpirlo via stopJ.
                res = self.rtde_c.moveL(target_pose, self.vel, self.acc)
                if not res or self._abort.is_set():
                    self.ultimo_error = "moveL cancelado o abortado durante la trayectoria."
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
            # En simulación dormimos en tramos cortos para poder abortar.
            fin = time.time() + float(os.getenv("MOCK_DELAY", 1.0))
            while time.time() < fin:
                if self._abort.is_set():
                    logger.warning("[SIMULACIÓN UR5] movimiento abortado.")
                    return False
                time.sleep(0.02)
            # Actualizar pose simulada
            self._mock_pose = [
                x, y, z,
                rx if rx is not None else self._mock_pose[3],
                ry if ry is not None else self._mock_pose[4],
                rz if rz is not None else self._mock_pose[5],
            ]
            return True

    def stop(self):
        """Parada de seguridad: interrumpe el movimiento en curso."""
        self._abort.set()
        if self.mode == "real" and self.connected and self.rtde_c:
            try:
                # stopJ con desaceleración alta para frenar rápido pero seguro.
                self.rtde_c.stopJ(5.0)
                logger.warning("UR5: parada de seguridad enviada (stopJ).")
            except Exception as e:
                logger.error("No se pudo detener el UR5: %s", e)
        else:
            logger.warning("[SIMULACIÓN UR5] parada de seguridad solicitada.")

    def disconnect(self):
        if self.rtde_c:
            self.rtde_c.disconnect()
        self.connected = False