# Guía de Operación y Ajustes — Desafío 2: UR5

Esta guía contiene los parámetros críticos para configurar y ajustar el sistema en el laboratorio cuando el robot esté conectado.

## 1. Ajustes de Entorno (`.env`)
Modifica este archivo para cambiar entre modo simulación y real:

- `ROBOT_MODE`: Cambiar a `real` para conectar al hardware. `mock` para simulación.
- `UR5_IP`: IP estática del robot (ej. `192.168.1.10`).
- `GRIPPER_PORT`: Puerto COM del Arduino (ej. `COM3` en Windows, `/dev/ttyACM0` en Linux).
- `ROBOT_VEL` / `ROBOT_ACC`: Ajustar para mayor/menor velocidad en los movimientos del UR5.

## 2. Ajustes de Visión y Coordenadas (`vision/config.json`)
Si el robot no está posicionándose correctamente sobre la pieza o las zonas de depósito, ajusta estos valores:

- **ROI (Region of Interest):** Define el recorte de la imagen (`roi_desde_x`, `roi_hasta_x`, etc.).
- **Homografía (`puntos_px` vs `puntos_ur5`):** 
    - `puntos_px`: Coordenadas en la imagen (relativas al recorte ROI).
    - `puntos_ur5`: Coordenadas reales en metros (relativas a Home si `usar_home_como_origen` está activo).
    - **Los valores de fábrica NO son una calibración.** Si el robot apunta al lugar equivocado, el ciclo aborta en la aproximación (el destino queda cerca de la base o fuera de la zona). Calibralo midiendo 4 esquinas con el robot:
      ```
      python tools/calibrar_homografia.py
      ```
      El asistente abre la cámara, te pide tocar cada esquina de la mesa con el TCP y hacer clic sobre la punta en la imagen; lee la pose real por RTDE (no mueve nada), calcula la homografía y, con un 5.º punto interior, informa el error en mm antes de guardar. También ajusta `zona_trabajo_*` al área medida (con 2 cm de margen) y deja backup del `config.json`.
    - Los `destinos` A/B también son valores de fábrica. Se miden igual, tocando el centro de cada zona de depósito:
      ```
      python tools/calibrar_homografia.py --destinos
      ```
- **Zona de Trabajo:** `zona_trabajo_min_x/y` y `max_x/y` define dónde permitimos que el sistema busque piezas (en el mismo sistema que `puntos_ur5`).
- **Home como punto cero (`usar_home_como_origen`):** con `true`, los valores de `puntos_ur5`, `zona_trabajo_*` y `destinos` se interpretan como desplazamientos desde la XY de `home_pose`, no desde la base. El origen se toma automáticamente de `home_pose`, no hay que duplicarlo.
- **Radio mínimo de la base (`radio_minimo_base`):** distancia horizontal mínima (m) a la que se permite el TCP. Por defecto `0.20`; si un destino queda más cerca del eje de la base, `mover_a()` rechaza el movimiento para no chocar contra la columna del robot.

> Verificación previa (no mueve el robot):
> ```
> python tools/verificar_homografia.py
> python tools/verificar_homografia.py --px 269,200
> python tools/verificar_homografia.py foto.jpg
> ```
> Muestra las esquinas calibradas, una grilla del área de trabajo y avisa si algún punto queda peligrosamente cerca de la base.

## 3. Ajustes de Movimiento (`vision/config.json`)
Los parámetros de altura son críticos para no chocar la mesa o el gripper:

- `z_seguro`: Altura de transporte (debe ser mayor a cualquier pieza).
- `z_pick`: Altura exacta para cerrar el gripper sobre la pieza.
- `z_deposit`: Altura para soltar la pieza.
- `home_pose`: Pose Home `[x, y, z, rx, ry, rz]` en metros y radianes (la misma que devuelve `getActualTCPPose()`).
- `home_tol_m` / `home_tol_rad`: Tolerancia de posición (m) y orientación (rad) para considerar que el robot ya está en Home.
- `home_al_iniciar`: Si es `true`, los dashboards verifican Home al arrancar y mueven el robot si está fuera de posición.

**Botón Home:** en el dashboard web (panel *Control*) y en el dashboard de escritorio (botón naranja **HOME** en la barra superior, se hace clic con el mouse) el robot se mueve a `home_pose`. En el de escritorio el movimiento corre en segundo plano para no congelar el video; se rechaza si hay parada activa o un ciclo en curso. También se puede pedir por voz ("home").

## 4. Vocabulario de Voz (`core/voice_control.py`)
Si el sistema no entiende tus comandos o quieres agregar nuevos:

1. Abrir `core/voice_control.py`.
2. Buscar el método `clasificar_intencion(self, texto)`.
3. Agregar un nuevo patrón `re.search` con las palabras clave deseadas.
4. **Importante:** Si agregas una acción nueva, asegúrate de añadirla en `core/dashboard.py` dentro de `procesar_eventos_voz`.

## 5. Troubleshooting (Lista de chequeo en el laboratorio)
- **El robot no se mueve pero todo está conectado:** mirá la consola del dashboard. Si aparece `TCP a N mm de la base (mínimo 200 mm): destino rechazado`, la calibración de la homografía está mal: esa coordenada cae sobre la base. Recalibrá con `python tools/calibrar_homografia.py`. Si dice `modo real sin conexión activa`, revisá `UR5_IP`/cable. Si el ciclo aborta en `sin pieza con confianza >= 0.70`, es iluminación/HSV.
- **Solo puede haber un cliente RTDE de control:** si abrís dos dashboards (o un script) a la vez, el segundo falla con `One of the RTDE input registers are already in use!`. Cerrá el otro proceso. El script de calibración usa RTDE *receive* (solo lectura) y no tiene ese problema.
- **Diagnóstico rápido de todo el hardware:** `python tools/diagnostico_hardware.py` (lista cámaras que realmente entregan imagen, puertos COM, respuesta STATUS del gripper y si el UR5 responde en RTDE). Cerrá el dashboard antes de correrlo.
- **¿No se ve la cámara?** Corré el diagnóstico y verificá el índice. En este equipo la cámara cenital es la **0** (la 1 es una webcam del aula). La primera apertura puede tardar ~20 s. Cerrá Zoom/Teams/OBS: si otra app la tiene tomada, abre pero no entrega frames.
- **¿No se mueve el gripper?** El diagnóstico debe mostrar `STATUS = READY` en el `gripper_port` (COM4). Si el puerto cambió: `python tools/detectar_puerto_gripper.py`.
- **¿No detecta piezas?** Revisar los umbrales de color HSV en `vision/config.json` (`rojo_bajo1`, `azul_bajo`, etc.) si la iluminación del laboratorio es distinta a la de tu casa. Ojo: si la imagen está oscura (brillo medio V bajo), el umbral de valor (tercer número, p. ej. `70`) puede descartar la pieza entera.
- **¿Robot bloqueado?** Si `core/dashboard.py` se congela al arrancar, es probable que la cámara esté siendo usada por otra app o el puerto COM del Arduino esté ocupado.
- **¿No escucha bien?** Ajusta `recognizer.energy_threshold` en el `__init__` de `core/voice_control.py`. Si hay mucho ruido, auméntalo.

## 6. Comandos rápidos (Cheat Sheet)
| Comando de voz | Acción en modo REAL |
|---|---|
| *"Clasificar"* | Ejecuta el ciclo completo |
| *"Agarra la pieza roja"* | Clasifica en Categoría A |
| *"Agarra la pieza azul"* | Clasifica en Categoría B |
| *"Abri gripper"* | Abre pinza física |
| *"Cerra gripper"* | Cierra pinza física |
| *"Detener"* / *"Alto"* | Parada de seguridad inmediata |
| *"Estado"* | Informe vocal de la mesa |

---
*Nota: Siempre realice las primeras pruebas con el UR5 en modo de velocidad reducida (Speed Slider en el Teach Pendant).*
