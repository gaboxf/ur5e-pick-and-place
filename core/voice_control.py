"""Módulo de Control por Voz — Desafío 2: Pick and Place UR5.

Permite interactuar con la celda robótica mediante comandos de voz en español:
- Reconocimiento de voz continuo en segundo plano (SpeechRecognition).
- Respuestas de voz sintetizadas (pyttsx3) sin bloquear el hilo principal.
- Clasificación de intenciones orientada a tareas del robot y del gripper.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
from enum import Enum
from typing import Callable, Optional

import pyttsx3
import speech_recognition as sr

# Ejecución directa (python core/voice_control.py): agregar la raíz del
# proyecto al path para poder importar el paquete "core".
from pathlib import Path
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import get_logger

logger = get_logger("VoiceControl")


class ComandoVoz(Enum):
    CICLO_AUTO = "ciclo_auto"
    CLASIFICAR_ROJA = "clasificar_roja"
    CLASIFICAR_AZUL = "clasificar_azul"
    CLASIFICAR_VERDE = "clasificar_verde"
    CLASIFICAR_VIOLETA = "clasificar_violeta"
    ABRIR_GRIPPER = "abrir_gripper"
    CERRAR_GRIPPER = "cerrar_gripper"
    HOME = "home"
    DETENER = "detener"
    ESTADO = "estado"
    CONSULTAR_COLOR = "consultar_color"
    DESCONOCIDO = "desconocido"


class VoiceController:
    """Administrador de reconocimiento y síntesis de voz para el UR5."""

    def __init__(
        self,
        idioma: str = "es-AR",
        on_command_callback: Optional[Callable[[ComandoVoz, str], None]] = None,
        habilitar_tts: bool = True,
    ):
        self.idioma = idioma
        self.on_command = on_command_callback
        self.habilitar_tts = habilitar_tts

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300  # Sensibilidad del micrófono
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8

        self._stop_listening = None
        self._escuchando = False
        self._tts_queue: queue.Queue[str] = queue.Queue()
        self._tts_thread: Optional[threading.Thread] = None
        self._tts_running = False

        if self.habilitar_tts:
            self._iniciar_tts()

    # -- Síntesis de voz (TTS) en hilo secundario ----------------------------
    def _iniciar_tts(self):
        self._tts_running = True
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

    def _tts_worker(self):
        try:
            engine = pyttsx3.init()
            # Configurar voz en español si está disponible
            voices = engine.getProperty("voices")
            for v in voices:
                if "spanish" in v.name.lower() or "español" in v.name.lower() or "sabina" in v.name.lower() or "helena" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
            engine.setProperty("rate", 165)  # Velocidad de habla natural
        except Exception as e:
            logger.warning("No se pudo inicializar pyttsx3: %s", e)
            return

        while self._tts_running:
            try:
                texto = self._tts_queue.get(timeout=0.5)
                if texto is None:
                    break
                engine.say(texto)
                engine.runAndWait()
                self._tts_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Error en síntesis de voz: %s", e)

    def hablar(self, texto: str):
        """Envía un mensaje para ser pronunciado por el robot sin bloquear."""
        logger.debug("[Voz UR5]: %s", texto)
        if not self.habilitar_tts:
            return
        self._tts_queue.put(texto)

    # -- Reconocimiento y análisis semántico ---------------------------------
    def clasificar_intencion(self, texto: str) -> tuple[ComandoVoz, str]:
        """Convierte texto libre reconocido en un comando estructurado."""
        t = texto.lower().strip()
        logger.debug("Texto escuchado: '%s'", t)

        # 1. Parada / Emergencia
        if re.search(r"\b(parar|detener|alto|stop|cancelar|emergencia)\b", t):
            return ComandoVoz.DETENER, "Parada solicitada"

        # 2. Control de Gripper
        if re.search(r"\b(abrir|abre|abri|soltar|liberar)\b", t) and re.search(r"\b(pinza|gripper|mano|garra)\b", t):
            return ComandoVoz.ABRIR_GRIPPER, "Abriendo gripper"
        if re.search(r"\b(cerrar|cierra|cerra|agarrar|sujetar|tomar|apretar)\b", t) and re.search(r"\b(pinza|gripper|mano|garra)\b", t):
            return ComandoVoz.CERRAR_GRIPPER, "Cerrando gripper"
        if t in ["abrir", "abrir pinza", "soltar", "abri", "abri gripper"]:
            return ComandoVoz.ABRIR_GRIPPER, "Abriendo gripper"
        if t in ["cerrar", "cerrar pinza", "agarrar", "cerra", "cerra gripper"]:
            return ComandoVoz.CERRAR_GRIPPER, "Cerrando gripper"

        # 3. Posición Home
        if re.search(r"\b(home|inicio|reposo|origen|posicion inicial)\b", t):
            return ComandoVoz.HOME, "Moviendo a posición Home"

        # 4. Estado / Diagnóstico
        if re.search(r"\b(estado|diagnostico|como estas|que ves|reporte)\b", t):
            return ComandoVoz.ESTADO, "Consultando estado del sistema"
        
        # Nueva regla: Preguntar color (más flexible)
        if re.search(r"\b(que color|de que color|color)\b", t):
            return ComandoVoz.CONSULTAR_COLOR, "Consultando color"

        # 5. Clasificación específica por categoría / color
        if re.search(r"\b(roja|rojo|cilindro)\b", t) and re.search(r"\b(clasificar|agarrar|tomar|llevar|pieza|agarra)\b", t):
            return ComandoVoz.CLASIFICAR_ROJA, "Clasificando pieza roja hacia Zona A"
        if re.search(r"\b(azul|cubo)\b", t) and re.search(r"\b(clasificar|agarrar|tomar|llevar|pieza|agarra)\b", t):
            return ComandoVoz.CLASIFICAR_AZUL, "Clasificando pieza azul hacia Zona B"
        if re.search(r"\b(verde)\b", t) and re.search(r"\b(clasificar|agarrar|tomar|llevar|pieza|agarra)\b", t):
            return ComandoVoz.CLASIFICAR_VERDE, "Clasificando pieza verde hacia Zona C"
        if re.search(r"\b(violeta|morada)\b", t) and re.search(r"\b(clasificar|agarrar|tomar|llevar|pieza|agarra)\b", t):
            return ComandoVoz.CLASIFICAR_VIOLETA, "Clasificando pieza violeta hacia Zona D"

        # 6. Ciclo general de Pick and Place
        if re.search(r"\b(clasificar|iniciar|comenzar|arrancar|ejecutar|recoger|tomar pieza|pick and place|agarra)\b", t):
            return ComandoVoz.CICLO_AUTO, "Iniciando ciclo automático de clasificación"

        return ComandoVoz.DESCONOCIDO, f"No comprendí el comando: '{texto}'"

    def _audio_callback(self, recognizer: sr.Recognizer, audio: sr.AudioData):
        """Callback invocado por SpeechRecognition en segundo plano."""
        try:
            # Reconocimiento con la API de Google (rápida, gratis, sin requerir GPU)
            texto = recognizer.recognize_google(audio, language=self.idioma)
            comando, respuesta = self.clasificar_intencion(texto)

            if comando != ComandoVoz.DESCONOCIDO:
                self.hablar(respuesta)
            else:
                self.hablar("Comando no reconocido. Puedes decir clasificar, abrir pinza, o detener.")

            if self.on_command:
                self.on_command(comando, texto)

        except sr.UnknownValueError:
            # Audio no interpretable (ruido de fondo común en taller), se ignora silenciosamente
            pass
        except sr.RequestError as e:
            logger.error("Error al conectar con el servicio de reconocimiento: %s", e)
            self.hablar("Error de conexión en el reconocimiento de voz.")
        except Exception as e:
            logger.error("Excepción en callback de audio: %s", e)

    # -- Control del ciclo de escucha ----------------------------------------
    def iniciar_escucha(self) -> bool:
        """Inicia la captura continua de audio en segundo plano."""
        if self._escuchando:
            return True

        try:
            microphone = sr.Microphone()
            with microphone as source:
                logger.info("Calibrando ruido ambiente del micrófono (1 segundo)...")
                self.recognizer.adjust_for_ambient_noise(source, duration=1.0)

            # Inicia escucha en segundo plano (hilo nativo de SpeechRecognition)
            self._stop_listening = self.recognizer.listen_in_background(
                microphone, self._audio_callback, phrase_time_limit=4.0
            )
            self._escuchando = True
            logger.info("Escucha por voz iniciada correctamente.")
            self.hablar("Control por voz activo.")
            return True
        except Exception as e:
            logger.error("No se pudo iniciar el micrófono: %s", e)
            return False

    def detener_escucha(self):
        """Detiene la escucha en segundo plano."""
        if self._stop_listening:
            self._stop_listening(wait_for_stop=False)
            self._stop_listening = None
        self._escuchando = False
        logger.info("Escucha por voz detenida.")

    def cerrar(self):
        """Libera todos los recursos del módulo de voz."""
        self.detener_escucha()
        self._tts_running = False
        self._tts_queue.put(None)
        if self._tts_thread and self._tts_thread.is_alive():
            self._tts_thread.join(timeout=1.0)


# Test rápido del módulo si se ejecuta directamente
if __name__ == "__main__":
    def mi_callback(cmd: ComandoVoz, texto: str):
        print(f"\n[EVENTO DISPARADO] Comando: {cmd.name} | Texto original: '{texto}'\n")

    print("=" * 60)
    print("Prueba del Módulo de Control por Voz para UR5")
    print("Di comandos como:")
    print(" - 'clasificar'")
    print(" - 'clasificar pieza roja'")
    print(" - 'abrir pinza'")
    print(" - 'cerrar pinza'")
    print(" - 'detener'")
    print("Presiona Ctrl+C para finalizar.")
    print("=" * 60)

    voice = VoiceController(on_command_callback=mi_callback)
    if voice.iniciar_escucha():
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nFinalizando prueba...")
        finally:
            voice.cerrar()

