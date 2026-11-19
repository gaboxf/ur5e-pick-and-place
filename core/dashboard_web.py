"""Interfaz Web de Monitoreo Remoto — Desafío 2: Pick and Place UR5.

Servidor Flask independiente que comparte el mismo RobotController que el
dashboard de escritorio. Expone:

  GET  /               -> página HTML de monitoreo
  GET  /video          -> stream MJPEG en vivo (cámara + detección dibujada)
  GET  /api/state      -> estado actual en JSON
  GET  /stream         -> Server-Sent Events con el estado cada 0.5 s
  POST /api/command    -> comandos remotos (abrir, cerrar, home, ciclo, ...)

No usa CDN ni JavaScript externo: el navegador solo necesita HTML5.
Se puede abrir desde otra máquina de la red con http://<ip-del-pc>:5000
"""

from __future__ import annotations

import collections
import json
import math
import queue
import socket
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

# Ejecución directa (python core/dashboard_web.py): agregar la raíz del
# proyecto al path para poder importar el paquete "core".
import sys
BASE_DIR = Path(__file__).resolve().parent
RAIZ_PROYECTO = BASE_DIR.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import configurar_logging, get_logger

from core.robot_controller import RobotController
from vision.homografia_editor import EditorHomografia
from vision.hsv_editor import EditorHSV
from vision.roi_editor import EditorROI
from vision.visor import VisorCamara

logger = get_logger("WebDashboard")

NOMBRES_COLOR = {"A": "roja", "B": "azul", "C": "verde", "D": "violeta"}
NOMBRES_CATEGORIA = {"A": "A (roja)", "B": "B (azul)", "C": "C (verde)", "D": "D (violeta)"}


class WebDashboard:
    """Estado compartido entre el bucle de captura y los endpoints Flask."""

    def __init__(self, config_path: str = "vision/config.json"):
        _cfg = Path(config_path)
        self.config_path = str(_cfg if _cfg.is_absolute() else RAIZ_PROYECTO / _cfg)
        self.controller = RobotController(self.config_path)
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        # Editor visual de la ROI (comparte el mismo ConfigCamara del pipeline,
        # así los cambios de botones surten efecto en la detección al instante)
        self.editor_roi = EditorROI(self.controller.pipeline.cfg.camara)
        # Editor visual de los rangos HSV (comparte el ConfigSegmentacion)
        self.editor_hsv = EditorHSV(
            self.controller.pipeline.cfg.segmentacion,
            self.controller.pipeline.cfg.camara,
        )
        # Asistente de calibración de la homografía (comparte ConfigHomografia
        # y el transformador, para recalcular la matriz al aplicar)
        self.editor_homografia = EditorHomografia(
            self.controller.pipeline.cfg.homografia,
            self.controller.pipeline.cfg.camara,
            self.controller.pipeline.transformador,
        )

        self._lock = threading.Lock()
        self._cam_lock = threading.Lock()  # serializa escaneo / cambio de cámara
        self._running = False
        self._hilo_captura: threading.Thread | None = None
        self.cambiando_camara = False

        self.latest_jpeg: bytes | None = None
        self.clientes_sse = 0

        self.estado = "INICIANDO"
        self.deteccion: dict = {"ok": False, "motivo_fallo": "sin iniciar"}
        self.ultimo_evento: str | None = None
        self.ultimo_comando: str | None = None
        self.parada_activa = False
        self.abortar_ciclo = False
        self.camara_ok = False
        self.gripper_ok = False
        self.robot_ok = False
        self.ciclos_ok = 0
        self.ciclos_error = 0
        self.por_categoria = {"A": 0, "B": 0, "C": 0, "D": 0}
        self._t_inicio = time.time()
        self.logs_recientes: collections.deque[dict] = collections.deque(maxlen=60)
        self.registrar_log("INFO", "Consola de eventos iniciada.")

        # Parámetros de la celda que el cliente usa para dibujar el mapa de
        # trabajo a escala (mismos límites que valida la visión).
        # `zona_trabajo_*` se guarda en el sistema de trabajo (relativo a Home
        # si usar_home_como_origen), pero el TCP y la detección se dibujan en
        # coordenadas absolutas de base. Llevar la zona a base evita que el
        # rectángulo aparezca desplazado exactamente el vector de Home.
        ox = float(getattr(self.controller, "origen_x", 0.0))
        oy = float(getattr(self.controller, "origen_y", 0.0))
        self.celda_info = {
            "zona": {
                "min_x": float(self.cfg.get("zona_trabajo_min_x", 0.05)) + ox,
                "max_x": float(self.cfg.get("zona_trabajo_max_x", 0.55)) + ox,
                "min_y": float(self.cfg.get("zona_trabajo_min_y", -0.05)) + oy,
                "max_y": float(self.cfg.get("zona_trabajo_max_y", 0.45)) + oy,
            },
            "z": {
                "seguro": float(self.cfg.get("z_seguro", 0.25)),
                "pick": float(self.cfg.get("z_pick", 0.12)),
                "deposit": float(self.cfg.get("z_deposit", self.cfg.get("z_pick", 0.12))),
            },
        }

    def registrar_log(self, nivel: str, mensaje: str):
        """Registra un evento visible en la consola web."""
        hora = time.strftime("%H:%M:%S")
        with self._lock:
            self.logs_recientes.append({"hora": hora, "nivel": nivel, "mensaje": mensaje})

    # ------------------------------------------------------------- hardware
    def conectar(self):
        """Conecta los tres subsistemas sin abortar si alguno falla."""
        try:
            self.camara_ok = bool(self.controller.pipeline.conectar_camara())
        except Exception as e:
            logger.error("Cámara: %s", e)
            self.camara_ok = False

        try:
            self.gripper_ok = bool(self.controller.gripper.connect())
            self.gripper_ok = not self.controller.gripper.simulado
        except Exception as e:
            logger.error("Gripper: %s", e)
            self.gripper_ok = False

        try:
            self.robot_ok = bool(self.controller.robot.connect())
        except Exception as e:
            logger.error("Robot: %s", e)
            self.robot_ok = False

        # El rastreo de la pose alimenta el "mapa de trabajo" del dashboard
        try:
            self.controller.iniciar_rastreo()
        except Exception:
            logger.exception("No se pudo iniciar el rastreo de la pose del TCP")

        self.registrar_log(
            "OK" if self.camara_ok else "WARN",
            f"Cámara: {'OK' if self.camara_ok else 'SIN CÁMARA'}"
        )
        self.registrar_log(
            "OK" if self.gripper_ok else "WARN",
            f"Gripper: {'OK (Arduino conectado)' if self.gripper_ok else 'SIMULADO'}"
        )
        self.registrar_log(
            "INFO",
            f"Robot UR5: {'OK' if self.robot_ok else 'FALLO'} (Modo {self.controller.robot.mode.upper()})"
        )

        # Al arrancar, verificar que el robot esté en Home y corregirlo si
        # quedó fuera de posición (por ejemplo, tras una parada).
        if self.robot_ok:
            try:
                home_ok = self.controller.asegurar_home()
            except Exception as e:
                logger.error("Home al iniciar: %s", e)
                home_ok = False
            self.registrar_log(
                "OK" if home_ok else "WARN",
                "Chequeo de Home OK." if home_ok
                else "No se pudo verificar/mover el robot a Home.",
            )

        logger.info(
            "Hardware -> cámara:%s gripper:%s robot:%s",
            self.camara_ok, self.gripper_ok, self.robot_ok,
        )

    # ------------------------------------------------------- selección cámara
    def indices_camaras(self) -> list[int]:
        """Sondea los índices 0..9 y devuelve los índices disponibles.

        Libera temporalmente la cámara activa para poder sondear los demás
        índices (en Windows/DirectShow no se puede abrir la misma cámara dos
        veces). Al terminar reconecta la cámara activa.
        """
        with self._cam_lock:
            self.cambiando_camara = True
            try:
                self.controller.pipeline.visor.desconectar()
                caps = VisorCamara.abrir_disponibles(max_indice=9)
                indices = sorted(caps)
                for cap in caps.values():
                    cap.release()
            finally:
                ok = self.controller.pipeline.conectar_camara()
                self.camara_ok = ok
                self.cambiando_camara = False
        logger.info("Cámaras disponibles: %s", indices or "ninguna")
        return indices

    def cambiar_camara(self, indice: int) -> tuple[bool, str]:
        """Conecta en caliente la cámara del índice dado y persiste la elección."""
        indice = int(indice)
        with self._cam_lock:
            self.cambiando_camara = True
            try:
                actual = self.controller.pipeline.visor.cfg.indice_camara
                if indice == actual and self.camara_ok:
                    return True, f"La cámara {indice} ya está activa."

                self.controller.pipeline.visor.desconectar()
                self.controller.pipeline.visor.cfg.indice_camara = indice
                ok = self.controller.pipeline.conectar_camara()
                self.camara_ok = ok

                if not ok:
                    # Revertir al índice anterior para no dejar la celda sin cámara
                    self.controller.pipeline.visor.cfg.indice_camara = actual
                    ok_prev = self.controller.pipeline.conectar_camara()
                    self.camara_ok = ok_prev
                    return False, f"No se pudo abrir la cámara {indice}."

                self._persistir_indice_camara(indice)
                with self._lock:
                    self.latest_jpeg = None  # fuerza frame fresco en el stream
                logger.info("Cámara cambiada al índice %d.", indice)
                return True, f"Cámara cambiada al índice {indice}."
            finally:
                self.cambiando_camara = False

    def _persistir_indice_camara(self, indice: int) -> None:
        """Guarda el índice elegido en config.json sin tocar el resto de claves."""
        try:
            ruta = Path(self.config_path)
            data = json.loads(ruta.read_text(encoding="utf-8"))
            data.setdefault("camara", {})["indice_camara"] = int(indice)
            ruta.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("Cámara %d guardada como predeterminada en %s", indice, ruta)
        except Exception:
            logger.exception("No se pudo persistir la cámara en %s", self.config_path)

    def indice_camara_actual(self) -> int:
        try:
            return int(self.controller.pipeline.visor.cfg.indice_camara)
        except Exception:
            return -1

    # ------------------------------------------------------------- editor ROI
    def ajustar_roi(self, accion: str) -> tuple[bool, str]:
        """Edita la ROI con el editor visual (mover, tamaño, mostrar, aplicar)."""
        accion = (accion or "").strip().lower()
        if accion == "aplicar":
            return True, self.editor_roi.aplicar(self.config_path)
        return self.editor_roi.mover(accion)

    # ------------------------------------------------------------- editor HSV
    def ajustar_hsv(self, accion: str, valor: str = "") -> tuple[bool, str]:
        """Edita los rangos HSV con el editor visual."""
        accion = (accion or "").strip().lower()
        if accion == "aplicar":
            return True, self.editor_hsv.aplicar(self.config_path)
        return self.editor_hsv.mover(accion, valor)

    def iniciar(self):
        self.conectar()
        self.estado = "LISTO" if not self.parada_activa else "PARADA_SEGURIDAD"
        self._running = True
        self._hilo_captura = threading.Thread(
            target=self._loop_captura, name="WebCapture", daemon=True
        )
        self._hilo_captura.start()

    def detener(self):
        self._running = False
        if self._hilo_captura and self._hilo_captura.is_alive():
            self._hilo_captura.join(timeout=2.0)
        try:
            self.controller.desconectar()
        except Exception:
            pass

    # ------------------------------------------------------- bucle captura
    def _loop_captura(self):
        ultimo_error: str | None = None
        while self._running:
            try:
                self._tick()
                ultimo_error = None
            except Exception as e:
                # Deduplica fallos consecutivos idénticos para no inundar el log
                msg = f"{type(e).__name__}: {e}"
                if msg != ultimo_error:
                    logger.error("Error en bucle de captura: %s", msg)
                    ultimo_error = msg
                else:
                    logger.debug("Error en bucle de captura (repetido): %s", msg)
                time.sleep(0.2)

    def _tick(self):
        # 1. Eventos asíncronos de seguridad del gripper (E-stop / proximidad)
        try:
            evento = self.controller.gripper.get_evento()
        except Exception:
            evento = None
        if evento:
            self._procesar_evento(evento)

        # 2. Captura + visión
        frame = None
        try:
            frame = self.controller.pipeline.visor.leer()
        except Exception:
            frame = None

        if frame is None:
            frame = self._frame_placeholder("SIN CAMARA")

        try:
            det = self.controller.pipeline.procesar_frame(frame)
        except Exception as e:
            det = {"ok": False, "motivo_fallo": f"error vision: {e}"}

        try:
            frame_dib = self.controller.pipeline.dibujar(frame, det)
        except Exception:
            frame_dib = frame

        # Rectángulo amarillo de la ROI (si está activada la visualización)
        frame_dib = self.editor_roi.dibujar(frame_dib)
        # Máscara HSV en vivo (pinta los píxeles capturados por cada color)
        frame_dib = self.editor_hsv.dibujar(frame_dib)
        # Puntos del asistente de homografía (solo en modo calibración)
        frame_dib = self.editor_homografia.dibujar(frame_dib)

        frame_dib = self._dibujar_hud(frame_dib, det)

        # Tamaño real del frame para los editores que convierten clics
        # (la cámara puede no honrar el ancho/alto pedidos)
        self.editor_homografia.tamano_frame = (frame_dib.shape[1],
                                               frame_dib.shape[0])

        ok, buf = cv2.imencode(".jpg", frame_dib, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._lock:
                self.latest_jpeg = buf.tobytes()
                self.deteccion = det

        # 3. Mantener estado coherente
        with self._lock:
            if self.parada_activa:
                # conserva el estado de emergencia fijado por el evento
                pass
            elif self.estado in ("INICIANDO", "LISTO", "ESPERANDO_PIEZA"):
                # Solo se reevalúa en reposo: no se pueden sobrescribir estados
                # de un ciclo en marcha (DETECTANDO, ROBOT_ACTUANDO, etc.).
                self.estado = "LISTO" if det.get("ok") else "ESPERANDO_PIEZA"

        time.sleep(0.03)

    def _frame_placeholder(self, texto: str) -> np.ndarray:
        frame = np.full((480, 640, 3), 35, dtype=np.uint8)
        cv2.putText(frame, texto, (150, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
        return frame

    def _dibujar_hud(self, frame: np.ndarray, det: dict) -> np.ndarray:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w, 34), (40, 40, 40), -1)
        color = (0, 0, 255) if self.parada_activa else (0, 255, 0)

        # Texto izquierdo (estado)
        txt_izq = f"{self.estado}"
        cv2.putText(frame, txt_izq, (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        (w_izq, _), _ = cv2.getTextSize(txt_izq, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)

        # Texto derecho: alineado al borde derecho y acotado al espacio libre
        if det.get("ok"):
            txt = f"{det['categoria']}  ({det['x_ur5'] * 1000:.1f}, {det['y_ur5'] * 1000:.1f}) mm"
        else:
            txt = f"buscando: {det.get('motivo_fallo', '')}"

        fuente, escala, grosor = cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
        margen = 12
        max_w = w - margen * 2 - w_izq - 24  # deja hueco tras el estado
        (tw, th), _ = cv2.getTextSize(txt, fuente, escala, grosor)
        if tw > max_w:
            while len(txt) > 3:
                (tw, _), _ = cv2.getTextSize(txt + "...", fuente, escala, grosor)
                if tw <= max_w:
                    break
                txt = txt[:-1]
            txt = txt + "..."
            (tw, th), _ = cv2.getTextSize(txt, fuente, escala, grosor)
        cv2.putText(frame, txt, (w - margen - tw, 24),
                    fuente, escala, (0, 255, 255), grosor)
        return frame

    def _procesar_evento(self, evento: str):
        """Atiende un evento asíncrono de seguridad del firmware del gripper.

        Con el hardware actual (4 servos, sin E-STOP ni sensor de proximidad)
        el unico evento posible es el watchdog.
        """
        logger.warning("Evento de seguridad recibido: %s", evento)
        self.ultimo_evento = evento
        if evento == "apertura_watchdog":
            # El firmware se abrio solo: la PC dejo de mandar comandos
            # validos por mas de 15 s. La pieza ya no esta sujeta, asi que
            # el ciclo debe abortarse y el robot frenarse.
            self.abortar_ciclo = True
            self.parada_activa = True
            self.estado = "APERTURA_WATCHDOG"
            self.controller.detener()
            logger.error(
                "El gripper se abrió solo por watchdog: la PC dejó de "
                "comunicarse. La pieza se soltó; el robot fue frenado."
            )
        elif evento == "apertura_watchdog_completada":
            logger.warning("Terminó la apertura por watchdog del gripper")

    # ------------------------------------------------------------ comandos
    def comando(self, accion: str, categoria: str | None = None) -> tuple[bool, str]:
        self.ultimo_comando = accion
        logger.info("Comando remoto: %s (cat=%s)", accion, categoria)

        if accion in ("abrir", "cerrar", "home", "ciclo", "detener", "reset", "soltar"):
            # El reset es la única acción permitida con la parada activa.
            if self.parada_activa and accion != "reset":
                self.registrar_log("WARN", f"Comando '{accion}' rechazado: parada de seguridad activa.")
                return False, "Parada de seguridad activa: reinicie la parada antes de operar."

        if accion == "limpiar_rastro":
            self.controller.limpiar_rastro()
            self.registrar_log("INFO", "Rastro del robot borrado.")
            return True, "Rastro del robot borrado."

        if accion == "abrir":
            self.registrar_log("INFO", "Comando manual: abrir gripper")
            self.controller.gripper.open()
            return True, "Gripper abierto."
        if accion == "cerrar":
            self.registrar_log("INFO", "Comando manual: cerrar gripper")
            self.controller.gripper.close()
            return True, "Gripper cerrado."
        if accion == "soltar":
            self.registrar_log("WARN", "Comando: soltar pieza (apertura suave)")
            ok = self.controller.gripper.stop()
            return (True, "Pieza soltada (gripper abierto).") if ok else (
                False, "No se pudo soltar la pieza."
            )
        if accion == "home":
            self.registrar_log("INFO", "Comando manual: mover a Home")
            if self.controller.ir_a_home():
                self.registrar_log("OK", "Robot en posición Home.")
                return True, "Movido a posición Home."
            motivo = self.controller.ultimo_error or "el robot rechazó el movimiento"
            self.registrar_log("ERROR", f"No se pudo mover el robot a Home: {motivo}")
            return False, f"No se pudo mover a Home: {motivo}"
        if accion == "detener":
            self.registrar_log("ERROR", "Comando: PARADA DE SEGURIDAD solicitada")
            self.abortar_ciclo = True
            self.parada_activa = True
            self.controller.detener()
            self.estado = "PARADA_SEGURIDAD"
            return True, "Parada solicitada."
        if accion == "reset":
            self.registrar_log("OK", "Parada de seguridad reiniciada")
            self.parada_activa = False
            self.abortar_ciclo = False
            self.ultimo_evento = None
            self.estado = "LISTO"
            return True, "Parada reiniciada."
        if accion == "ciclo":
            if self.estado in ("ROBOT_ACTUANDO", "DETECTANDO", "VERIFICANDO"):
                self.registrar_log("WARN", "Ya hay un ciclo en curso.")
                return False, "Ya hay un ciclo en curso."
            # Fijar el estado antes de lanzar el hilo, para que el _tick no lo
            # sobreescriba con LISTO/ESPERANDO_PIEZA durante la detección.
            self.estado = "DETECTANDO"
            threading.Thread(
                target=self.ejecutar_ciclo, args=(categoria,), daemon=True
            ).start()
            return True, "Ciclo iniciado."

        return False, f"Acción desconocida: {accion}"

    def ejecutar_ciclo(self, categoria_filtro: str | None = None) -> bool:
        if self.parada_activa:
            return False

        self.abortar_ciclo = False
        self.estado = "DETECTANDO"
        det = self.controller.pipeline.procesar_frame()

        if not det.get("ok"):
            self.estado = "ESPERANDO_PIEZA"
            self.registrar_log("WARN", f"Ciclo cancelado: {det.get('motivo_fallo', 'sin pieza detectada')}")
            return False

        categoria = det["categoria"]
        if categoria_filtro and categoria != categoria_filtro:
            self.estado = "ESPERANDO_PIEZA"
            self.registrar_log("WARN", f"Pieza detectada es {categoria}, pero se filtró por {categoria_filtro}")
            return False

        self.estado = "ROBOT_ACTUANDO"
        x, y = det["x_ur5"], det["y_ur5"]
        # Centro real de agarre = centro detectado + offset de herramienta
        # (pinzas vs TCP). Ver `calibrar_offset_pick_actual()`.
        x_pick, y_pick = self.controller.punto_pick(x, y)
        # Leer del config vivo del controlador: `self.cfg` es una foto tomada
        # al arrancar y no ve las cotas calibradas en caliente (antes el ciclo
        # seguía usando z_seguro=250 mm aunque se hubiera calibrado en 237 mm).
        z_seguro = float(self.controller.config.get("z_seguro", 0.200))
        z_pick = float(self.controller.config.get("z_pick", 0.050))
        z_deposit = self.controller.z_deposito()
        dest = self.controller.destino_para(categoria)

        self.registrar_log("INFO", f"Iniciando ciclo: Pieza {categoria} en ({x*1000:.1f}, {y*1000:.1f}) mm")
        if (x_pick, y_pick) != (x, y):
            self.registrar_log(
                "INFO",
                f"Offset de pick ({(x_pick-x)*1000:+.0f}, {(y_pick-y)*1000:+.0f}) mm: "
                f"TCP de agarre en ({x_pick*1000:.1f}, {y_pick*1000:.1f}) mm",
            )

        try:
            # 1. Aproximación segura sobre la pieza (Handshake RTDE)
            self.registrar_log("INFO", f"Aproximación sobre pieza (Z={z_seguro*1000:.0f} mm)...")
            self._mover(x_pick, y_pick, z_seguro, "aproximación fallida")
            if self._abortado():
                raise RuntimeError("abortado por seguridad")

            # 2. Descenso para recogida (Handshake RTDE)
            self.registrar_log("INFO", f"Descendiendo a cota de pick (Z={z_pick*1000:.0f} mm)...")
            self._mover(x_pick, y_pick, z_pick, "descenso a pick fallido")

            # 3. Cierre de pinza con validación de confirmación del firmware
            self.registrar_log("INFO", "Cerrando gripper (esperando confirmación DONE de Arduino)...")
            if not self.controller.gripper.close():
                raise RuntimeError("cierre de gripper fallido (sin confirmación DONE)")
            self.registrar_log("INFO", "Gripper cerrado OK")

            # 4. Elevación inicial
            self.registrar_log("INFO", "Elevando a cota segura...")
            self._mover(x_pick, y_pick, z_seguro, "elevación post-pick fallida")
            if self._abortado():
                raise RuntimeError("abortado por seguridad")

            # 5. Confirmación visual de agarre (Closed-Loop Vision)
            dx = dest["x"] - x_pick
            dy = dest["y"] - y_pick
            dist_dest = math.hypot(dx, dy)
            if dist_dest > 0.08:
                x_check = x_pick + (dx / dist_dest) * 0.08
                y_check = y_pick + (dy / dist_dest) * 0.08
            else:
                x_check, y_check = dest["x"], dest["y"]

            if not self.controller.mover_a(x_check, y_check, z_seguro):
                raise RuntimeError(
                    "desplazamiento a verificación fallido: "
                    f"{self.controller.ultimo_error or 'el robot rechazó el movimiento'}"
                )
            if self._abortado():
                raise RuntimeError("abortado por seguridad")

            with self._lock:
                self.estado = "VERIFICANDO"
            self.registrar_log("INFO", "Inspeccionando mesa con cámara cenital (Closed-Loop)...")
            time.sleep(0.15)  # estabilización visual del frame

            es_mock = (self.controller.robot.mode == "mock")
            det_verif = self.controller.pipeline.procesar_frame()
            if det_verif.get("ok"):
                # Si se detecta una pieza cerca de la coordenada original de pick, no fue levantada
                dist_pick = math.hypot(det_verif["x_ur5"] - x, det_verif["y_ur5"] - y)
                if dist_pick < 0.050:
                    if not es_mock:
                        self.registrar_log(
                            "ERROR",
                            f"Fallo de agarre: la pieza sigue en mesa en ({det_verif['x_ur5']:.3f}, {det_verif['y_ur5']:.3f})"
                        )
                        self.controller.gripper.open()
                        self.controller.mover_a(*self.controller.punto_base(0.30, 0.0), z_seguro)
                        raise RuntimeError("Fallo de agarre: la pieza no fue recogida (verificado por visión)")
                    else:
                        self.registrar_log(
                            "WARN",
                            f"Verificación visual: pieza aún en mesa en ({det_verif['x_ur5']*1000:.0f}, {det_verif['y_ur5']*1000:.0f}) mm. "
                            "[MODO MOCK: robot virtual, continuando ciclo]"
                        )
                else:
                    self.registrar_log("OK", "Confirmación visual OK: pieza levantada exitosamente.")
            else:
                self.registrar_log("OK", "Confirmación visual OK: mesa despejada en punto de pick.")

            with self._lock:
                self.estado = "ROBOT_ACTUANDO"

            # 6. Transporte a la zona de clasificación
            self.registrar_log("INFO", f"Transportando a destino {categoria} ({dest['x']:.2f}, {dest['y']:.2f}) m...")
            self._mover(dest["x"], dest["y"], z_seguro, "transporte a destino fallido")
            self._mover(dest["x"], dest["y"], z_deposit, "descenso a destino fallido")

            # 7. Liberación y retirada segura
            self.registrar_log("INFO", "Abriendo gripper...")
            if not self.controller.gripper.open():
                logger.warning("Apertura de gripper no reportó confirmación normal.")

            if not self.controller.mover_a(dest["x"], dest["y"], z_seguro):
                raise RuntimeError(
                    "retirada post-depósito fallida: "
                    f"{self.controller.ultimo_error or 'el robot rechazó el movimiento'}"
                )

            with self._lock:
                self.ciclos_ok += 1
                self.por_categoria[categoria] = self.por_categoria.get(categoria, 0) + 1
                self.estado = "LISTO"
            self.registrar_log("OK", f"¡Ciclo completado con éxito! Categoría {categoria} clasificada.")
            logger.info("Ciclo OK: categoría %s", categoria)
            return True

        except Exception as e:
            self.registrar_log("ERROR", f"Ciclo interrumpido: {e}")
            logger.warning("Ciclo interrumpido: %s", e)
            with self._lock:
                self.ciclos_error += 1
                self.estado = "PARADA_SEGURIDAD" if self.parada_activa else "ERROR_CICLO"
            return False

    def _abortado(self) -> bool:
        return self.abortar_ciclo or self.parada_activa

    def _mover(self, x, y, z, contexto: str) -> None:
        """mover_a, pero con el motivo real del rechazo en el mensaje de error."""
        if self.controller.mover_a(x, y, z):
            return
        raise RuntimeError(
            f"{contexto}: "
            f"{self.controller.ultimo_error or 'el robot rechazó el movimiento'}"
        )

    # ------------------------------------------------------------- estado
    def estado_json(self) -> dict:
        with self._lock:
            det = dict(self.deteccion) if self.deteccion else {}
            return {
                "estado": self.estado,
                "parada_activa": self.parada_activa,
                "ultimo_evento": self.ultimo_evento,
                "ultimo_comando": self.ultimo_comando,
                "logs": list(self.logs_recientes),
                "camara_ok": self.camara_ok,
                "indice_camara": self.indice_camara_actual(),
                "gripper_ok": self.gripper_ok,
                "robot_ok": self.robot_ok,
                "deteccion": det,
                "gripper": self.controller.gripper.get_estado_completo(),
                "ciclos_ok": self.ciclos_ok,
                "ciclos_error": self.ciclos_error,
                "por_categoria": dict(self.por_categoria),
                "uptime_s": int(time.time() - self._t_inicio),
                "roi": self.editor_roi.valores(),
                "hsv": self.editor_hsv.estado(),
                "homografia": self.editor_homografia.estado(),
                # Sin la trayectoria completa: el mapa la pide a /api/robot
                "robot": self.controller.tracker.datos(incluir_trayectoria=False),
                "celda": self.celda_info,
                "frame": {
                    "ancho": self.editor_homografia.tamano_frame[0],
                    "alto": self.editor_homografia.tamano_frame[1],
                },
            }


# --------------------------------------------------------------- Flask app
dashboard = WebDashboard()
app = Flask(__name__, template_folder=str(BASE_DIR.parent / "templates"))


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/logo-upro.jpg")
def logo_upro():
    return send_from_directory(str(BASE_DIR.parent), "logo-upro.jpg")



def _generar_mjpeg():
    while True:
        with dashboard._lock:
            jpg = dashboard.latest_jpeg
        if jpg is None:
            time.sleep(0.1)
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")
        time.sleep(0.03)


@app.route("/video")
def video():
    return Response(_generar_mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/state")
def api_state():
    return jsonify(dashboard.estado_json())


@app.route("/api/robot")
def api_robot():
    """Telemetría liviana del TCP para el mapa de trabajo en vivo.

    Se consulta desde el navegador varias veces por segundo (SSE va a 0.5 s,
    demasiado lento para ver el rastro mientras el robot se mueve).
    """
    datos = dashboard.controller.datos_robot()
    det = dashboard.deteccion or {}
    return jsonify({
        "robot": datos,
        "estado": dashboard.estado,
        "parada": dashboard.parada_activa,
        "deteccion": {
            "ok": bool(det.get("ok")),
            "categoria": det.get("categoria"),
            "x_ur5": det.get("x_ur5"),
            "y_ur5": det.get("y_ur5"),
        },
        "celda": dashboard.celda_info,
    })


@app.route("/stream")
def stream():
    def eventos():
        dashboard.clientes_sse += 1
        try:
            while True:
                payload = json.dumps(dashboard.estado_json(), default=str)
                yield f"data: {payload}\n\n"
                time.sleep(0.5)
        finally:
            dashboard.clientes_sse -= 1
    return Response(eventos(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    categoria = data.get("categoria") or None
    ok, msg = dashboard.comando(accion, categoria)
    return jsonify({"ok": ok, "mensaje": msg}), (200 if ok else 400)


@app.route("/api/camaras")
def api_camaras():
    """Escanea los índices 0..9 y devuelve las cámaras disponibles.

    Ojo: el escaneo desconecta/reconecta la cámara activa, puede tardar
    varios segundos. El stream MJPEG se congela un instante y se recupera solo.
    """
    try:
        indices = dashboard.indices_camaras()
    except Exception as e:
        logger.exception("Error escaneando cámaras")
        return jsonify({"disponibles": [], "actual": dashboard.indice_camara_actual(),
                        "error": str(e)}), 500
    return jsonify({"disponibles": indices,
                    "actual": dashboard.indice_camara_actual()})


@app.route("/api/camara", methods=["POST"])
def api_camara():
    data = request.get_json(silent=True) or {}
    try:
        indice = int(data.get("indice"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Índice de cámara inválido."}), 400
    ok, msg = dashboard.cambiar_camara(indice)
    return jsonify({"ok": ok, "mensaje": msg,
                    "indice": dashboard.indice_camara_actual(),
                    "camara_ok": dashboard.camara_ok}), (200 if ok else 400)


@app.route("/api/roi", methods=["POST"])
def api_roi():
    """Ajuste visual de la ROI: mover, tamaño, mostrar/ocultar, aplicar y reset.

    Acciones válidas: izquierda, derecha, arriba, abajo, mas_grande,
    mas_pequena, mostrar, aplicar, reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    ok, msg = dashboard.ajustar_roi(accion)
    return jsonify({"ok": ok, "mensaje": msg,
                    "roi": dashboard.editor_roi.valores()}), (200 if ok else 400)


@app.route("/api/hsv", methods=["POST"])
def api_hsv():
    """Calibración visual de colores HSV.

    Acciones: color (con valor rojo/azul/verde/violeta), matiz_izquierda,
    matiz_derecha, mas_amplio, mas_estrecho, mas_sensible, menos_sensible,
    area_mas, area_menos, confianza_mas, confianza_menos, mostrar, aplicar,
    reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    valor = (data.get("valor") or "").strip().lower()
    ok, msg = dashboard.ajustar_hsv(accion, valor)
    return jsonify({"ok": ok, "mensaje": msg,
                    "hsv": dashboard.editor_hsv.estado()}), (200 if ok else 400)


@app.route("/api/homografia", methods=["POST"])
def api_homografia():
    """Asistente de calibración de la homografía.

    Acciones: iniciar, punto (con valor "x,y" en píxeles del frame completo,
    típicamente un clic sobre el video), deshacer, cancelar, aplicar, reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    valor = (data.get("valor") or "").strip()
    if accion == "aplicar":
        msg = dashboard.editor_homografia.aplicar(dashboard.config_path)
        ok = not msg.startswith("Error")
        return jsonify({"ok": ok, "mensaje": msg,
                        "homografia": dashboard.editor_homografia.estado()}), \
            (200 if ok else 400)
    ok, msg = dashboard.editor_homografia.mover(accion, valor)
    return jsonify({"ok": ok, "mensaje": msg,
                    "homografia": dashboard.editor_homografia.estado()}), \
        (200 if ok else 400)


@app.route("/api/calibrar_robot", methods=["POST"])
def api_calibrar_robot():
    """Calibra parámetros físicos del robot en vivo capturando su TCP actual.

    Acciones soportadas:
    - home: Captura la pose completa actual como nuevo Home
    - z_pick: Captura Z actual como cota de agarre
    - z_seguro: Captura Z actual como cota segura de tránsito
    - offset_pick: Corrige X,Y del agarre con el TCP actual sobre la pieza detectada
    - destino_a: Captura X,Y actual como destino de clasificación A
    - destino_b: Captura X,Y actual como destino de clasificación B
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()

    if accion == "home":
        ok, msg = dashboard.controller.calibrar_home_actual()
    elif accion == "z_pick":
        ok, msg = dashboard.controller.calibrar_z_pick_actual()
        if ok:
            dashboard.celda_info["z"]["pick"] = dashboard.controller.config["z_pick"]
    elif accion == "z_seguro":
        ok, msg = dashboard.controller.calibrar_z_seguro_actual()
        if ok:
            dashboard.celda_info["z"]["seguro"] = dashboard.controller.config["z_seguro"]
    elif accion == "offset_pick":
        ok, msg = dashboard.controller.calibrar_offset_pick_actual()
    elif accion == "destino_a":
        ok, msg = dashboard.controller.calibrar_destino_actual("A")
    elif accion == "destino_b":
        ok, msg = dashboard.controller.calibrar_destino_actual("B")
    else:
        return jsonify({"ok": False, "mensaje": f"Acción desconocida: '{accion}'"}), 400

    nivel = "INFO" if ok else "WARN"
    dashboard.registrar_log(nivel, f"Calibración robot: {msg}")
    return jsonify({
        "ok": ok,
        "mensaje": msg,
        "config": {
            "home_pose": dashboard.controller.home_pose,
            "z_pick": dashboard.controller.config.get("z_pick"),
            "z_seguro": dashboard.controller.config.get("z_seguro"),
            "pick_offset_x": dashboard.controller.config.get("pick_offset_x"),
            "pick_offset_y": dashboard.controller.config.get("pick_offset_y"),
            "destinos": dashboard.controller.config.get("destinos"),
        }
    }), (200 if ok else 400)



if __name__ == "__main__":
    configurar_logging()
    dashboard.iniciar()
    logger.info("Escuchando en todas las interfaces (0.0.0.0:5000).")
    logger.info("Abrir en este equipo:  http://localhost:5000")
    try:
        ip_local = socket.gethostbyname(socket.gethostname())
        if not ip_local.startswith("127."):
            logger.info("Abrir desde otros equipos de la red:  http://%s:5000", ip_local)
    except OSError:
        pass
    try:
        app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
    finally:
        dashboard.detener()