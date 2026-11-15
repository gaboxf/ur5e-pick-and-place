"""Dashboard de Control — Desafío 2: Pick and Place UR5.

Interfaz visual unificada (OpenCV) y control multimodal:
- Visualización de cámara y detección en tiempo real.
- Control por voz (SpeechRecognition + síntesis pyttsx3).
- Verificación en bucle cerrado post-depósito.
- Estado de la máquina de estados y comunicación con el gripper.
- Atajos de teclado y comandos de voz interactivos.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path

import cv2
import numpy as np

# Ejecución directa (python core/dashboard.py): agregar la raíz del proyecto
# al path para poder importar el paquete "core".
import sys
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import configurar_logging, get_logger
from core.robot_controller import RobotController
from vision.homografia_editor import EditorHomografia
from vision.hsv_editor import EditorHSV
from vision.roi_editor import EditorROI
from vision.visor import VisorCamara
from core.voice_control import VoiceController, ComandoVoz

logger = get_logger("Dashboard")

# Color (BGR) de la línea de estado HSV según el color editado
COLOR_TEXTO_HUD = {
    "rojo": (80, 80, 255),
    "azul": (255, 120, 60),
    "verde": (80, 220, 80),
    "violeta": (230, 130, 220),
}


class DashboardApp:
    def __init__(self, config_path: str = "vision/config.json", activar_voz: bool = True):
        _cfg = Path(config_path)
        self.config_path = _cfg if _cfg.is_absolute() else RAIZ_PROYECTO / _cfg
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        # Inicializar Robot Controller (Visión + Robot UR5 + Gripper en una sola fuente)
        self.robot_controller = RobotController(str(self.config_path))
        self.pipeline = self.robot_controller.pipeline
        self.gripper = self.robot_controller.gripper

        # Editor visual de la ROI (comparte el mismo ConfigCamara del pipeline)
        self.editor_roi = EditorROI(self.pipeline.cfg.camara)
        # Editor visual de los rangos HSV (comparte el ConfigSegmentacion)
        self.editor_hsv = EditorHSV(
            self.pipeline.cfg.segmentacion, self.pipeline.cfg.camara
        )
        # Asistente de calibración de la homografía (comparte ConfigHomografia
        # y el transformador, para recalcular la matriz al aplicar)
        self.editor_homografia = EditorHomografia(
            self.pipeline.cfg.homografia,
            self.pipeline.cfg.camara,
            self.pipeline.transformador,
        )

        # Cola para comunicar eventos de voz con el hilo principal de OpenCV
        self.event_queue: queue.Queue[tuple[ComandoVoz, str]] = queue.Queue()

        # Inicializar Control por Voz
        self.activar_voz = activar_voz
        self.ultimo_comando_voz = "Ninguno"
        if self.activar_voz:
            self.voice = VoiceController(
                idioma="es-AR",
                on_command_callback=self._on_voice_command,
                habilitar_tts=True,
            )
        else:
            self.voice = None

        # Estados del sistema
        self.conectado_camara = False
        self.conectado_gripper = False
        self.modo_automatico = False
        self.estado_actual = "INACTIVO"
        self.ultima_deteccion = {"ok": False, "motivo_fallo": "sistema no iniciado"}
        self.abortar_ciclo = False
        # Parada de seguridad por software (tecla [e] / boton "detener").
        # No hay E-STOP fisico en el gripper: la parada certificada es la del UR5.
        self.parada_activa = False

        # Cámara predeterminada (índice guardado en config.json)
        self.camara_predeterminada = int(self.cfg.get("camara", {}).get("indice_camara", 0))

    def _on_voice_command(self, cmd: ComandoVoz, texto_reconocido: str):
        """Callback ejecutado en hilo de audio: encola el comando para el hilo principal."""
        self.ultimo_comando_voz = f"{cmd.name} ('{texto_reconocido}')"
        self.event_queue.put((cmd, texto_reconocido))

    def conectar_hardware(self):
        logger.info("Conectando hardware desde el dashboard...")
        conectado_todo = self.robot_controller.conectar()
        # Verificar estados derivados
        self.conectado_camara = self.pipeline.visor.conectado if hasattr(self.pipeline.visor, 'conectado') else True
        self.conectado_gripper = not self.gripper.simulado

        if self.voice and self.activar_voz:
            self.voice.iniciar_escucha()

        if conectado_todo:
            self.estado_actual = "LISTO"
            logger.info("¡Hardware y robot conectados correctamente!")
            if self.voice:
                self.voice.hablar("Sistema UR5 conectado y listo para operar.")
        else:
            self.estado_actual = "MODO_MOCK"
            logger.warning("Iniciando en MODO_MOCK / Simulación.")
            if self.voice:
                self.voice.hablar("Iniciado en modo de simulación.")

    def fijar_camara_predeterminada(self, indice: int) -> None:
        """Fija una cámara como predeterminada: actualiza config.json y la config en memoria."""
        self.camara_predeterminada = int(indice)
        self.pipeline.cfg.camara.indice_camara = self.camara_predeterminada
        self.pipeline.visor.cfg.indice_camara = self.camara_predeterminada

        # Persistir SOLO la clave de cámara para no perder el resto del config.json
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("camara", {})["indice_camara"] = self.camara_predeterminada
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Cámara %d guardada como predeterminada en %s",
                        self.camara_predeterminada, self.config_path)
        except Exception:
            logger.exception("No se pudo persistir la cámara predeterminada en %s", self.config_path)

    def seleccionar_camara(self, conectar: bool = False) -> bool:
        """Detecta las cámaras disponibles y permite fijar una como predeterminada.

        Muestra una ventana con la vista previa de cada cámara detectada:
          - Teclas [0-9]: fijar esa cámara como predeterminada (se guarda en config.json).
          - [ESC] / [q]: cancelar y mantener la cámara predeterminada actual.

        Si `conectar` es True (re-selección en caliente con [k]) reconecta el visor
        con la cámara elegida; si es False (arranque) solo fija el índice y deja
        la conexión en manos de `conectar_hardware`.
        """
        indice_original = self.pipeline.visor.cfg.indice_camara

        # Liberar la cámara actual para poder sondear las demás índices
        if self.conectado_camara:
            self.pipeline.visor.desconectar()
            self.conectado_camara = False

        logger.info("Buscando cámaras disponibles (índices 0-9)...")
        caps = VisorCamara.abrir_disponibles(max_indice=9)

        eleccion: int | None = None
        if not caps:
            logger.warning("No se detectó ninguna cámara disponible.")
        elif len(caps) == 1 and next(iter(caps)) == indice_original and not conectar:
            # Única cámara y ya es la predeterminada: no hace falta mostrar la UI
            eleccion = indice_original
            logger.info("Única cámara disponible (índice %d), ya está como predeterminada.", eleccion)
        else:
            eleccion = self._ventana_seleccion(caps)

        # Liberar todas las capturas de sondeo
        for cap in caps.values():
            cap.release()

        if eleccion is not None and eleccion != indice_original:
            self.fijar_camara_predeterminada(eleccion)
            if self.voice:
                self.voice.hablar(f"Cámara {eleccion} fijada como predeterminada.")

        if conectar:
            ok = self.pipeline.conectar_camara()
            self.conectado_camara = ok
            if ok:
                logger.info("Cámara reconectada con el índice %d.", self.pipeline.visor.cfg.indice_camara)
            return ok
        return True

    def _ventana_seleccion(self, caps: dict[int, cv2.VideoCapture]) -> int | None:
        """Ventana modal con vistas previas en mosaico de las cámaras detectadas.

        Devuelve el índice elegido con [0-9], o None si se cancela con [ESC]/[q].
        """
        win = "Seleccion de Camara  |  [0-9]: fijar predeterminada  |  [ESC]: cancelar"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        TILE_W, TILE_H = 480, 270
        predeterminado = self.camara_predeterminada
        eleccion: int | None = None

        try:
            while True:
                tiles = []
                for idx in sorted(caps):
                    ok, frame = caps[idx].read()
                    if not ok or frame is None:
                        tile = np.zeros((TILE_H, TILE_W, 3), dtype=np.uint8)
                        cv2.putText(tile, f"Cam {idx} (sin senal)", (TILE_W // 2 - 90, TILE_H // 2),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    else:
                        tile = cv2.resize(frame, (TILE_W, TILE_H))

                    es_default = idx == predeterminado
                    cv2.rectangle(tile, (0, 0), (TILE_W, 34), (30, 30, 30), -1)
                    etiqueta = f"[{idx}] Cam {idx}" + ("  <PREDETERMINADA>" if es_default else "")
                    cv2.putText(tile, etiqueta, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 255, 0) if es_default else (255, 255, 255), 2)
                    if es_default:
                        cv2.rectangle(tile, (0, 0), (TILE_W - 1, TILE_H - 1), (0, 255, 0), 3)
                    tiles.append(tile)

                # Mosaico de 2 columnas
                filas = []
                for i in range(0, len(tiles), 2):
                    par = tiles[i:i + 2]
                    while len(par) < 2:
                        par.append(np.zeros((TILE_H, TILE_W, 3), dtype=np.uint8))
                    filas.append(np.hstack(par))
                lienzo = np.vstack(filas)

                cv2.putText(lienzo, "Presione el NUMERO de la camara para fijarla como predeterminada | ESC: cancelar",
                            (10, lienzo.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                cv2.imshow(win, lienzo)

                key = cv2.waitKey(30) & 0xFF
                if key == 27 or key == ord('q'):
                    logger.debug("Selección de cámara cancelada. Se mantiene el índice %d.", predeterminado)
                    break
                if ord('0') <= key <= ord('9'):
                    idx = key - ord('0')
                    if idx in caps:
                        eleccion = idx
                        logger.info("Cámara %d seleccionada como predeterminada.", idx)
                        break
                    logger.warning("El índice %d no corresponde a una cámara disponible.", idx)
        finally:
            cv2.destroyWindow(win)

        return eleccion

    def mover_ur5(self, x, y, z):
        """Envía comando de movimiento al UR5 real o simulado."""
        return self.robot_controller.mover_a(x, y, z)

    def procesar_eventos_seguridad(self):
        """Atiende los eventos asíncronos de seguridad del firmware del gripper.

        Con el hardware actual (4 servos, sin E-STOP ni sensor de proximidad)
        el unico evento posible es el watchdog: el firmware se abrio solo
        porque la PC dejo de mandar comandos validos.
        """
        try:
            evento = self.gripper.get_evento()
        except Exception:
            evento = None
        if not evento:
            return

        logger.warning("Evento de seguridad: %s", evento)
        if evento == "apertura_watchdog":
            # El firmware se abrio solo: la PC dejo de mandar comandos
            # validos por mas de 15 s. La pieza ya no esta sujeta, asi que
            # el ciclo se aborta y el robot frena.
            self.abortar_ciclo = True
            self.estado_actual = "APERTURA_WATCHDOG"
            self.robot_controller.detener()
            logger.error(
                "El gripper se abrió solo por watchdog: la PC dejó de "
                "comunicarse. La pieza se soltó; el robot fue frenado."
            )
            if self.voice:
                self.voice.hablar("El gripper se abrió solo. Pieza soltada.")
        elif evento == "apertura_watchdog_completada":
            logger.warning("Terminó la apertura por watchdog del gripper")

    def ejecutar_ciclo(self, categoria_filtro: str | None = None):
        """Ejecuta un ciclo completo de Pick and Place con verificación en bucle cerrado."""
        if self.parada_activa:
            logger.warning("Ciclo bloqueado: parada de seguridad activa.")
            return
        if not self.conectado_camara:
            return

        self.abortar_ciclo = False
        self.estado_actual = "DETECTANDO"
        frame = self.pipeline.visor.leer()
        if frame is None:
            return

        det = self.pipeline.procesar_frame(frame)
        self.ultima_deteccion = det

        if not det.get("ok"):
            if self.estado_actual != "ESPERANDO_PIEZA":
                self.estado_actual = "ESPERANDO_PIEZA"
                if self.voice:
                    self.voice.hablar("No veo ninguna pieza. Pon una en la mesa para empezar.")
            return

        categoria = det["categoria"]
        if categoria_filtro and categoria != categoria_filtro:
            logger.debug(
                "Pieza encontrada (%s) no coincide con filtro pedido (%s)",
                categoria,
                categoria_filtro,
            )
            self.estado_actual = "ESPERANDO_PIEZA"
            if self.voice:
                self.voice.hablar(f"Se detectó pieza {categoria}, pero se esperaba {categoria_filtro}.")
            return

        # Hay pieza válida
        self.estado_actual = "ROBOT_ACTUANDO"
        x_ur5, y_ur5 = det["x_ur5"], det["y_ur5"]

        z_seguro = self.cfg.get("z_seguro", 0.200)
        z_pick = self.cfg.get("z_pick", 0.050)
        z_deposit = self.cfg.get("z_deposit", 0.050)

        # 1. Aproximación y Pick
        self.mover_ur5(x_ur5, y_ur5, z_seguro)
        if self.abortar_ciclo or self.parada_activa:
            return

        self.mover_ur5(x_ur5, y_ur5, z_pick)
        if self.abortar_ciclo or self.parada_activa:
            return
        logger.debug("Cerrando gripper...")
        self.gripper.close()
        cv2.waitKey(1000)

        self.mover_ur5(x_ur5, y_ur5, z_seguro)
        if self.abortar_ciclo or self.parada_activa:
            return

        # 2. Place según categoría
        destinos = self.cfg.get("destinos", {
            "A": {"x": 0.40, "y": 0.20},
            "B": {"x": 0.40, "y": -0.20},
            "C": {"x": 0.20, "y": 0.0},
            "D": {"x": 0.20, "y": -0.20}
        })
        dest = destinos.get(categoria, {"x": 0.4, "y": 0.0})

        self.mover_ur5(dest["x"], dest["y"], z_seguro)
        if self.abortar_ciclo or self.parada_activa:
            return
        self.mover_ur5(dest["x"], dest["y"], z_deposit)
        if self.abortar_ciclo or self.parada_activa:
            return

        logger.debug("Abriendo gripper...")
        self.gripper.open()
        cv2.waitKey(1000)

        # Retirada a altura segura
        self.mover_ur5(dest["x"], dest["y"], z_seguro)

        # 3. Verificación en bucle cerrado (Closed-loop feedback)
        self.estado_actual = "VERIFICANDO"
        cv2.waitKey(300)
        logger.info("¡Verificación completada! Pieza categoría %s clasificada con éxito.", categoria)
        if self.voice:
            self.voice.hablar(f"Pieza categoría {categoria} clasificada con éxito.")

        self.estado_actual = "LISTO"

    def procesar_eventos_voz(self):
        """Procesa comandos de voz pendientes en el hilo principal."""
        while not self.event_queue.empty():
            try:
                cmd, texto = self.event_queue.get_nowait()
                logger.info("Ejecutando comando de voz: %s", cmd.name)

                if cmd == ComandoVoz.CICLO_AUTO:
                    self.ejecutar_ciclo()
                elif cmd == ComandoVoz.CLASIFICAR_ROJA:
                    self.ejecutar_ciclo(categoria_filtro="A")
                elif cmd == ComandoVoz.CLASIFICAR_AZUL:
                    self.ejecutar_ciclo(categoria_filtro="B")
                elif cmd == ComandoVoz.CLASIFICAR_VERDE:
                    self.ejecutar_ciclo(categoria_filtro="C")
                elif cmd == ComandoVoz.CLASIFICAR_VIOLETA:
                    self.ejecutar_ciclo(categoria_filtro="D")
                elif cmd == ComandoVoz.ABRIR_GRIPPER:
                    self.gripper.open()
                elif cmd == ComandoVoz.CERRAR_GRIPPER:
                    self.gripper.close()
                elif cmd == ComandoVoz.HOME:
                    self.mover_ur5(0.30, 0.0, 0.25)
                elif cmd == ComandoVoz.DETENER:
                    self.abortar_ciclo = True
                    self.estado_actual = "PARADA_SEGURIDAD"
                    logger.warning("¡Parada de seguridad activada por voz!")
                elif cmd == ComandoVoz.ESTADO:
                    msg = f"Sistema en estado {self.estado_actual}. "
                    if self.ultima_deteccion.get("ok"):
                        msg += f"Hay una pieza categoría {self.ultima_deteccion['categoria']} en la mesa."
                    else:
                        msg += "No hay piezas en la zona de trabajo."
                    if self.voice:
                        self.voice.hablar(msg)
                elif cmd == ComandoVoz.CONSULTAR_COLOR:
                    det = self.ultima_deteccion
                    if det.get("ok"):
                        nombres_colores = {"A": "roja", "B": "azul", "C": "verde", "D": "violeta"}
                        color = nombres_colores.get(det["categoria"], "desconocido")
                        self.voice.hablar(f"La pieza detectada es de color {color}.")
                    else:
                        self.voice.hablar("No veo ninguna pieza para decirte de qué color es.")

                self.event_queue.task_done()
            except queue.Empty:
                break

    def _on_mouse(self, evento, x, y, flags, param):
        """Clics sobre la ventana: registra puntos del asistente de homografía.

        Las coordenadas llegan en píxeles de la imagen completa (OpenCV las
        escala si la ventana fue redimensionada); el editor las convierte a
        coordenadas del recorte ROI.
        """
        if evento != cv2.EVENT_LBUTTONDOWN:
            return
        ok, msg = self.editor_homografia.mover(
            "punto", f"{x},{y}"
        )
        logger.info("Editor homografía: %s", msg)

    def correr(self):
        # 0. Selección de cámara: detecta las disponibles y fija la predeterminada
        self.seleccionar_camara(conectar=False)
        self.conectar_hardware()

        ventana_nombre = "Megadesafio UPrO — Dashboard UR5e con Voz"
        cv2.namedWindow(ventana_nombre, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(ventana_nombre, 1280, 800)

        # Clics del mouse: solo se usan con el asistente de homografía activo
        cv2.setMouseCallback(ventana_nombre, self._on_mouse)

        logger.info("Iniciando bucle del Dashboard. Presione 'q' para salir.")
        logger.info("Atajos: [SPACE] Ciclo | [v] Voz ON/OFF | [o] Abrir | [c] Cerrar | [a] Auto | [k] Seleccionar camara | [i] Ver ROI | [Flechas] Mover ROI | [+] [-] Tamaño ROI | [h] Mascara HSV | [x] Color | [1-0] Ajustar HSV (9/0 confianza) | [g] Calibrar homografia (clics) | [Backspace] deshacer punto | [Enter] aplicar | [Esc] cancelar | [p] Guardar config | [q] Salir")

        try:
            while True:
                # 1. Atender comandos de voz
                self.procesar_eventos_voz()
                # 1b. Atender eventos de seguridad del Arduino (E-stop / proximidad)
                self.procesar_eventos_seguridad()

                frame = self.pipeline.visor.leer()
                if frame is None:
                    logger.error("No se pudo leer frame de la cámara.")
                    break

                # 2. Procesar visión en tiempo real para mostrar feedback visual
                det = self.pipeline.procesar_frame(frame)
                self.ultima_deteccion = det
                frame_dibujado = self.pipeline.dibujar(frame, det)

                # Rectángulo amarillo de la ROI (si está activada la visualización)
                frame_dibujado = self.editor_roi.dibujar(frame_dibujado)
                # Máscara HSV en vivo (pinta los píxeles capturados por cada color)
                frame_dibujado = self.editor_hsv.dibujar(frame_dibujado)
                # Puntos del asistente de homografía (solo en modo calibración)
                self.editor_homografia.tamano_frame = (frame.shape[1], frame.shape[0])
                frame_dibujado = self.editor_homografia.dibujar(frame_dibujado)

                # Si estamos en modo automático continuo, ejecutar ciclo
                if self.modo_automatico and self.estado_actual in ("LISTO", "ESPERANDO_PIEZA"):
                    self.ejecutar_ciclo()

                # 3. Dibujar Panel de Control (HUD) en pantalla
                h, w = frame_dibujado.shape[:2]

                # Barra superior de estado
                color_estado = (0, 255, 0) if self.estado_actual in ("LISTO", "ROBOT_ACTUANDO", "VERIFICANDO") else (0, 0, 255)
                cv2.rectangle(frame_dibujado, (0, 0), (w, 80), (40, 40, 40), -1)

                txt_estado = f"ESTADO: {self.estado_actual} | AUTO: {'ON' if self.modo_automatico else 'OFF'}"
                cv2.putText(frame_dibujado, txt_estado, (20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_estado, 2)

                hw_status = f"Camara[{self.pipeline.visor.cfg.indice_camara}]: {'OK' if self.conectado_camara else 'FAIL'} | Gripper: {'OK' if self.conectado_gripper else 'MOCK'} | Voz: {'ACTIVA' if self.activar_voz else 'OFF'}"
                cv2.putText(frame_dibujado, hw_status, (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

                txt_voz = f"Ultimo comando de voz: {self.ultimo_comando_voz}"
                cv2.putText(frame_dibujado, txt_voz, (20, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                # Banner interactivo de "¿Qué quieres hacer?"
                if self.estado_actual == "LISTO" and not det.get("ok"):
                    cv2.rectangle(frame_dibujado, (w // 4, h // 2 - 40), (3 * w // 4, h // 2 + 10), (0, 100, 0), -1)
                    cv2.putText(frame_dibujado, "¿Que quieres que haga?", (w // 4 + 20, h // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    cv2.putText(frame_dibujado, "[Dí: 'clasificar', 'home', 'abrir gripper'...]", (w // 4 + 20, h // 2 + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

                # Panel inferior de información de detección
                cv2.rectangle(frame_dibujado, (0, h - 90), (w, h), (40, 40, 40), -1)
                if det.get("ok"):
                    info_det = f"PIEZA DETECTADA -> Cat: {det['categoria']} | px: ({det['x_px']}, {det['y_px']}) | UR5: ({det['x_ur5'] * 1000:.1f}, {det['y_ur5'] * 1000:.1f} mm) | Conf: {det['conf']:.2f}"
                    color_det = (0, 255, 255)
                else:
                    info_det = f"BUSCANDO PIEZA... Motivo: {det.get('motivo_fallo', 'desconocido')}"
                    color_det = (100, 100, 255)
                cv2.putText(frame_dibujado, info_det, (20, h - 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_det, 2)

                # Estado de la ROI (solo si está visible el rectángulo amarillo)
                if self.editor_roi.mostrar:
                    cv2.putText(frame_dibujado, f"ROI: {self.editor_roi.texto()}  [p] guardar",
                                (20, h - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

                # Estado del editor HSV (color activo + máscara en vivo)
                if self.editor_hsv.mostrar:
                    color_txt = COLOR_TEXTO_HUD.get(self.editor_hsv.color_actual, (255, 255, 255))
                    cv2.putText(frame_dibujado,
                                f"HSV {self.editor_hsv.texto()}  px:{self.editor_hsv.pixeles}  conf>={self.editor_hsv.cfg.confianza_minima:.2f}  [x] color [1-0] ajustar",
                                (20, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color_txt, 1)

                ayuda_txt = "Voz: 'clasificar', 'abrir/cerrar pinza', 'alto' | Teclas: [SPACE] Ciclo | [k] Camara | [i] ROI | [h] Mascara | [x] Color | [1-0] HSV (9/0 confianza) | [g] Homografia (clics) | [p] Guardar | [e] PARO | [r] Reset | [q] Salir"
                cv2.putText(frame_dibujado, ayuda_txt, (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)

                cv2.imshow(ventana_nombre, frame_dibujado)

                # Manejo de teclado (10 ms de espera)
                key = cv2.waitKey(10) & 0xFF
                if key == ord('q'):
                    logger.debug("Saliendo del Dashboard...")
                    break
                elif key == ord(' '):
                    logger.debug("Ejecutando ciclo manual...")
                    self.ejecutar_ciclo()
                elif key == ord('a'):
                    self.modo_automatico = not self.modo_automatico
                    logger.info("Modo automático cambiado a: %s", self.modo_automatico)
                elif key == ord('o'):
                    logger.debug("Acción manual: Abrir Gripper")
                    self.gripper.open()
                elif key == ord('c'):
                    logger.debug("Acción manual: Cerrar Gripper")
                    self.gripper.close()
                elif key == ord('v'):
                    self.activar_voz = not self.activar_voz
                    if self.voice:
                        if self.activar_voz:
                            self.voice.iniciar_escucha()
                        else:
                            self.voice.detener_escucha()
                    logger.info("Control por voz: %s", self.activar_voz)
                elif key == ord('k'):
                    logger.debug("Re-selección de cámara en caliente...")
                    self.seleccionar_camara(conectar=True)
                elif key == ord('e'):
                    self.parada_activa = True
                    self.abortar_ciclo = True
                    self.estado_actual = "PARADA_MANUAL"
                    self.robot_controller.detener()
                    logger.warning("¡Parada de seguridad MANUAL activada!")
                elif key == ord('r'):
                    self.parada_activa = False
                    self.abortar_ciclo = False
                    self.estado_actual = "LISTO"
                    logger.info("Parada de seguridad reiniciada.")
                elif key == ord('i'):
                    ok, msg = self.editor_roi.mover("mostrar")
                    logger.info("Editor ROI: %s", msg)
                elif key == ord('p'):
                    logger.info("Editor ROI: %s", self.editor_roi.aplicar(self.config_path))
                    logger.info("Editor HSV: %s", self.editor_hsv.aplicar(self.config_path))
                elif key == ord('h'):
                    ok, msg = self.editor_hsv.mover("mostrar")
                    logger.info("Editor HSV: %s", msg)
                elif key == ord('x'):
                    ok, msg = self.editor_hsv.mover("color")
                    logger.info("Editor HSV: %s", msg)
                elif key == ord('g'):
                    # Asistente de homografía: iniciar / cancelar
                    accion = "cancelar" if self.editor_homografia.calibrando else "iniciar"
                    ok, msg = self.editor_homografia.mover(accion)
                    logger.info("Editor homografía: %s", msg)
                elif key == 8:  # Backspace: deshacer el último punto marcado
                    ok, msg = self.editor_homografia.mover("deshacer")
                    logger.info("Editor homografía: %s", msg)
                elif key == 13:  # Enter: aplicar la calibración actual
                    logger.info("Editor homografía: %s",
                                self.editor_homografia.aplicar(self.config_path))
                elif key == 27:  # Esc: cancelar calibración (si está activa)
                    if self.editor_homografia.calibrando:
                        ok, msg = self.editor_homografia.mover("cancelar")
                        logger.info("Editor homografía: %s", msg)
                else:
                    # Flechas del teclado (Windows devuelve códigos extendidos)
                    # y +/- para cambiar el tamaño de la ROI
                    accion = {
                        2424832: "izquierda",   # ←
                        2555904: "derecha",     # →
                        2490368: "arriba",      # ↑
                        2621440: "abajo",       # ↓
                        ord('+'): "mas_grande",
                        ord('-'): "mas_pequena",
                    }.get(key)
                    if accion:
                        ok, msg = self.editor_roi.mover(accion)
                        logger.info("Editor ROI: %s", msg)
                    else:
                        # Atajos de calibración HSV del color seleccionado
                        accion_hsv = {
                            ord('1'): "matiz_izquierda",
                            ord('2'): "matiz_derecha",
                            ord('3'): "mas_estrecho",
                            ord('4'): "mas_amplio",
                            ord('5'): "mas_sensible",
                            ord('6'): "menos_sensible",
                            ord('7'): "area_menos",
                            ord('8'): "area_mas",
                            ord('9'): "confianza_menos",
                            ord('0'): "confianza_mas",
                        }.get(key)
                        if accion_hsv:
                            ok, msg = self.editor_hsv.mover(accion_hsv)
                            logger.info("Editor HSV: %s", msg)

        except KeyboardInterrupt:
            logger.info("Interrumpido por teclado.")
        finally:
            if self.voice:
                self.voice.cerrar()
            self.robot_controller.desconectar()
            self.pipeline.desconectar()
            cv2.destroyAllWindows()
            logger.info("Dashboard finalizado de forma segura.")


if __name__ == "__main__":
    configurar_logging()
    app = DashboardApp(activar_voz=True)
    app.correr()