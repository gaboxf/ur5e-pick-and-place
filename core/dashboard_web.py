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
import os
import re
import secrets
import queue
import socket
import threading
import time
from functools import wraps
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, make_response, render_template, request, send_from_directory

# Ejecución directa (python core/dashboard_web.py): agregar la raíz del
# proyecto al path para poder importar el paquete "core".
import sys
BASE_DIR = Path(__file__).resolve().parent
RAIZ_PROYECTO = BASE_DIR.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import configurar_logging, get_logger

from core.robot_controller import RobotController
from vision.config import actualizar_config
from vision.homografia_editor import EditorHomografia
from vision.hsv_editor import EditorHSV
from vision.roi_editor import EditorROI
from vision.visor import VisorCamara

logger = get_logger("WebDashboard")

NOMBRES_COLOR = {"A": "roja", "B": "azul"}
NOMBRES_CATEGORIA = {"A": "A (roja)", "B": "B (azul)"}


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
        self._operacion = threading.Lock()
        self._parada_lock = threading.Lock()
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
        self.por_categoria = {"A": 0, "B": 0}
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
            self.controller.gripper.connect()
            self.gripper_ok = self.controller.gripper.conexion_disponible()
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
            f"Gripper: {'OK (Arduino conectado)' if self.gripper_ok else 'SIN CONEXIÓN FÍSICA'}"
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
            actualizar_config(ruta, {"camara": {"indice_camara": int(indice)}})
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
            msg = self.editor_roi.aplicar(self.config_path)
            return not msg.startswith("Error"), msg
        return self.editor_roi.mover(accion)

    # ------------------------------------------------------------- editor HSV
    def ajustar_hsv(self, accion: str, valor: str = "") -> tuple[bool, str]:
        """Edita los rangos HSV con el editor visual."""
        accion = (accion or "").strip().lower()
        if accion == "aplicar":
            msg = self.editor_hsv.aplicar(self.config_path)
            return not msg.startswith("Error"), msg
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

        self.camara_ok = frame is not None
        if frame is None:
            frame = self._frame_placeholder("SIN CAMARA")
            det = {"ok": False, "estado_vision": "sin_frame", "motivo_fallo": "Sin imagen de cámara."}
        else:
            self.editor_homografia.tamano_frame = (frame.shape[1], frame.shape[0])
            try:
                det = self.controller.pipeline.procesar_frame(frame)
            except Exception as e:
                det = {"ok": False, "estado_vision": "error", "motivo_fallo": f"Error de visión: {e}"}

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
            elif self.estado in ("INICIANDO", "LISTO", "ESPERANDO_PIEZA",
                                 "CALIBRACION_PENDIENTE", "SIN_FRAME"):
                # Solo se reevalúa en reposo: no se pueden sobrescribir estados
                # de un ciclo en marcha (DETECTANDO, ROBOT_ACTUANDO, etc.).
                estado_vision = det.get("estado_vision")
                if estado_vision in ("calibracion_pendiente", "sin_frame"):
                    self.estado = estado_vision.upper()
                else:
                    self.estado = "LISTO" if det.get("ok") else "ESPERANDO_PIEZA"

        time.sleep(0.03)

    def _frame_placeholder(self, texto: str) -> np.ndarray:
        frame = np.full((480, 640, 3), 35, dtype=np.uint8)
        cv2.putText(frame, texto, (150, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
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
            parada_enviada = self._activar_parada("APERTURA_WATCHDOG")
            logger.error(
                "El gripper se abrió solo por watchdog: la PC dejó de "
                "comunicarse. La pieza se soltó. Parada del UR5: %s.",
                "enviada" if parada_enviada else "sin confirmación"
            )
        elif evento == "apertura_watchdog_completada":
            logger.warning("Terminó la apertura por watchdog del gripper")

    # ------------------------------------------------------------ comandos
    def disponibilidad_mandos(self) -> dict[str, str]:
        """Devuelve el motivo de bloqueo por mando, o cadena vacía si está disponible."""
        robot = self.controller.robot
        gripper = self.controller.gripper
        self.robot_ok = robot.conexion_disponible()
        self.gripper_ok = gripper.conexion_disponible()
        motivo_robot = "" if self.robot_ok else "Revisá la conexión con el UR5."
        admite_simulado = robot.mode != "real" and gripper.simulado
        motivo_gripper = "" if self.gripper_ok or admite_simulado else "Revisá la conexión con el gripper."
        motivo_vision = "" if self.camara_ok else "Revisá la conexión con la cámara."
        if self.editor_homografia.calibrando:
            motivo_vision = "Terminá o cancelá la calibración antes de ejecutar."
        elif self.deteccion.get("estado_vision") == "calibracion_pendiente":
            motivo_vision = "Verificá la homografía para la cámara y la ROI actuales."
        motivo_calibracion = motivo_robot
        if robot.mode != "real":
            motivo_calibracion = "La calibración física requiere el UR5 conectado en modo real."
        return {
            "home": motivo_robot,
            "gripper": motivo_gripper,
            "ciclo": " ".join(m for m in (motivo_robot, motivo_gripper, motivo_vision) if m),
            "calibrar_robot": motivo_calibracion,
        }

    def _activar_parada(self, estado: str) -> bool:
        with self._parada_lock:
            with self._lock:
                self.abortar_ciclo = True
                self.parada_activa = True
                self.estado = estado
            return self.controller.detener(retener=True)

    def comando(self, accion: str, categoria: str | None = None) -> tuple[bool, str]:
        self.ultimo_comando = accion
        logger.info("Comando remoto: %s (cat=%s)", accion, categoria)

        if accion == "detener":
            self.registrar_log("ERROR", "Parada solicitada desde el dashboard.")
            if self._activar_parada("PARADA_SEGURIDAD"):
                return True, "Parada solicitada."
            return False, "Parada local activa. No se pudo enviar al UR5. Revisá la conexión RTDE."

        if accion == "limpiar_rastro":
            self.controller.limpiar_rastro()
            return True, "Rastro del robot borrado."

        if accion not in ("abrir", "cerrar", "home", "ciclo", "reset", "soltar"):
            return False, f"Acción desconocida: {accion}"

        if not self._operacion.acquire(blocking=False):
            return False, "Esperá a que termine la operación en curso."

        hilo_iniciado = False
        try:
            if accion == "reset":
                if not self._parada_lock.acquire(blocking=False):
                    return False, "Esperá a que termine la parada."
                try:
                    with self._lock:
                        self.controller.robot.reiniciar_parada()
                        self.parada_activa = False
                        self.abortar_ciclo = False
                        self.ultimo_evento = None
                        self.estado = "LISTO"
                finally:
                    self._parada_lock.release()
                self.registrar_log("OK", "Parada reiniciada.")
                return True, "Parada reiniciada."

            if self._abortado():
                return False, "Parada activa. Reiniciá la parada antes de operar."

            tipo = accion if accion in ("home", "ciclo") else "gripper"
            motivo = self.disponibilidad_mandos()[tipo]
            if motivo:
                return False, motivo

            if accion == "ciclo":
                if categoria not in (None, "A", "B"):
                    return False, "Solo están habilitadas las categorías A y B."
                with self._lock:
                    if self._abortado():
                        return False, "Parada activa."
                    self.estado = "DETECTANDO"
                hilo = threading.Thread(
                    target=self._ciclo_en_segundo_plano, args=(categoria,), daemon=True
                )
                try:
                    hilo.start()
                except RuntimeError:
                    with self._lock:
                        if not self._abortado():
                            self.estado = "ERROR_CICLO"
                    logger.exception("No se pudo iniciar el hilo del ciclo")
                    return False, "No se pudo iniciar el ciclo."
                hilo_iniciado = True
                return True, "Ciclo iniciado."

            if accion == "home":
                ok = self.controller.ir_a_home()
                motivo = self.controller.ultimo_error or "movimiento rechazado"
                if not ok:
                    return False, f"No se pudo ir a Home: {motivo}"
                return True, ("Robot simulado en Home." if self.controller.robot.mode != "real"
                              else "Robot en Home.")

            metodo, mensaje = {
                "abrir": (self.controller.gripper.open, "Gripper abierto."),
                "cerrar": (self.controller.gripper.close, "Gripper cerrado."),
                "soltar": (self.controller.gripper.stop, "Apertura del gripper confirmada."),
            }[accion]
            if self._abortado():
                return False, "Parada activa."
            if not metodo():
                return False, "El gripper no confirmó el comando. Revisá su conexión."
            if self.controller.gripper.simulado:
                return True, "Comando simulado. " + mensaje
            return True, mensaje
        finally:
            if not hilo_iniciado:
                self._operacion.release()

    def _ciclo_en_segundo_plano(self, categoria: str | None) -> None:
        try:
            self._ejecutar_ciclo(categoria)
        except Exception:
            logger.exception("Error inesperado durante el ciclo")
            self._registrar_fallo_ciclo("Error inesperado durante el ciclo. Consultá el registro.")
        finally:
            self._operacion.release()

    def ejecutar_ciclo(self, categoria_filtro: str | None = None) -> bool:
        """Ejecuta un ciclo síncrono con la misma exclusión que el dashboard."""
        if not self._operacion.acquire(blocking=False):
            return False
        try:
            return self._ejecutar_ciclo(categoria_filtro)
        finally:
            self._operacion.release()

    def _ejecutar_ciclo(self, categoria_filtro: str | None = None) -> bool:
        if self._abortado():
            return False
        motivo = self.disponibilidad_mandos()["ciclo"]
        if motivo:
            self._registrar_fallo_ciclo(motivo)
            return False

        with self._lock:
            if self._abortado():
                return False
            self.estado = "DETECTANDO"
        det = self.controller.pipeline.procesar_frame()

        if not det.get("ok"):
            with self._lock:
                if not self._abortado():
                    estado_vision = det.get("estado_vision")
                    self.estado = (estado_vision.upper() if estado_vision in
                                   ("calibracion_pendiente", "sin_frame") else "ESPERANDO_PIEZA")
            self.registrar_log("WARN", f"Ciclo cancelado: {det.get('motivo_fallo', 'sin pieza detectada')}")
            return False

        categoria = det["categoria"]
        if categoria_filtro and categoria != categoria_filtro:
            with self._lock:
                if not self._abortado():
                    self.estado = "ESPERANDO_PIEZA"
            self.registrar_log("WARN", f"Pieza detectada es {categoria}, pero se filtró por {categoria_filtro}")
            return False

        with self._lock:
            if self._abortado():
                return False
            self.estado = "ROBOT_ACTUANDO"
        x, y = det["x_ur5"], det["y_ur5"]
        # Centro real de agarre = centro detectado + offset de herramienta
        # (pinzas vs TCP). Ver `capturar_pieza_para_offset()` y
        # `confirmar_offset_pick()` (calibración en dos pasos).
        x_pick, y_pick = self.controller.punto_pick(x, y)
        # Leer del config vivo del controlador: `self.cfg` es una foto tomada
        # al arrancar y no ve las cotas calibradas en caliente (antes el ciclo
        # seguía usando z_seguro=250 mm aunque se hubiera calibrado en 237 mm).
        z_seguro = float(self.controller.config.get("z_seguro", 0.200))
        z_pick = float(self.controller.config.get("z_pick", 0.050))
        z_deposit = self.controller.z_deposito()
        dest = self.controller.destino_para(categoria)
        if dest is None:
            self._registrar_fallo_ciclo(f"La categoría {categoria} no tiene un destino válido.")
            return False

        self.registrar_log("INFO", f"Iniciando ciclo: Pieza {categoria} en ({x*1000:.1f}, {y*1000:.1f}) mm")
        if (x_pick, y_pick) != (x, y):
            self.registrar_log(
                "INFO",
                f"Offset de pick ({(x_pick-x)*1000:+.0f}, {(y_pick-y)*1000:+.0f}) mm: "
                f"TCP de agarre en ({x_pick*1000:.1f}, {y_pick*1000:.1f}) mm",
            )

        try:
            if not self.controller.offset_pick_valido():
                ox, oy = self.controller.offset_pick()
                raise RuntimeError(
                    f"Offset de pick inválido en config.json: "
                    f"({ox * 1000:.0f}, {oy * 1000:.0f}) mm (máximo "
                    f"{self.controller.pick_offset_max_m * 1000:.0f} mm). "
                    "Ciclo abortado por seguridad."
                )

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
            self._comprobar_parada()
            if self.disponibilidad_mandos()["gripper"]:
                raise RuntimeError("Se perdió la conexión con el gripper antes del agarre.")
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

            self._mover(x_check, y_check, z_seguro, "desplazamiento a verificación fallido")
            with self._lock:
                self._comprobar_parada()
                self.estado = "VERIFICANDO"
            self.registrar_log("INFO", "Inspeccionando mesa con cámara cenital (Closed-Loop)...")
            time.sleep(0.15)  # estabilización visual del frame

            es_mock = (self.controller.robot.mode == "mock")
            det_verif = self.controller.pipeline.procesar_frame()
            self._comprobar_parada()
            if not det_verif.get("ok") and det_verif.get("estado_vision") != "sin_pieza":
                raise RuntimeError(
                    "Verificación de agarre inconclusa: "
                    f"{det_verif.get('motivo_fallo', 'imagen o localización inválida')}. "
                    "Revisá la pieza y el gripper antes de continuar."
                )
            if det_verif.get("ok"):
                # Si se detecta una pieza cerca de la coordenada original de pick, no fue levantada
                dist_pick = math.hypot(det_verif["x_ur5"] - x, det_verif["y_ur5"] - y)
                if dist_pick < 0.050:
                    if not es_mock:
                        self.registrar_log(
                            "ERROR",
                            f"Fallo de agarre: la pieza sigue en mesa en ({det_verif['x_ur5']:.3f}, {det_verif['y_ur5']:.3f})"
                        )
                        self._comprobar_parada()
                        self.controller.gripper.open()
                        self._mover(
                            *self.controller.punto_base(0.30, 0.0), z_seguro,
                            "retirada tras el fallo de agarre",
                        )
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
                self._comprobar_parada()
                self.estado = "ROBOT_ACTUANDO"

            # 6. Transporte a la zona de clasificación
            self.registrar_log("INFO", f"Transportando a destino {categoria} ({dest['x']:.2f}, {dest['y']:.2f}) m...")
            self._mover(dest["x"], dest["y"], z_seguro, "transporte a destino fallido")
            self._mover(dest["x"], dest["y"], z_deposit, "descenso a destino fallido")

            # 7. Liberación y retirada segura
            self.registrar_log("INFO", "Abriendo gripper...")
            self._comprobar_parada()
            apertura = not self.disponibilidad_mandos()["gripper"] and self.controller.gripper.open()

            self._mover(dest["x"], dest["y"], z_seguro, "retirada post-depósito fallida")

            if not apertura:
                raise RuntimeError("El gripper no confirmó la liberación. Revisá su conexión.")

            # 8. Regreso a Home (pose segura para el próximo ciclo)
            self._comprobar_parada()
            if not self.controller.ir_a_home():
                motivo = self.controller.ultimo_error or "movimiento rechazado"
                raise RuntimeError(f"Depósito realizado. No se pudo volver a Home: {motivo}")

            with self._lock:
                self._comprobar_parada()
                self.ciclos_ok += 1
                self.por_categoria[categoria] = self.por_categoria.get(categoria, 0) + 1
                self.estado = "LISTO"
            self.registrar_log("OK", f"¡Ciclo completado con éxito! Categoría {categoria} clasificada.")
            logger.info("Ciclo OK: categoría %s", categoria)
            return True

        except Exception as e:
            self._registrar_fallo_ciclo(str(e))
            return False

    def _registrar_fallo_ciclo(self, motivo: str) -> None:
        self.registrar_log("ERROR", f"Ciclo interrumpido: {motivo}")
        logger.warning("Ciclo interrumpido: %s", motivo)
        with self._lock:
            self.ciclos_error += 1
            if not self.parada_activa:
                self.estado = "ERROR_CICLO"

    def _abortado(self) -> bool:
        return self.abortar_ciclo or self.parada_activa

    def _comprobar_parada(self) -> None:
        if self._abortado():
            raise RuntimeError("operación interrumpida por parada")

    def _mover(self, x, y, z, contexto: str) -> None:
        """Ejecuta un movimiento, respetando la parada antes y después."""
        self._comprobar_parada()
        if not self.controller.mover_a(x, y, z):
            raise RuntimeError(
                f"{contexto}: "
                f"{self.controller.ultimo_error or 'el robot rechazó el movimiento'}"
            )
        self._comprobar_parada()

    # ------------------------------------------------------------- estado
    def estado_json(self) -> dict:
        mandos = self.disponibilidad_mandos()
        with self._lock:
            det = dict(self.deteccion) if self.deteccion else {}
            return {
                "estado": self.estado,
                "parada_activa": self.parada_activa,
                "operacion_en_curso": self._operacion.locked(),
                "parada_en_curso": self._parada_lock.locked(),
                "ultimo_evento": self.ultimo_evento,
                "ultimo_comando": self.ultimo_comando,
                "logs": list(self.logs_recientes),
                "camara_ok": self.camara_ok,
                "indice_camara": self.indice_camara_actual(),
                "gripper_ok": self.gripper_ok,
                "robot_ok": self.robot_ok,
                "gripper_simulado": self.controller.robot.mode != "real" and self.controller.gripper.simulado,
                "bloqueo_mandos": mandos,
                "robot_modo": self.controller.robot.mode,
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


class CuposControlWeb:
    """Reserva cupos por navegador y habilita los mandos en una pestaña."""

    def __init__(self, limite: int):
        self.limite = limite
        self._lock = threading.Lock()
        self._cupos: dict[str, tuple[str, float | None]] = {}

    def consultar(self, navegador: str, pestaña: str, accion: str = "", anterior: str = "") -> dict:
        with self._lock:
            ahora = time.monotonic()
            self._cupos = {
                id_navegador: cupo for id_navegador, cupo in self._cupos.items()
                if cupo[1] is None or cupo[1] > ahora
            }
            cupo = self._cupos.get(navegador)
            propio = cupo is not None and cupo[0] == pestaña
            if accion == "tomar" and (cupo is not None or len(self._cupos) < self.limite):
                self._cupos[navegador] = (pestaña, None)
                propio = True
            elif accion == "salir" and propio:
                # Conserva el cupo unos segundos si la página se está recargando.
                self._cupos[navegador] = (pestaña, ahora + 5)
                propio = False
            elif accion == "renunciar" and propio:
                del self._cupos[navegador]
                propio = False
            elif accion == "reanudar" and cupo is not None and cupo[0] in (pestaña, anterior):
                self._cupos[navegador] = (pestaña, None)
                propio = True
            elif propio and cupo[1] is not None:
                propio = False
            return {
                "controla": propio,
                "puede_tomar": navegador in self._cupos or len(self._cupos) < self.limite,
                "ocupados": len(self._cupos),
                "limite": self.limite,
            }


def _identificador_valido(valor: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{32}", valor) is not None


# --------------------------------------------------------------- Flask app
dashboard = WebDashboard()
max_controladores = int(os.getenv("WEB_CONTROLADORES_MAX", "1"))
if max_controladores < 1:
    raise ValueError("WEB_CONTROLADORES_MAX debe ser un entero mayor o igual a 1.")
cupos_control = CuposControlWeb(max_controladores)
app = Flask(__name__, template_folder=str(BASE_DIR.parent / "templates"))

ENDPOINTS_CONTROL = {
    "api_command", "api_camaras", "api_camara", "api_roi",
    "api_hsv", "api_homografia", "api_calibrar_robot",
}


@app.before_request
def verificar_control_web():
    if request.endpoint not in ENDPOINTS_CONTROL:
        return None
    navegador = request.cookies.get("ur5_navegador", "")
    pestaña = request.headers.get("X-UR5-Pestana", "")
    if (_identificador_valido(navegador) and _identificador_valido(pestaña)
            and cupos_control.consultar(navegador, pestaña, "verificar")["controla"]):
        return None
    return jsonify({"ok": False, "mensaje": "Tomá el control web antes de enviar comandos."}), 403


@app.route("/")
def index():
    respuesta = make_response(render_template("dashboard.html"))
    if not _identificador_valido(request.cookies.get("ur5_navegador", "")):
        respuesta.set_cookie(
            "ur5_navegador", secrets.token_hex(16), httponly=True,
            samesite="Strict", secure=request.is_secure, max_age=30 * 24 * 60 * 60,
        )
    return respuesta


@app.route("/api/control", methods=["GET", "POST"])
def api_control():
    navegador = request.cookies.get("ur5_navegador", "")
    pestaña = request.headers.get("X-UR5-Pestana", "") or request.form.get("pestaña", "")
    accion = request.form.get("accion", "") if request.method == "POST" else ""
    if not _identificador_valido(navegador) or not _identificador_valido(pestaña):
        return jsonify({"ok": False, "mensaje": "Abrí el dashboard para obtener una sesión."}), 403
    if accion not in ("", "tomar", "salir", "renunciar", "reanudar"):
        return jsonify({"ok": False, "mensaje": "Acción de control inválida."}), 400
    anterior = request.form.get("anterior", "")
    estado = cupos_control.consultar(navegador, pestaña, accion, anterior)
    return jsonify(estado), (409 if accion == "tomar" and not estado["controla"] else 200)


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


def requiere_celda_disponible(funcion):
    """Excluye cambios de cámara y calibración durante movimientos web."""
    @wraps(funcion)
    def atender(*args, **kwargs):
        if not dashboard._operacion.acquire(blocking=False):
            return jsonify({"ok": False, "mensaje": "Esperá a que termine la operación en curso."}), 409
        try:
            if dashboard.parada_activa:
                return jsonify({"ok": False, "mensaje": "Reiniciá la parada antes de ajustar la celda."}), 409
            return funcion(*args, **kwargs)
        finally:
            dashboard._operacion.release()

    return atender


@app.route("/api/camaras")
@requiere_celda_disponible
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
@requiere_celda_disponible
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
@requiere_celda_disponible
def api_roi():
    """Ajuste visual de la ROI: mover, tamaño, mostrar/ocultar, aplicar y reset.

    Acciones válidas: izquierda, derecha, arriba, abajo, mas_grande,
    mas_pequena, mostrar, aplicar, reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    if accion == "establecer":
        ok, msg = dashboard.editor_roi.establecer(
            data.get("desde_x"), data.get("hasta_x"),
            data.get("desde_y"), data.get("hasta_y"),
        )
    else:
        ok, msg = dashboard.ajustar_roi(accion)
    return jsonify({"ok": ok, "mensaje": msg,
                    "roi": dashboard.editor_roi.valores()}), (200 if ok else 400)


@app.route("/api/hsv", methods=["POST"])
@requiere_celda_disponible
def api_hsv():
    """Calibración visual de colores HSV.

    Acciones: color (con valor rojo/azul), matiz_izquierda,
    matiz_derecha, mas_amplio, mas_estrecho, mas_sensible, menos_sensible,
    area_mas, area_menos, confianza_mas, confianza_menos, mostrar, aplicar,
    reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    valor = (data.get("valor") or "").strip().lower()
    if accion == "establecer":
        parametro = (data.get("parametro") or "").strip().lower()
        color = (data.get("color") or "").strip().lower() or None
        try:
            valor_num = float(valor)
        except (TypeError, ValueError):
            valor_num = None
        ok, msg = dashboard.editor_hsv.establecer(parametro, valor_num, color)
    else:
        ok, msg = dashboard.ajustar_hsv(accion, valor)
    return jsonify({"ok": ok, "mensaje": msg,
                    "hsv": dashboard.editor_hsv.estado()}), (200 if ok else 400)


@app.route("/api/homografia", methods=["POST"])
@requiere_celda_disponible
def api_homografia():
    """Asistente de calibración de la homografía.

    Acciones: iniciar, punto (con valor "x,y" en píxeles del frame completo,
    típicamente un clic sobre el video), deshacer, cancelar, aplicar, reset.
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()
    valor = (data.get("valor") or "").strip()
    if accion in ("iniciar", "punto", "aplicar") and not dashboard.camara_ok:
        return jsonify({"ok": False, "mensaje": "La calibración requiere imagen en vivo."}), 409
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
@requiere_celda_disponible
def api_calibrar_robot():
    """Calibra parámetros físicos del robot en vivo capturando su TCP actual.

    Acciones soportadas:
    - home: Captura la pose completa actual como nuevo Home
    - z_pick: Captura Z actual como cota de agarre
    - z_seguro: Captura Z actual como cota segura de tránsito
    - offset_pick_capturar: PASO 1, guarda la pieza detectada por la cámara (robot afuera)
    - offset_pick_confirmar: PASO 2, lee el TCP ya sobre la pieza y guarda el offset
    - destino_a: Captura X,Y actual como destino de clasificación A
    - destino_b: Captura X,Y actual como destino de clasificación B
    """
    data = request.get_json(silent=True) or {}
    accion = (data.get("accion") or "").strip().lower()

    motivo = dashboard.disponibilidad_mandos()["calibrar_robot"]
    if motivo:
        return jsonify({"ok": False, "mensaje": motivo}), 409

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
    elif accion == "offset_pick_capturar":
        ok, msg = dashboard.controller.capturar_pieza_para_offset()
    elif accion == "offset_pick_confirmar":
        ok, msg = dashboard.controller.confirmar_offset_pick()
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
            "offset_pick": dashboard.controller.estado_offset_pick(),
            "destinos": dashboard.controller.config.get("destinos"),
        }
    }), (200 if ok else 400)



def main() -> None:
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


if __name__ == "__main__":
    main()
