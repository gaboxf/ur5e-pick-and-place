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
- **Offset de agarre (`pick_offset_x` / `pick_offset_y`):** corrige la diferencia constante entre el centro que ve la cámara y el centro real de las pinzas (en metros, coordenadas de base). Se suma a la coordenada detectada **solo en el pick**; los destinos no se tocan. Si el TCP queda siempre desplazado el mismo tanto y hacia el mismo lado (p. ej. 5 cm), calibrarlo así:
    Se hace en **dos pasos**: la cámara no puede ver la pieza mientras el brazo está encima.
    1. **Paso 1 — capturar con la cámara:** apoyar una pieza con el robot afuera del campo de visión y ejecutar la captura. Guarda en memoria `x_ur5`, `y_ur5` y la categoría (no mueve el robot ni lee su pose).
       - Web: botón **1️⃣ Capturar pieza con cámara** o POST `/api/calibrar_robot` con `{"accion": "offset_pick_capturar"}`.
       - Escritorio: tecla **[N]**. Consola: `controller.capturar_pieza_para_offset()`.
    2. **Paso 2 — confirmar con el TCP:** con el pendant/freedrive llevar el TCP hasta el centro **real** de esa misma pieza (a la altura de agarre), dejarlo quieto y confirmar. Ahí sí se lee la pose; si el robot se está moviendo, el modo no es `real`, la lectura RTDE falla o la Z no coincide con `z_pick`, el comando lo rechaza y **no guarda nada**.
    > El offset calculado no puede superar `pick_offset_max_m` (por defecto `0.15` m = 150 mm). Si supera ese límite se descarta la captura: un valor de ~945 mm proveniente de una pose simulada mandó el robot al lado contrario. En modo MOCK la calibración está deshabilitada.
       - Web: botón **2️⃣ Confirmar TCP sobre la pieza** o POST `/api/calibrar_robot` con `{"accion": "offset_pick_confirmar"}`.
       - Escritorio: tecla **[M]**. Consola: `controller.confirmar_offset_pick()`.
    Guarda `pick_offset = TCP_medido − centro_detectado` en `vision/config.json`. Para volver a cero, poner ambos valores en `0.0`.
    > Si el error **cambia con la posición de la pieza** (mayor en una esquina que en el centro), no es offset de herramienta: la homografía está mal. Recalibrá con `python tools/calibrar_homografia.py` y revisá que el TCP esté definido en la punta de la herramienta, no en la brida.

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
- `z_deposit`: Altura para soltar la pieza. Debe ser **casi igual a `z_pick`** (la pieza se apoya en la misma mesa: p. ej. `z_pick=0.0712` → `z_deposit=0.075`, ~4 mm de holura). El código nunca baja por debajo de `z_pick` aunque el config tenga un valor menor, para no chocar la mesa.
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
- **El botón Home falla con `El robot no puede moverse: ...` o `moveL rechazado: ...`:** el mensaje indica la causa concreta (botón Teach/Freedrive del pendant activo, paro protector, modo de robot, pose fuera de alcance/límites de seguridad o sin IK). Si calibraste Home/z/destinos moviendo el brazo a mano, salí de **Freedrive** antes de pedir el movimiento.
- **El TCP queda ~5 cm al costado de la pieza al agarrar:** si el desplazamiento es constante, calibrá `pick_offset_x` / `pick_offset_y` en dos pasos: [N] capturar pieza con la cámara (robot afuera) y [M] confirmar con el TCP ya sobre la pieza (web: botones 1️⃣ y 2️⃣, o acciones `offset_pick_capturar` / `offset_pick_confirmar`). No se puede hacer de una sola vez porque el brazo tapa la pieza (ver §2). Si el error varía según dónde esté la pieza, recalibrá la homografía y verificá la definición del TCP en el controlador (debe estar en la punta del gripper, no en la brida).
- **El robot se fue al lado contrario ~90 cm al arrancar un ciclo:** había un `pick_offset_x` de 945 mm guardado, calculado con la pose simulada del modo MOCK en vez del TCP real. Se reseteó a `0.0`. Para que no vuelva a pasar: calibrar solo con `ROBOT_MODE=real` (la calibración lo exige), reiniciar el dashboard después de cambiar `.env`, y no superar `pick_offset_max_m` (150 mm).
- **La pieza se suelta desde muy alto:** `z_deposit` estaba muy por encima de `z_pick`. Ajustalo a `z_pick` + unos milímetros (ver §3).
- **Cambiaste Home y las piezas aparecen corridas:** `puntos_ur5` son offsets desde Home, así que al mover Home la homografía queda desactualizada. Volvé a medir las 4 esquinas con `python tools/calibrar_homografia.py` (y reiniciá el dashboard). El dashboard avisa con `Ojo: cambió el origen; recalibrá la homografía`.
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
