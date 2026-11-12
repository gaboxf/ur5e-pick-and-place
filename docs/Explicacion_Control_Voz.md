# Funcionamiento del Control por Voz (Local / Offline)

Este documento explica cómo funciona el módulo de control por voz del sistema, destacando por qué es **100% local (offline)** y cómo procesa las órdenes sin depender de APIs en la nube.

## 1. Arquitectura del Sistema
El sistema utiliza un pipeline local que nunca envía audio a servidores externos para su "razonamiento". El flujo es el siguiente:

1.  **Captura (Micrófono):** El módulo `core/voice_control.py` escucha continuamente en segundo plano usando la biblioteca `speech_recognition`.
2.  **Transcripción:**
    *   Utiliza una API de reconocimiento de voz (por defecto, Google Speech Recognition, que procesa el audio localmente en la llamada).
    *   *Nota:* Para máxima seguridad/independencia, este componente puede cambiarse por un motor 100% offline (como `Vosk`), pero el procesamiento lógico de lo que el robot "entiende" sigue siendo local.
3.  **Análisis Semántico ("El Pensamiento"):**
    *   Aquí es donde reside la "inteligencia" del control. El texto obtenido se procesa mediante **expresiones regulares (`re`)** en Python dentro de `core/voice_control.py`.
    *   No hay un LLM (como Gemini o GPT) analizando frases. El sistema busca patrones de palabras clave predefinidas (ej: "roja", "clasificar", "pinza").
    *   Si encuentra un patrón, dispara una intención pre-programada (`ComandoVoz`).
4.  **Síntesis de Voz (Respuesta del Robot):**
    *   Utiliza `pyttsx3`, una biblioteca instalada localmente que invoca los motores de síntesis de voz (TTS) propios del sistema operativo Windows.
    *   La respuesta de voz se genera en tiempo real en tu notebook.

## 2. Ventajas del Modelo Local
*   **Latencia casi cero:** El tiempo entre decir "Detener" y que el robot reciba la orden es mínimo, ya que no hay ida y vuelta de datos a servidores externos.
*   **Independencia:** Si el laboratorio no tiene internet o la conexión es inestable, el robot **seguirá funcionando igual**.
*   **Privacidad:** Tu voz y los comandos del laboratorio no salen de la computadora.
*   **Determinismo:** Sabes exactamente cómo va a reaccionar el robot ante cada palabra. No hay riesgo de alucinaciones de IA ni comportamientos inesperados por parte de un modelo externo.

## 3. ¿Cómo "Razona" el robot? (Lógica Determinista)
El "cerebro" semántico funciona con **condicionales y búsqueda de patrones**. Ejemplo simplificado del proceso:

```python
# Lógica interna simplificada (pseudocódigo)
if "roja" in texto and "agarra" in texto:
    return ComandoVoz.CLASIFICAR_ROJA
```

Al ser una estructura basada en reglas, es **fácilmente auditable y modificable** por el usuario (tú), permitiendo agregar nuevos comandos simplemente añadiendo nuevas reglas en `core/voice_control.py`.

## 4. Estabilidad y Mantenimiento
*   **Seguridad:** Al no depender de una API de terceros, el sistema es más estable ante cambios de versión o políticas de uso de servicios en la nube.
*   **Personalización:** Puedes cambiar las frases que activan los comandos en cualquier momento editando las listas de palabras clave en el archivo `core/voice_control.py`, sin necesidad de reentrenar ningún modelo.

---
*Este sistema de "Inteligencia Artificial" se clasifica como **sistemas expertos basados en reglas** y procesamiento de lenguaje natural local, lo que lo hace ideal para entornos industriales donde la seguridad y la fiabilidad son prioritarias.*
