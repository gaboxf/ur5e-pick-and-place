import time
import json
import math
import sys
import threading
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
from core.robot_tracker import RastreadorMovimiento

class RobotController:
    """Orquestador principal para el UR5.
    Integra la Visión, el Robot UR5e y el Gripper.
    """

    def __init__(self, config_path="vision/config.json"):
        _cfg = Path(config_path)
        if not _cfg.is_absolute():
            _cfg = RAIZ_PROYECTO / _cfg
        self.config_path = _cfg
        self.pipeline = PipelineVision(str(_cfg))
        with open(_cfg, "r", encoding="utf-8") as f:
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

        # Rastreo de la pose del TCP para el "mapa de trabajo" de los dashboards.
        # Un hilo muestrea la pose a ~20 Hz, de modo que el rastro se dibuja
        # también mientras el robot se mueve (aunque el hilo de UI esté ocupado).
        self.tracker = RastreadorMovimiento()
        self._rastreando = False
        self._hilo_rastreo: threading.Thread | None = None

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

    # ------------------------------------------------------------- rastreo
    def iniciar_rastreo(self) -> None:
        """Arranca el hilo que muestrea la pose del TCP."""
        if self._hilo_rastreo and self._hilo_rastreo.is_alive():
            return
        self._rastreando = True
        self._hilo_rastreo = threading.Thread(
            target=self._bucle_rastreo, name="RastreoTCP", daemon=True
        )
        self._hilo_rastreo.start()

    def detener_rastreo(self) -> None:
        self._rastreando = False
        hilo = self._hilo_rastreo
        if hilo and hilo.is_alive() and hilo is not threading.current_thread():
            hilo.join(timeout=1.0)

    def _bucle_rastreo(self) -> None:
        while self._rastreando:
            try:
                pose = self.robot.get_actual_tcp_pose()
                if pose:
                    self.tracker.actualizar(pose[0], pose[1], pose[2])
            except Exception:
                # Una lectura fallida no debe cortar el bucle ni la operación
                logger.debug("Lectura de pose TCP fallida durante el rastreo.")
            time.sleep(0.05)

    def pose_actual(self) -> list[float] | None:
        """Pose actual del TCP ([x, y, z, rx, ry, rz]) o None si no hay datos."""
        try:
            return self.robot.get_actual_tcp_pose()
        except Exception:
            return None

    def datos_robot(self) -> dict:
        """Snapshot del rastreador para los dashboards."""
        return self.tracker.datos()

    def limpiar_rastro(self) -> None:
        self.tracker.limpiar()

    # -- Sistema de trabajo -------------------------------------------------
    def punto_base(self, x: float, y: float) -> tuple[float, float]:
        """Convierte un punto del sistema de trabajo (Home) a base del robot."""
        return x + self.origen_x, y + self.origen_y

    def punto_pick(self, x: float, y: float) -> tuple[float, float]:
        """Aplica el offset entre el centro detectado y el centro real de agarre.

        La homografía entrega el centro de la pieza en el plano calibrado, pero
        el punto que cierra las pinzas es el centro del gripper, que puede estar
        desplazado respecto del TCP (largo de la herramienta, TCP configurado en
        la brida, parallax de la cámara, etc.). Ese desplazamiento constante se
        mide con `calibrar_offset_pick_actual()` y se guarda en config.json como
        `pick_offset_x` / `pick_offset_y` (metros, en coordenadas de base).
        """
        off_x = float(self.config.get("pick_offset_x", 0.0))
        off_y = float(self.config.get("pick_offset_y", 0.0))
        return x + off_x, y + off_y

    def z_deposito(self) -> float:
        """Cota de liberación: nunca por debajo de la altura de agarre.

        La pieza se suelta apoyándola en la misma mesa de la que se levantó,
        así que `z_deposit` debe ser ~= `z_pick` (unos milímetros arriba para
        no arrastrarla). Si alguien recalibra `z_pick` más arriba, el valor
        guardado de `z_deposit` quedaría por debajo y chocaría: se limita.
        """
        z_pick = float(self.config.get("z_pick", 0.050))
        return max(float(self.config.get("z_deposit", z_pick)), z_pick)

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

        self.iniciar_rastreo()

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
                self.ultimo_error = (
                    self.robot.ultimo_error or "Falló la elevación previa al movimiento a Home."
                )
                logger.error(self.ultimo_error)
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

    # -------------------------------------------------- calibración en vivo
    def guardar_config(self) -> bool:
        """Persiste la configuración actualizada en config.json."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            logger.info("Configuración guardada en %s", self.config_path)
            return True
        except Exception:
            logger.exception("Error guardando %s", self.config_path)
            return False

    def calibrar_home_actual(self) -> tuple[bool, str]:
        """Captura la pose actual del TCP y la establece como nuevo Home."""
        pose = self.pose_actual()
        if not pose:
            return False, "No se pudo leer la pose del robot."
        anterior = list(self.home_pose) if self.home_pose else None
        self.home_pose = [float(v) for v in pose]
        self.config["home_pose"] = self.home_pose
        self.guardar_config()

        aviso = ""
        if self.config.get("usar_home_como_origen", False):
            self._sincronizar_origen_pipeline()
            # Los puntos_ur5 se guardan como offset desde Home: si Home se
            # mueve, la homografía queda corrida y hay que volver a medirla.
            if anterior is not None:
                dx = self.home_pose[0] - anterior[0]
                dy = self.home_pose[1] - anterior[1]
                desplazamiento = math.hypot(dx, dy)
                if desplazamiento > self.home_tol_m:
                    logger.warning(
                        "Home cambió %.0f mm: los puntos_ur5 son offsets desde "
                        "Home, así que la homografía quedó desactualizada. "
                        "Recalibrá con: python tools/calibrar_homografia.py",
                        desplazamiento * 1000,
                    )
                    aviso = (" · Ojo: cambió el origen; recalibrá la homografía "
                             "(puntos_ur5 se miden desde Home).")

        msg = f"Home fijado en X={self.home_pose[0]:.3f}, Y={self.home_pose[1]:.3f}, Z={self.home_pose[2]:.3f} m"
        logger.info(msg)
        return True, msg + aviso

    def _sincronizar_origen_pipeline(self) -> None:
        """Mantiene el origen de visión igual al Home vigente.

        `RobotController` y `TransformadorCoordenadas` guardan cada uno su
        copia del origen. Si solo se actualizaba la del controlador (al
        recalibrar Home en vivo), la visión seguía sumando el Home viejo y
        las piezas se mapeaban decenas de centímetros corridas.
        """
        self.origen_x = float(self.home_pose[0])
        self.origen_y = float(self.home_pose[1])
        self.pipeline.cfg.origen_x = self.origen_x
        self.pipeline.cfg.origen_y = self.origen_y
        self.pipeline.transformador.origen_x = self.origen_x
        self.pipeline.transformador.origen_y = self.origen_y
        logger.info(
            "Origen de trabajo sincronizado con Home: (%.3f, %.3f) m",
            self.origen_x, self.origen_y,
        )

    def calibrar_z_pick_actual(self) -> tuple[bool, str]:
        """Captura la altura Z actual del TCP y la establece como cota de agarre (z_pick)."""
        pose = self.pose_actual()
        if not pose:
            return False, "No se pudo leer la pose del robot."
        z = round(float(pose[2]), 4)
        self.config["z_pick"] = z
        self.guardar_config()
        msg = f"Z Pick fijado en {z*1000:.1f} mm ({z:.4f} m)"
        logger.info(msg)
        return True, msg

    def calibrar_offset_pick_actual(self) -> tuple[bool, str]:
        """Mide el offset XY entre la pieza detectada y el TCP actual.

        Procedimiento (con una pieza apoyada en la mesa):
          1. Dejar que el pipeline detecte esa pieza (panel Detección de visión).
          2. Con el teach pendant / freedrive llevar el TCP hasta el centro
             exacto de la pieza, a la altura de agarre, y sin moverlo.
          3. Ejecutar esta calibración: guarda
                pick_offset_x = TCP_x - x_ur5_detectado
                pick_offset_y = TCP_y - y_ur5_detectado
        En el dashboard web es el botón/acción `offset_pick`; en el de
        escritorio la tecla [O].
        """
        det = self.pipeline.procesar_frame()
        if not det.get("ok"):
            return False, (
                "No se pudo detectar la pieza: "
                f"{det.get('motivo_fallo', 'sin detección')}"
            )
        pose = self.pose_actual()
        if not pose:
            return False, "No se pudo leer la pose del robot."

        x_det, y_det = float(det["x_ur5"]), float(det["y_ur5"])
        off_x = round(float(pose[0]) - x_det, 4)
        off_y = round(float(pose[1]) - y_det, 4)
        self.config["pick_offset_x"] = off_x
        self.config["pick_offset_y"] = off_y
        self.guardar_config()

        msg = (
            f"Offset de pick fijado en X={off_x * 1000:+.1f} mm, "
            f"Y={off_y * 1000:+.1f} mm (pieza en {x_det:.3f}, {y_det:.3f} m; "
            f"TCP en {pose[0]:.3f}, {pose[1]:.3f} m)"
        )
        logger.info(msg)
        return True, msg

    def calibrar_z_seguro_actual(self) -> tuple[bool, str]:
        """Captura la altura Z actual del TCP y la establece como cota segura (z_seguro)."""
        pose = self.pose_actual()
        if not pose:
            return False, "No se pudo leer la pose del robot."
        z = round(float(pose[2]), 4)
        self.config["z_seguro"] = z
        self.guardar_config()
        msg = f"Z Seguro fijado en {z*1000:.1f} mm ({z:.4f} m)"
        logger.info(msg)
        return True, msg

    def calibrar_destino_actual(self, categoria: str) -> tuple[bool, str]:
        """Captura la posición X,Y actual del TCP como destino para la categoría dada (ej: 'A' o 'B')."""
        pose = self.pose_actual()
        if not pose:
            return False, "No se pudo leer la pose del robot."
        cat = categoria.upper()
        x_base, y_base = float(pose[0]), float(pose[1])
        if self.config.get("usar_home_como_origen", False) and self.home_pose:
            x_rel = round(x_base - self.home_pose[0], 4)
            y_rel = round(y_base - self.home_pose[1], 4)
        else:
            x_rel, y_rel = round(x_base, 4), round(y_base, 4)

        self.config.setdefault("destinos", {})[cat] = {"x": x_rel, "y": y_rel}
        self.guardar_config()
        msg = f"Destino {cat} fijado en ({x_rel:.3f}, {y_rel:.3f}) m"
        logger.info(msg)
        return True, msg

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
        self.detener_rastreo()
        self.robot.disconnect()
        self.pipeline.desconectar()
        self.gripper.close_connection()

    def mover_a(self, x, y, z, rx=3.14, ry=0.0, rz=0.0):
        """Interfaz para mover el UR5 (Dominio B).
        Implementa el contrato 4.4 del plan de trabajo.
        Acepta parámetros opcionales de orientación (rx, ry, rz).
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

        # Registrar el destino antes de mover: el mapa lo dibuja como objetivo
        self.tracker.fijar_destino(x, y, z)
        if not (self._hilo_rastreo and self._hilo_rastreo.is_alive()):
            self.iniciar_rastreo()

        # Construir pose completa [x, y, z, rx, ry, rz] con orientación por defecto
        # Siempre forzar orientación hacia abajo para evitar path sanity checks
        ok = self.robot.move_l(x, y, z, rx, ry, rz)
        # Muestra final para que la posición actual no dependa del hilo de rastreo
        if ok:
            self.tracker.actualizar(x, y, z)
        else:
            self.ultimo_error = (
                self.robot.ultimo_error
                or f"El robot no pudo ir a ({x:.3f}, {y:.3f}, {z:.3f})."
            )
        return ok

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
        z_seguro = float(self.config.get("z_seguro", 0.200))
        z_pick = float(self.config.get("z_pick", 0.050))
        z_deposit = self.z_deposito()
        # Centro real de agarre = centro detectado + offset de herramienta.
        x_pick, y_pick = self.punto_pick(x_ur5, y_ur5)
        if (x_pick, y_pick) != (x_ur5, y_ur5):
            logger.info(
                "Offset de pick aplicado: (%.1f, %.1f) mm -> destino (%.3f, %.3f) m",
                (x_pick - x_ur5) * 1000, (y_pick - y_ur5) * 1000, x_pick, y_pick,
            )

        logger.info("Iniciando recogida de pieza categoría [%s] en (%.3f, %.3f)", categoria, x_ur5, y_ur5)

        # 2. Secuencia física del UR5 con handshake y confirmación de agarre
        # Acercamiento seguro sobre la pieza
        if not self.mover_a(x_pick, y_pick, z_seguro):
            logger.error("Aproximación inicial fallida")
            return False

        # Descenso para recogida
        if not self.mover_a(x_pick, y_pick, z_pick):
            logger.error("Descenso a cota de pick fallido")
            return False

        # Accionar gripper (Cerrar con confirmación del firmware)
        logger.debug("Cerrando gripper...")
        if not self.gripper.close():
            logger.error("Cierre de gripper fallido (sin confirmación DONE)")
            return False

        # Levantar a cota segura
        if not self.mover_a(x_pick, y_pick, z_seguro):
            logger.error("Elevación post-pick fallida")
            return False

        # Obtener destino según categoría (A -> Zona A, B -> Zona B)
        destino = self.destino_para(categoria)

        # Desplazamiento leve hacia el destino para despejar la vista cenital sobre la mesa
        dx = destino["x"] - x_pick
        dy = destino["y"] - y_pick
        dist_dest = math.hypot(dx, dy)
        if dist_dest > 0.08:
            x_check = x_pick + (dx / dist_dest) * 0.08
            y_check = y_pick + (dy / dist_dest) * 0.08
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
