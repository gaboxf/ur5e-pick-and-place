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

        self.rtde_c = None
        self.rtde_r = None
        self.connected = False

        # Bandera de aborto: la usan stop() y move_l() para cortar un movimiento.
        self._abort = threading.Event()

    def connect(self):
        if self.mode == "real" and HAS_UR_RTDE:
            try:
                self.rtde_c = rtde_control.RTDEControlInterface(self.ip)
                self.rtde_r = rtde_receive.RTDEReceiveInterface(self.ip)
                self.connected = True
                logger.info("Conectado al UR5 real en %s", self.ip)
                return True
            except Exception as e:
                logger.error("Error conectando al UR5: %s", e)
                return False
        else:
            logger.info("Modo %s: Simulando conexión UR5.", self.mode)
            self.connected = True
            return True

    def move_l(self, x, y, z, rx=None, ry=None, rz=None):
        """Mueve el UR5 a (x, y, z) en metros.

        La orientación (rx, ry, rz) es opcional: si no se indica, se conserva
        la orientación actual del TCP.
        """
        self._abort.clear()
        target_pose = [x, y, z, rx, ry, rz]

        if self.mode == "real" and self.connected:
            if rx is None or ry is None or rz is None:
                actual = self.rtde_r.getActualTCPPose()
                target_pose = [x, y, z, actual[3], actual[4], actual[5]]
            if self._abort.is_set():
                logger.warning("Movimiento abortado antes de iniciar.")
                return False
            try:
                # moveL es bloqueante; stop() puede interrumpirlo via stopJ.
                self.rtde_c.moveL(target_pose, self.vel, self.acc)
                return not self._abort.is_set()
            except Exception as e:
                logger.warning("moveL interrumpido o con error: %s", e)
                return False
        else:
            logger.debug("[SIMULACIÓN UR5] moveL: %s", target_pose)
            # En simulación dormimos en tramos cortos para poder abortar.
            fin = time.time() + float(os.getenv("MOCK_DELAY", 1.0))
            while time.time() < fin:
                if self._abort.is_set():
                    logger.warning("[SIMULACIÓN UR5] movimiento abortado.")
                    return False
                time.sleep(0.02)
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