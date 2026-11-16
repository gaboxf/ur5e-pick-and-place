import time
import json
import math
import sys
from pathlib import Path

# Ejecución directa (python core/robot_controller.py): agregar la raíz del
# proyecto al path para poder importar el paquete "core".
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import get_logger

logger = get_logger("RobotController")

from vision.pipeline import PipelineVision

from gripper.tools.gripper_client import GripperClient
from core.ur5_client import UR5Client

class RobotController:
    """Orquestador principal para el UR5.
    Integra la Visión, el Robot UR5e y el Gripper.
    """

    def __init__(self, config_path="vision/config.json"):
        self.pipeline = PipelineVision(config_path)
        with open(config_path, "r") as f:
            self.config = json.load(f)
        
        gripper_port = self.config.get("gripper_port", "COM3")
        self.gripper = GripperClient(gripper_port)
        
        # Integración con cliente UR5 real
        self.robot = UR5Client()
        
    def conectar(self) -> bool:
        if not self.pipeline.conectar_camara():
            return False
        if not self.gripper.connect():
            return False
        if not self.robot.connect():
            return False
        return True

    def desconectar(self):
        self.robot.disconnect()
        self.pipeline.desconectar()
        self.gripper.close_connection()

    def mover_a(self, x, y, z, rx=3.14, ry=0.0, rz=0.0):
        """Interfaz para mover el UR5 (Dominio B).
        Implementa el contrato 4.4 del plan de trabajo.
        Accepts optional 4th parameter r for future orientation support.
        """
        # Construir pose completa [x, y, z, rx, ry, rz] con orientación por defecto
        # Siempre forzar orientación hacia abajo para evitar path sanity checks
        return self.robot.move_l(x, y, z, rx, ry, rz)

    def detener(self):
        """Parada de seguridad: interrumpe el movimiento del robot en curso."""
        self.robot.stop()


    def ejecutar_ciclo(self) -> bool:
        """Ejecuta un ciclo completo de Pick and Place guiado por visión:
        1. Captura y detecta pieza (Contrato 4.1).
        2. Si ok=false, no hace nada.
        3. Si ok=true, calcula trayectorias y ejecuta agarre y clasificación.
        """
        # 1. Obtener detección del módulo de visión
        deteccion = self.pipeline.procesar_frame()
        logger.debug("Resultado de visión: %s", deteccion)

        if not deteccion.get("ok"):
            logger.warning("Ciclo abortado: %s", deteccion.get("motivo_fallo"))
            return False

        categoria = deteccion["categoria"]
        x_ur5 = deteccion["x_ur5"]
        y_ur5 = deteccion["y_ur5"]
        z_seguro = self.config.get("z_seguro", 0.200)
        z_pick = self.config.get("z_pick", 0.050)
        z_deposit = self.config.get("z_deposit", 0.050)

        logger.info("Iniciando recogida de pieza categoría [%s] en (%.3f, %.3f)", categoria, x_ur5, y_ur5)

        # 2. Secuencia física del UR5 con handshake y confirmación de agarre
        # Acercamiento seguro sobre la pieza
        if not self.mover_a(x_ur5, y_ur5, z_seguro):
            logger.error("Aproximación inicial fallida")
            return False

        # Descenso para recogida
        if not self.mover_a(x_ur5, y_ur5, z_pick):
            logger.error("Descenso a cota de pick fallido")
            return False

        # Accionar gripper (Cerrar con confirmación del firmware)
        logger.debug("Cerrando gripper...")
        if not self.gripper.close():
            logger.error("Cierre de gripper fallido (sin confirmación DONE)")
            return False

        # Levantar a cota segura
        if not self.mover_a(x_ur5, y_ur5, z_seguro):
            logger.error("Elevación post-pick fallida")
            return False

        # Obtener destino según categoría (A -> Zona A, B -> Zona B)
        destinos = self.config.get("destinos", {
            "A": {"x": 0.40, "y": 0.20},
            "B": {"x": 0.40, "y": -0.20}
        })
        destino = destinos.get(categoria, {"x": 0.40, "y": 0.00})

        # Desplazamiento leve hacia el destino para despejar la vista cenital sobre la mesa
        dx = destino["x"] - x_ur5
        dy = destino["y"] - y_ur5
        dist_dest = math.hypot(dx, dy)
        if dist_dest > 0.08:
            x_check = x_ur5 + (dx / dist_dest) * 0.08
            y_check = y_ur5 + (dy / dist_dest) * 0.08
        else:
            x_check, y_check = destino["x"], destino["y"]

        if not self.mover_a(x_check, y_check, z_seguro):
            logger.error("Desplazamiento a verificación fallido")
            return False

        # Verificación visual de agarre (Closed-Loop Vision)
        time.sleep(0.15)
        det_verif = self.pipeline.procesar_frame()
        if det_verif.get("ok"):
            dist_pick = math.hypot(det_verif["x_ur5"] - x_ur5, det_verif["y_ur5"] - y_ur5)
            if dist_pick < 0.050:
                logger.warning(
                    "Fallo de agarre detectado por visión: la pieza sigue en la mesa en (%.3f, %.3f). "
                    "Distancia al pick: %.1f mm",
                    det_verif["x_ur5"], det_verif["y_ur5"], dist_pick * 1000
                )
                self.gripper.open()
                self.mover_a(0.30, 0.0, z_seguro)
                return False

        logger.info("Confirmación visual OK: la pieza fue levantada exitosamente.")

        # Transporte a zona de clasificación
        logger.info("Transportando pieza a categoría %s -> (%.3f, %.3f)", categoria, destino["x"], destino["y"])
        if not self.mover_a(destino["x"], destino["y"], z_seguro):
            logger.error("Transporte hacia destino fallido")
            return False

        # Descender a zona de depósito
        if not self.mover_a(destino["x"], destino["y"], z_deposit):
            logger.error("Descenso a cota de depósito fallido")
            return False

        # Liberar pieza (Abrir gripper)
        logger.debug("Abriendo gripper...")
        if not self.gripper.open():
            logger.warning("Apertura de gripper no reportó confirmación normal.")

        # Retirada segura
        if not self.mover_a(destino["x"], destino["y"], z_seguro):
            logger.error("Retirada post-depósito fallida")
            return False

        logger.info("¡Ciclo de clasificación completado con éxito!")
        return True


# Ejemplo de ejecución del controlador integrado
if __name__ == "__main__":
    from core.logging_config import configurar_logging
    configurar_logging()

    # Asegurar valores por defecto en config.json si faltan
    config_path = RAIZ_PROYECTO / "vision" / "config.json"
    with open(config_path, "r") as f:
        cfg_data = json.load(f)
    
    # Agregar parámetros de robot si no están
    cfg_data.setdefault("gripper_port", "COM3")
    cfg_data.setdefault("z_seguro", 0.200)
    cfg_data.setdefault("z_pick", 0.050)
    cfg_data.setdefault("z_deposit", 0.050)
    cfg_data.setdefault("destinos", {
        "A": {"x": 0.40, "y": 0.20},
        "B": {"x": 0.40, "y": -0.20}
    })
    with open(config_path, "w") as f:
        json.dump(cfg_data, f, indent=2)

    controller = RobotController(config_path)
    if controller.conectar():
        try:
            print("\n--- Ejecutando ciclo de prueba (Ctrl+C para salir) ---")
            controller.ejecutar_ciclo()
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.")
        finally:
            controller.desconectar()
    else:
        print("No se pudo iniciar el controlador integrado.")
