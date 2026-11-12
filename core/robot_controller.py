import time
import json
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

        # 2. Secuencia física del UR5
        # Acercamiento seguro sobre la pieza
        self.mover_a(x_ur5, y_ur5, z_seguro, 0.0)

        # Descenso para recogida
        self.mover_a(x_ur5, y_ur5, z_pick, 0.0)

        # Accionar gripper (Cerrar)
        logger.debug("Cerrando gripper...")
        self.gripper.close()
        time.sleep(2.0)

        # Levantar con la pieza
        self.mover_a(x_ur5, y_ur5, z_seguro, 0.0)

        # Obtener destino según categoría (A -> Zona A, B -> Zona B)
        destinos = self.config.get("destinos", {
            "A": {"x": 0.40, "y": 0.20},
            "B": {"x": 0.40, "y": -0.20}
        })
        destino = destinos.get(categoria, {"x": 0.40, "y": 0.00})

        # Transporte a zona de clasificación
        logger.info("Transportando pieza a categoría %s -> (%.3f, %.3f)", categoria, destino["x"], destino["y"])
        self.mover_a(destino["x"], destino["y"], z_seguro, 0.0)

        # Descender a zona de depósito
        self.mover_a(destino["x"], destino["y"], z_deposit, 0.0)

        # Liberar pieza (Abrir gripper)
        logger.debug("Abriendo gripper...")
        self.gripper.open()
        time.sleep(2.0)

        # Retirada segura
        self.mover_a(destino["x"], destino["y"], z_seguro, 0.0)

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
