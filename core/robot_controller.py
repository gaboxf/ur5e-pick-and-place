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

        # Pose Home compartida por los dashboards, la voz y la web.
        # Se define en vision/config.json como [x, y, z, rx, ry, rz].
        self.home_pose = self._leer_home_pose()
        self.home_tol_m = float(self.config.get("home_tol_m", 0.005))
        self.home_tol_rad = float(self.config.get("home_tol_rad", 0.05))
        self.home_al_iniciar = bool(self.config.get("home_al_iniciar", True))

        gripper_port = self.config.get("gripper_port", "COM3")
        self.gripper = GripperClient(gripper_port)
        
        # Integración con cliente UR5 real
        self.robot = UR5Client()

        # Origen del sistema de trabajo que usa la homografía: (0,0) = base del
        # robot, o la XY de Home si config.json define usar_home_como_origen.
        self.origen_x = float(getattr(self.pipeline.cfg, "origen_x", 0.0))
        self.origen_y = float(getattr(self.pipeline.cfg, "origen_y", 0.0))
        # Distancia horizontal mínima a la base: por debajo, el TCP quedaría
        # sobre la propia columna del robot. Se rechaza el movimiento.
        self.radio_minimo_base = float(self.config.get("radio_minimo_base", 0.20))
        # Motivo del último movimiento rechazado/fallido, para mostrarlo en los
        # dashboards (antes solo quedaba en el log de Python).
        self.ultimo_error: str | None = None

    # -- Sistema de trabajo -------------------------------------------------
    def punto_base(self, x: float, y: float) -> tuple[float, float]:
        """Convierte un punto del sistema de trabajo (Home) a base del robot."""
        return x + self.origen_x, y + self.origen_y

    def destino_para(self, categoria: str) -> dict[str, float]:
        """Devuelve el destino de depósito de una categoría en base del robot.

        Los valores de `destinos` en config.json están en el mismo sistema que
        `puntos_ur5` (relativos a Home si el origen está activo).
        """
        destinos = self.config.get("destinos", {
            "A": {"x": 0.40, "y": 0.20},
            "B": {"x": 0.40, "y": -0.20},
        })
        dest = destinos.get(categoria) or {"x": 0.40, "y": 0.00}
        x, y = self.punto_base(float(dest["x"]), float(dest["y"]))
        return {"x": x, "y": y}

    def _leer_home_pose(self) -> list[float] | None:
        """Lee y valida la pose Home desde config.json.

        Formato esperado: [x, y, z, rx, ry, rz] en metros y radianes, tal
        como la devuelve rtde_r.getActualTCPPose().
        """
        home = self.config.get("home_pose")
        if not home:
            logger.warning(
                "config.json no define 'home_pose': el comando Home quedará deshabilitado."
            )
            return None
        try:
            pose = [float(v) for v in home]
        except (TypeError, ValueError):
            logger.error("'home_pose' inválida en config.json: %r", home)
            return None
        if len(pose) != 6:
            logger.error(
                "'home_pose' debe tener 6 valores [x,y,z,rx,ry,rz]; se recibieron %d.",
                len(pose),
            )
            return None
        return pose

    @staticmethod
    def _dif_angulo(a: float, b: float) -> float:
        """Diferencia angular mínima entre dos componentes, en [0, pi] rad."""
        return abs((a - b + math.pi) % (2 * math.pi) - math.pi)

    def distancia_a_home(self) -> float | None:
        """Distancia lineal (m) entre el TCP actual y Home."""
        if not self.home_pose:
            return None
        actual = self.robot.get_actual_tcp_pose()
        return math.sqrt(
            (actual[0] - self.home_pose[0]) ** 2
            + (actual[1] - self.home_pose[1]) ** 2
            + (actual[2] - self.home_pose[2]) ** 2
        )

    def esta_en_home(self, tol_m: float | None = None) -> bool:
        """True si el TCP está en Home dentro de las tolerancias configuradas."""
        if not self.home_pose:
            return False

        tol = self.home_tol_m if tol_m is None else tol_m
        actual = self.robot.get_actual_tcp_pose()
        dist = math.sqrt(
            sum((actual[i] - self.home_pose[i]) ** 2 for i in range(3))
        )
        if dist > tol:
            return False

        # La orientación se compara con diferencia angular envuelta para
        # tolerar representaciones equivalentes cerca de +/-pi.
        dif_rot = max(
            self._dif_angulo(actual[i], self.home_pose[i]) for i in (3, 4, 5)
        )
        return dif_rot <= self.home_tol_rad

    def ir_a_home(self, forzar: bool = False) -> bool:
        """Mueve el robot a la pose Home definida en config.json.

        Si ya está dentro de tolerancia (y `forzar` es False) no se mueve.
        Si el TCP está por debajo de la altura de Home, primero sube en
        vertical para no arrastrar la herramienta por la mesa.
        """
        if not self.home_pose:
            self.ultimo_error = "Home no configurado: falta 'home_pose' en config.json."
            logger.error(self.ultimo_error)
            return False
        if not self.robot.connected:
            self.ultimo_error = "No se puede ir a Home: el robot no está conectado."
            logger.error(self.ultimo_error)
            return False
        self.ultimo_error = None

        if not forzar and self.esta_en_home():
            logger.info(
                "Robot ya está en Home (tol %.1f mm / %.2f rad).",
                self.home_tol_m * 1000, self.home_tol_rad,
            )
            return True

        actual = self.robot.get_actual_tcp_pose()
        if actual[2] < self.home_pose[2] - 0.001:
            logger.info(
                "Elevando de Z=%.3f a Z=%.3f antes de desplazar a Home...",
                actual[2], self.home_pose[2],
            )
            if not self.robot.move_l(
                actual[0], actual[1], self.home_pose[2],
                actual[3], actual[4], actual[5],
            ):
                logger.error("Falló la elevación previa al movimiento a Home.")
                return False

        logger.info(
            "Moviendo a Home: pos=(%.3f, %.3f, %.3f) rot=(%.3f, %.3f, %.3f)",
            *self.home_pose,
        )
        if not self.robot.move_l(*self.home_pose, tol_m=self.home_tol_m):
            self.ultimo_error = (
                self.robot.ultimo_error or "No se pudo alcanzar la pose Home."
            )
            logger.error(self.ultimo_error)
            return False

        logger.info("Robot en Home.")
        return True

    def asegurar_home(self) -> bool:
        """Verifica Home al iniciar y corrige la pose si el robot está fuera."""
        if not self.home_al_iniciar:
            logger.info("Home automático al iniciar deshabilitado (home_al_iniciar=false).")
            return True
        if not self.home_pose:
            return False
        logger.info("Verificando posición Home al iniciar...")
        return self.ir_a_home()

    def conectar(self) -> bool:
        """Conecta cámara, gripper y robot.

        Cada subsistema se intenta por separado: un fallo en uno no impide
        probar los demás (antes, si la cámara fallaba, el gripper ni siquiera
        se intentaba conectar). Devuelve True solo si todo quedó operativo.
        """
        try:
            camara_ok = bool(self.pipeline.conectar_camara())
        except Exception:
            logger.exception("Error conectando la cámara")
            camara_ok = False
        if not camara_ok:
            logger.error(
                "Cámara no disponible: el ciclo guiado por visión no va a funcionar."
            )

        try:
            self.gripper.connect()
            gripper_ok = not self.gripper.simulado
        except Exception:
            logger.exception("Error conectando el gripper")
            gripper_ok = False
        if not gripper_ok:
            logger.warning("Gripper en MODO SIMULADO (no se moverá el hardware).")

        robot_ok = bool(self.robot.connect())
        if not robot_ok:
            logger.error("Robot UR5 no conectado (modo %s).", self.robot.mode)

        return camara_ok and gripper_ok and robot_ok

    def desconectar(self):
        self.robot.disconnect()
        self.pipeline.desconectar()
        self.gripper.close_connection()

    def mover_a(self, x, y, z, rx=3.14, ry=0.0, rz=0.0):
        """Interfaz para mover el UR5 (Dominio B).
        Implementa el contrato 4.4 del plan de trabajo.
        Accepts optional 4th parameter r for future orientation support.
        """
        self.ultimo_error = None
        # Seguridad: nunca llevar el TCP sobre la columna del robot.
        radio = math.hypot(x, y)
        if radio < self.radio_minimo_base:
            self.ultimo_error = (
                f"TCP a {radio * 1000:.0f} mm de la base (mínimo "
                f"{self.radio_minimo_base * 1000:.0f} mm): destino rechazado. "
                "Revisá la calibración de la homografía (puntos_ur5) y los "
                "destinos en vision/config.json."
            )
            logger.error(
                "Movimiento rechazado: (%.3f, %.3f) está a %.0f mm de la base "
                "(mínimo permitido %.0f mm). Revisá puntos_ur5, destinos y el "
                "origen (home) en vision/config.json.",
                x, y, radio * 1000, self.radio_minimo_base * 1000,
            )
            return False
        # Construir pose completa [x, y, z, rx, ry, rz] con orientación por defecto
        # Siempre forzar orientación hacia abajo para evitar path sanity checks
        if not self.robot.move_l(x, y, z, rx, ry, rz):
            self.ultimo_error = (
                self.robot.ultimo_error
                or f"El robot no pudo ir a ({x:.3f}, {y:.3f}, {z:.3f})."
            )
            return False
        return True

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
        destino = self.destino_para(categoria)

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
                self.mover_a(*self.punto_base(0.30, 0.0), z_seguro)
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
