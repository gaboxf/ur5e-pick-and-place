# Visión Artificial — Desafío 2: Pick and Place UR5

> README del proyecto: visión (cámara, detección HSV, clasificación de dos
> categorías, homografía píxel→UR5), control del UR5e, gripper y dashboards
> de escritorio y web. Código fuente en [`vision/`](vision/),
> [`core/`](core/) y [`gripper/`](gripper/).

---

## Arquitectura

```
vision/pipeline.py  →  orquestador principal (entrega el dict de detección)
   ├── vision/visor.py        →  adquisición de imágenes (cámara o imagen estática)
   ├── vision/detector.py     →  segmentación HSV + centroide + categoría
   └── vision/homografia.py   →  transformación píxel → plano del UR5
vision/config.py + vision/config.json →  única fuente de parámetros compartida
vision/roi_editor.py / hsv_editor.py / homografia_editor.py →  editores visuales
   de ROI, rangos HSV y calibración de la homografía (los consumen
   core/dashboard_web.py y core/dashboard.py; guardan en vision/config.json)
core/robot_controller.py →  robot UR5 + gripper + rastreo de la pose del TCP
core/robot_tracker.py    →  historial de poses (rastro) del efector
core/robot_map.py        →  panel lateral X-Y a escala (dashboard de escritorio)
```

> **Nota importante:** `vision/config.json` es la configuración real que se lee
> al arrancar (`load_config`). Los valores de `vision/config.py` son solo los
> defaults "de fábrica" que se usan si `config.json` no existe. Los botones
> **Aplicar** de los editores guardan en `vision/config.json`. Ese mismo
> archivo también guarda parámetros del lado del robot y gripper
> (`gripper_port`, `z_seguro`, `z_pick`, `z_deposit`, `destinos`, `home_pose`).

---

## ⚠ ANTES DE INICIAR CUALQUIER PROCESAMIENTO: orden de calibración

La cámara, la ROI y la homografía forman una **cadena de dependencias**.
Si se calibran fuera de orden (o se cambia algo después), las coordenadas
que recibe el robot son incorrectas y aparecen rechazos de
*"pieza fuera de la zona de trabajo autorizada"* o recogidas en el lugar
equivocado. Respetar siempre:

```
1. Cámara fija y enfocada (montada cenital, sin moverse NUNCA después)
2. Fijar la ROI (recorte de la zona de trabajo)
3. Calibrar la homografía CON esa ROI ya fija
4. Calibrar los rangos HSV con la iluminación definitiva
5. Verificar: pieza apoyada en la mesa dentro del área calibrada
6. Recién entonces: procesamiento / ciclos de pick and place
```

### Reglas que NO se pueden saltear

1. **La cámara no se mueve después de calibrar.** La homografía describe
   dónde está la cámara respecto de la mesa; un golpe o un ajuste de
   enfoque/posición invalida toda la calibración.

2. **La ROI se fija ANTES de la homografía.** El detector trabaja sobre el
   recorte de la ROI, así que el centroide que entra a la homografía está en
   coordenadas **relativas al recorte** (origen = esquina superior izquierda
   del recorte). Los `puntos_px` de la homografía deben estar medidos en ese
   mismo sistema. El asistente de homografía hace la conversión
   automáticamente (clic sobre el video completo → resta el offset de la
   ROI), pero solo si la ROI ya es la definitiva.

3. **Si se cambia la ROI, se recalibra la homografía.** Mover o redimensionar
   la ROI cambia dónde caen los marcadores dentro del recorte: los
   `puntos_px` guardados dejan de corresponder. Repetir el paso 3.

4. **La homografía solo vale para piezas APOYADAS en la mesa.** Mapea el
   plano de la imagen contra el plano de la mesa (donde se pusieron los 4
   marcadores). Una pieza sostenida en el aire se proyecta a una coordenada
   desplazada y será rechazada por la zona de trabajo (comportamiento
   correcto: no se agarra con coordenadas no verificadas).

5. **Verificar el error antes de operar.** Apoyar una pieza en una posición
   no usada para calibrar, comparar la coordenada reportada (`x_ur5`, `y_ur5`
   en el panel Detección) contra la posición real, y registrar el error
   medio. Es un requisito de la consigna.

### Síntomas de calibración desordenada

| Síntoma | Causa probable |
|---|---|
| "pieza fuera de la zona de trabajo autorizada" con la pieza apoyada en la mesa | ROI cambiada después de calibrar, o `puntos_px` medidos sobre la imagen completa en vez del recorte |
| El robot va a un lugar desplazado (siempre hacia el mismo lado) | Offset de ROI: coordenadas en sistemas de referencia distintos |
| Detección correcta pero el pick falla en la altura | Pieza en el aire / plano de calibración distinto a la superficie de apoyo |

---

## Contrato de salida (§4.1 del plan de trabajo)

`vision/pipeline.py::procesar_frame()` devuelve un dict:

```json
{
  "ok": true,
  "categoria": "A|B",
  "x_px": 502, "y_px": 318,
  "x_ur5": 0.412, "y_ur5": 0.085,
  "conf": 0.91,
  "bbox": [480, 300, 540, 350],
  "motivo_fallo": ""
}
```

Regla: si `ok=false`, el orquestador **no mueve el robot**.

---

## Cómo usar

### Modo cámara (en tiempo real)
```python
from vision.pipeline import PipelineVision
p = PipelineVision()
p.conectar_camara()
det = p.procesar_frame()
```

### Modo imagen estática (depuración)
```python
p.conectar_imagen("fotos/prueba_rojo.jpg")
det = p.procesar_frame()
```

---

## Calibración visual de la ROI (sin editar números a mano)

La ROI (*Region of Interest*) es el recorte de la imagen donde se busca
piezas. Se define en fracciones de la imagen (`0.0` = borde, `1.0` = el otro
borde) y ahora se ajusta con botones / teclas viendo el rectángulo dibujado
en **amarillo fuerte** sobre el video.

### Dashboard web (`core/dashboard_web.py` → http://localhost:5000)

Panel **"Ajustar ROI (zona de trabajo)"**:

| Botón | Efecto |
|---|---|
| ◀ ▶ ▲ ▼ | Mueve el rectángulo completo (paso: 2% de la imagen) |
| + Grande / − Pequeña | Agranda o achica desde el centro |
| 👁 Mostrar / ocultar | Visualización opcional del rectángulo |
| ✔ Aplicar (guardar) | Guarda la ROI en `vision/config.json` |
| ↺ Reset | Restaura los valores iniciales (0.15/0.85/0.35/0.95) |

Protecciones: la ROI nunca sale de la imagen ni queda menor al 10%.

### Dashboard de escritorio (`core/dashboard.py`, ventana OpenCV)

| Tecla | Efecto |
|---|---|
| `[i]` | Mostrar / ocultar el rectángulo amarillo |
| Flechas ← → ↑ ↓ | Mover la ROI |
| `[+]` / `[-]` | Agrandar / achicar |
| `[t]` | Limpiar el rastro del robot en el mapa de trabajo |
| `[p]` | Guardar ROI **y** HSV en `vision/config.json` |

### Controles generales (dashboard de escritorio)

| Tecla / acción | Efecto |
|---|---|
| `[SPACE]` | Ejecutar un ciclo de pick & place |
| `[a]` | Activar / desactivar el modo automático |
| `[o]` / `[c]` | Abrir / cerrar el gripper |
| `[v]` | Activar / desactivar el control por voz |
| `[k]` | Volver a seleccionar la cámara en caliente |
| `[e]` | **Parada de seguridad manual** (frena el robot y aborta el ciclo) |
| `[r]` | Reiniciar la parada (requerido para volver a operar) |
| Clic en **HOME** (barra superior derecha) | Mover el robot a la pose `home_pose` de `vision/config.json` |
| `[h]` / `[x]` | Máscara HSV en vivo / cambiar el color editado |
| `[1]`–`[0]` | Ajustar rangos HSV (9/0 = umbral de confianza) |
| `[g]` | Iniciar / cancelar la calibración de homografía |
| `[N]` | Offset de pick, paso 1: capturar la pieza con la cámara (robot afuera de la vista) |
| `[M]` | Offset de pick, paso 2: con el TCP quieto sobre la pieza, leer la pose y guardar `pick_offset_x/y` |
| `[t]` | Limpiar el rastro del robot |
| `[p]` | Guardar la configuración |
| `[q]` | Salir |

Con la parada activa (`[e]`, voz *alto* o watchdog del gripper) se rechazan
todos los comandos salvo el reinicio (`[r]` / botón **Reset**).

### Procedimiento recomendado

1. Ejecutar el dashboard y activar la visualización de la ROI.
2. Poner una pieza en la mesa y ajustar el rectángulo hasta que contenga
   **solo** la mesa de trabajo (el fondo de fuera genera falsas detecciones).
3. La detección cambia al instante mientras se ajusta; validar con la pieza.
4. Apretar **Aplicar** una sola vez (si no se guarda, al reiniciar vuelve
   a los valores anteriores).

---

## Calibración visual de los colores (HSV)

El detector clasifica por color: **A rojo y B azul**.
Cada color se define con ventanas `[H, S, V]` bajo/alto (formato HSV de
OpenCV: H 0–179). El rojo tiene **dos ventanas** porque su matiz "envuelve"
el borde 0/180; el editor lo maneja automáticamente (fusiona ventanas que
se cruzan al desplazar).

### Dashboard web

Panel **"Colores (calibración HSV)"**:

| Botón | Efecto |
|---|---|
| Rojo (A) / Azul (B) / Verde (C) / Violeta (D) | Elige el color a editar (el activo queda resaltado) |
| ◀ Matiz / Matiz ▶ | Desplaza la ventana de matiz (H) |
| + Ancho / − Ancho | Ensancha o angosta la ventana de matiz |
| + Sensible / − Sensible | Baja o sube los mínimos de S y V (sombras / ruido) |
| − Área mín. / + Área mín. | Tolerancia al ruido vs. piezas chicas |
| 👁 Máscaras en vivo sí/no | **Pinta en el video los píxeles exactos que capta cada color** |
| ✔ Aplicar / ↺ Reset color | Guardar en `vision/config.json` / restaurar el color seleccionado |

Debajo de los botones se muestran en vivo los rangos actuales y la cantidad
de píxeles captados por cada color (`A 30351 · B 14194 ...`).

### Dashboard de escritorio

| Tecla | Efecto |
|---|---|
| `[h]` | Máscara en vivo sí/no |
| `[x]` | Ciclar color editado |
| `[1]`/`[2]` | Matiz − / + |
| `[3]`/`[4]` | Ancho − / + |
| `[5]`/`[6]` | Sensibilidad − / + |
| `[7]`/`[8]` | Área mínima − / + |
| `[9]`/`[0]` | Umbral de confianza − / + (más detecciones / menos ruido) |
| `[p]` | Guardar ROI y HSV en `vision/config.json` |

El HUD inferior muestra el color activo, sus rangos y los píxeles captados.

### Procedimiento recomendado

1. Activar la **máscara en vivo** y elegir el color de la pieza real.
2. Ajustar hasta que **solo** la pieza quede pintada:
   - Se pinta poca cosa → mover el **Matiz** o agrandar con **+ Ancho**.
   - Agujeros dentro de la pieza (sombras) → **+ Sensible**.
   - Se pinta también el fondo → **− Ancho** o **− Sensible**.
3. Verificar que el contador de píxeles sea estable (sin picos con la mesa vacía).
4. Repetir con el otro color y apretar **✔ Aplicar** una vez al final.

> Calibrar siempre con la **iluminación definitiva** de la celda: los rangos
> HSV dependen mucho de la luz.

---

## Calibración visual de la homografía (asistente)

La homografía es el punto crítico del proyecto: traduce el centroide de la
pieza (píxeles, relativo a la ROI) a coordenadas del UR5 (metros). Se
calibra con 4 puntos de referencia y ahora hay un **asistente visual** en
ambos dashboards que evita editar números a mano.

### Preparación física

1. Colocar 4 marcas visibles (por ejemplo, pedacitos de cinta) formando un
   rectángulo sobre la superficie de trabajo, **dentro de la ROI**.
2. Con el teach pendant del UR5, hacer que la herramienta toque cada marca
   y anotar las 4 coordenadas de base (metros, X/Y).
3. Cargar esas 4 coordenadas en `puntos_ur5` (vision/config.json) una sola
   vez: no cambian si la cámara o la ROI no se mueven. Están en metros y son
   relativas a la XY de `home_pose` (`usar_home_como_origen=true`), salvo que
   se configure lo contrario.

### Calibración medida con el robot (recomendada)

`python tools/calibrar_homografia.py` automatiza los pasos 1-3 anteriores:
abre la cámara, te pide tocar cada esquina con el TCP y hacer clic sobre la
punta en la imagen, lee la pose real por RTDE (solo lectura: no mueve el
robot), mide el error con un 5.º punto interior y guarda `puntos_px`,
`puntos_ur5` (relativos a Home) y `zona_trabajo_*` con backup del config.

Conviene cerrar los dashboards antes (la cámara la usa un solo proceso).
Opciones: `--sin-guardar`, `--margen 0.03`, `--brillo 1.8`, `--manual`.

Los `destinos` A y B son valores de fábrica; se miden del mismo modo,
tocando el centro de cada zona de depósito. El asistente mide esas dos zonas
(las que trae `vision/config.json`):

```
python tools/calibrar_homografia.py --destinos
```

### Dashboard web (`core/dashboard_web.py` → http://localhost:5000)

Panel **"Homografía (píxel → UR5)"**:

| Botón | Efecto |
|---|---|
| 🎯 Iniciar calibración | Activa el modo de marcado (espera 4 clics) |
| Clic sobre el video | Registra el punto marcado (convierte ROI automáticamente) |
| ↶ Deshacer punto | Quita el último clic |
| ✖ Cancelar | Aborta sin guardar |
| ✔ Aplicar (guardar) | Guarda `puntos_px` en vision/config.json **y recalcula la matriz al instante** |
| ↺ Reset fábrica | Restaura los puntos por defecto |

Orden de marcado: **1 sup-izq → 2 sup-der → 3 inf-der → 4 inf-izq**
(mismo orden que `puntos_ur5`). Mientras se calibra, el video muestra los
puntos guardados (cruces cian + cuadrilátero) y los recién marcados
(numerados).

### Dashboard de escritorio (`core/dashboard.py`)

| Tecla / acción | Efecto |
|---|---|
| `[g]` | Iniciar / cancelar el modo calibración |
| Clic (mouse sobre la ventana) | Registra el punto marcado |
| `[Backspace]` | Deshacer el último punto |
| `[Enter]` | Aplicar: guardar en config.json y recalibrar |
| `[Esc]` | Cancelar |

### Procedimiento recomendado

1. Verificar que la ROI sea la definitiva (ver sección "ANTES DE INICIAR").
2. 🎯 Iniciar → clic sobre las 4 marcas en orden → ✔ Aplicar.
3. Verificar con una pieza apoyada en una posición no calibrada y anotar
   el error (requisito de la consigna).
4. Si el robot aún va desplazado hacia un lado de forma constante, revisar
   que los `puntos_px` y la ROI estén en el mismo sistema (el asistente ya
   lo garantiza; el problema sería una ROI cambiada después de aplicar).

> Los `puntos_px` guardados (y los defaults de `vision/config.py`) están en
> coordenadas **relativas al recorte de la ROI**. No mezclar con
> coordenadas de la imagen completa: es la causa más común de que la
> homografía "apunte mal" por un desplazamiento constante.

---

## Monitoreo del movimiento del robot (mapa de trabajo)

Ambos dashboards muestran, al lado del video, un **mapa cenital del área de
trabajo** con la telemetría del TCP, para no tener que mirar al robot físico:

| Elemento | Significado |
|---|---|
| Punto blanco (TCP) | Posición actual del efector |
| Cruz verde | Destino del último movimiento (`mover_a`) |
| Línea punteada verde | Camino restante hasta el destino |
| Rastro naranja | Trayectoria recorrida (se aclara hacia el punto más reciente) |
| Cuadrado de color + letra | Pieza detectada por visión (A rojo y B azul) |
| Rectángulo punteado cian | `zona_trabajo_*` (límites autorizados) |
| Barra "ALTURA Z" | Altura actual con marcas de `z_pick`, `z_deposit` y `z_seguro` |
| Chip de estado | MOVIENDO / QUIETO / SIN TELEMETRÍA |

- La escala es **la misma en X e Y** (metros reales del UR5), así que las
  distancias del mapa son proporcionales a las de la mesa.
- La pose se muestrea a ~20 Hz en un **hilo aparte** (`core/robot_tracker.py`),
  por lo que el rastro se dibuja también mientras el robot se mueve.
- En el dashboard de escritorio el ciclo de pick & place corre en un hilo de
  fondo para que el panel no se congele durante los `moveL`.
- En el dashboard web el mapa se refresca a ~8 Hz desde `/api/robot`, más
  rápido que el SSE de estado (0,5 s).
- Para borrar el rastro: tecla `[t]` (escritorio) o botón **Limpiar rastro**
  (web). El rastro también se purga solo pasados 30 s.
- En **modo mock** el TCP simulado se interpola entre poses, así que el mapa
  también muestra el movimiento sin robot conectado.

---

## Parámetros clave (vision/config.json)

| Parámetro | Qué ajustar | Editor visual |
|---|---|---|
| `rojo_bajo1/alto1`, `rojo_bajo2/alto2` | Rangos HSV para el rojo (dos rangos por el wrap-around del matiz 0-180) | Panel "Colores" / teclas [1]-[8] |
| `azul_bajo/alto` | Rango HSV para el azul | Panel "Colores" / teclas [1]-[8] |
| `area_minima` / `area_maxima` | Filtrar ruido y agrupaciones de piezas | Botones "Área mín." / teclas [7]-[8] |
| `confianza_minima` | Umbral de solidez del contorno: solo se aceptan y dibujan detecciones con confianza ≥ umbral (0..1) | Botones "+/− Confianza" / teclas [9]-[0] |
| `roi_desde/hasta_x/y` | Recorte de la zona de trabajo (fracciones de la imagen) | Panel "Ajustar ROI" / teclas [i], flechas, [+] [-] |
| `puntos_px` / `puntos_ur5` | Puntos de referencia para la homografía (px relativos a la ROI / metros UR5 relativos a Home). Si nunca se midieron con el robot, calibrar con `python tools/calibrar_homografia.py` | Asistente `calibrar_homografia.py` / panel "Homografía" + clics |
| `zona_trabajo_*` | Límites de seguridad en metros (coords del UR5) | Manual |
| `gripper_port`, `z_seguro`, `z_pick`, `z_deposit`, `destinos` | Parámetros del gripper y del ciclo pick & place (compartidos con el lado robot). `z_deposit` debe ser ≈ `z_pick` (el código nunca baja por debajo de `z_pick`) | Manual / config.json |
| `pick_offset_x`, `pick_offset_y` | Corrección XY constante entre el centro detectado y el centro real de las pinzas (metros, coords. de base). Se calibra en dos pasos: `[N]` captura la pieza con la cámara y `[M]` lee el TCP ya sobre ella (web: `offset_pick_capturar` / `offset_pick_confirmar`) | `[N]` + `[M]` / botones web 1️⃣ y 2️⃣ |
| `pick_offset_max_m` | Magnitud máxima permitida para el offset de pick (por defecto `0.15` m). Un valor mayor se considera calibración/lectura inválida y aborta el ciclo | Manual / config.json |
| `home_pose`, `home_tol_m`, `home_tol_rad`, `home_al_iniciar` | Pose Home `[x, y, z, rx, ry, rz]`, tolerancias de verificación y si se corrige automáticamente al iniciar | Manual / config.json |
| `usar_home_como_origen` | Si es `true`, `puntos_ur5`, `zona_trabajo_*` y `destinos` se miden como desplazamientos desde la XY de `home_pose` (Home = punto cero) | Manual / config.json |
| `radio_minimo_base` | Distancia horizontal mínima (m) del TCP al eje de la base; por debajo se rechaza el movimiento para no chocar la columna | Manual / config.json |

---

## Entregables de este módulo

- `vision/pipeline.py` — orquestador
- `vision/visor.py` — adquisición
- `vision/detector.py` — segmentación y clasificación
- `vision/homografia.py` — transformación de coordenadas
- `vision/roi_editor.py` — editor visual de la ROI (botones web / teclas de escritorio)
- `vision/hsv_editor.py` — editor visual de rangos HSV con máscara en vivo
- `vision/homografia_editor.py` — asistente de calibración de la homografía (clics)
- `vision/config.py` / `vision/config.json` — configuración compartida
- `core/robot_controller.py` — orquestación de UR5 + gripper (Home, destinos, parada)
- `core/ur5_client.py` — cliente RTDE / movimiento del UR5
- `core/voice_control.py` — reconocimiento de comandos de voz
- `core/robot_tracker.py` — rastreo de la pose del TCP (rastro para el mapa)
- `core/robot_map.py` — mapa de trabajo X-Y a escala (panel del dashboard)
- `core/dashboard.py` — dashboard de escritorio (OpenCV + voz)
- `core/dashboard_web.py` / `templates/dashboard.html` — dashboard web (Flask + SSE)
- `gripper/firmware/` / `gripper/tools/gripper_client.py` — firmware y cliente del gripper
- `docs/Guia_Ajustes_Laboratorio.md` — guía de ajustes en laboratorio
- `tools/calibrar_homografia.py` — calibración de la homografía midiendo con el robot
- `tools/verificar_homografia.py` — verificación previa (no mueve el robot)
- `tools/diagnostico_hardware.py` — diagnóstico de cámara, gripper y UR5
- `README.md` — este archivo
- Evidencia: capturas de pantalla de detecciones correctas/incorrectas y error de homografía
