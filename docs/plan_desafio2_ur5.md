# Plan de proyecto — Desafío 2: Pick and Place guiado por visión (UR5)

> **Para presentar en el Encuentro 1 y solicitar el slot del UR5**
> Basada en: `desafio_upro.md` — Desafío 2

---

## 1. Datos

| Campo | Valor |
|---|---|
| Desafío | **2 — Pick and Place guiado por visión artificial** |
| Modalidad | Grupal — **3 integrantes** (obligatorio) |
| Integrantes | *(completar 3 nombres)* |
| Robot | Universal Robots UR5 + gripper de 4 servos con puntas TPU |

---

## 2. Problema propuesto

Celda que clasifica piezas de **dos categorías** colocadas en posiciones variables dentro de una zona de trabajo:

1. La cámara detecta y clasifica la pieza, y calcula **dónde está** (coords. UR5).
2. El UR5 la recoge con el gripper y la deposita en el **destino de su categoría**.
3. Ninguna coordenada se ingresa a mano: todo sale de la visión. Un ciclo completo = detectar → agarrar → transportar → soltar → quedar listo para el próximo.

---

## 3. Piezas y categorías

| Categoría | Forma | Diferenciador visual | Destino |
|---|---|---|---|
| Categoría A | Cilindro (o pieza impresa en 3D) | **Color rojo** | Zona A |
| Categoría B | Cubo (o pieza impresa en 3D) | **Color azul** | Zona B |

**Criterios de diseño (los del enunciado):**
- Altura uniforme y extrusión simple → agarre compatible con puntas TPU y abertura del gripper.
- Colores muy saturados → segmentación robusta por HSV (menos sensible a luz).
- Base plana estable: sin pérdida durante el transporte.
- Si imprimimos en 3D: STL simples y livianos, fabricación rápida. No es el foco del proyecto.

---

## 4. Arquitectura del sistema

```
[Cámara fija (cenital)] → [Python: visión + transformación] → [coords + categoría]
        ↓                                                        ↓
        ↓                                              [Python: máquina de estados]
        ↓                                                        ↓
[Gripper: Arduino + 4 servos] ←──── serial ────→ [UR5: URScript / RTDE-socket]
```

**Pipeline por ciclo (con Handshake RTDE y Verificación en Lazo Cerrado):**
1. Operador coloca pieza dentro de la zona y confirma inicio (con el robot en condición segura).
2. Visión: captura → recorte de ROI → segmentación HSV → contorno → **centroide de la pieza + categoría** (color/forma).
3. Transformación píxel→UR5 (homografía calibrada, ver punto 5).
4. Máquina de estados en Python: `LISTO → ESPERANDO_PIEZA → ROBOT_ACTUANDO → VERIFICANDO → DEPOSITADO → LISTO`.
5. UR5 ejecuta:
   - Acercamiento y descenso a cota de pick con **handshake RTDE determinista** (tolerancia euclidiana $\le 3\text{ mm}$ y quietud).
   - Cierre del gripper con espera y validación del token `DONE` del Arduino por puerto serie.
   - Elevación a `z_seguro` y leve desplazamiento hacia destino ($8\text{ cm}$) para despejar la visual cenital.
   - **Confirmación visual de agarre (Closed-Loop Vision):** La cámara vuelve a inspeccionar el punto de recogida en la mesa.
     - Si la pieza sigue en la mesa: se detecta agarre fallido, el gripper se abre, el robot se retira a Home y el ciclo se cancela de forma segura (sin viajar en falso).
     - Si la mesa está vacía: agarre confirmado, continúa el transporte.
   - Traslado al destino de su categoría → descenso a `z_deposit` → apertura confirmada → retirada segura a `z_seguro`.
6. Si no hay pieza válida detectada: se informa y **no se ejecuta trayectoria** (estado `ESPERANDO_PIEZA`).

---

## 5. El punto crítico: correspondencia cámara ↔ UR5

Es donde se juega la nota (indicador con 8 XP). Estrategia:

1. Definir 4 puntos de referencia visibles en la superficie a la altura de la pieza.
2. Calibrar **homografía** píxel→plano de la pieza con OpenCV.
3. Mapear el plano de imagen al plano de trabajo del UR5 usando posiciones enseñadas del robot (la herramienta toca los 4 puntos y se registran sus coords. de base).
4. Verificación: colocar piezas en **posiciones no calibradas** y comprobar el error de la posición de recogida; documentar ese error como sistema de validación.
5. Límites de seguridad: zona delimitada, coords. halladas fuera de la zona se rechazan (la visión nunca puede mover el robot fuera de los límites).

---

## 6. Gripper

- 4 servomotores, puntas TPU: control de apertura/cierre por **Arduino**, comandos de Python por serial (`OPEN`/`CLOSE` con timeout y confirmación).
- Calibrar la abertura según las piezas (gap de agarre sin aplastar).
- Sincronización: el UR5 solo cierra/desagarra cuando llega al waypoint correspondiente.

**Hardware a solicitar/confirmar en el Encuentro 1:**
- ¿El gripper ya incluye electrónica de control o hay que montarla nosotros?
- Arduino UNO (o similar) + cable USB.
- **Fuente 5 V externa para los servos** (los 4 servos NO se alimentan del USB del Arduino; común GND entre Arduino/fuente).
- Jumpers, protoboard o placa de prueba, conectores.
- Confirmar cámara disponible y cómo se conectará a la PC: webcam USB externa, montada **cenital y con soporte rígido** sobre la zona de trabajo (cinta/abrazadera). La cámara no se mueve después de calibrar la homografía. Delimitar la zona de trabajo con cinta sobre la mesa (sirve para ROI y límites de seguridad).

---

## 7. División de tareas (3 integrantes)

### Persona 1 — Visión artificial
- Colocar y fijar la cámara (cenital) sobre la zona de trabajo.
- Programar: captura → ROI → segmentación HSV → contorno → centroide + categoría (color).
- Calibrar la homografía píxel→UR5 con 4 puntos de referencia (usar el robot para tocar los 4 puntos y registrar sus coordenadas).
- Medir el error de transformación con piezas en posiciones no calibradas y documentarlo.

### Persona 2 — Robot UR5 + integración
- Aprender la programación del UR5 (documentación oficial + URSim) y definir waypoints: aproximación, PICK, depósitos A/B, posición segura.
- Validar cada trayectoria dry-run (sin pieza) antes de ejecutarla de verdad.
- Escribir la máquina de estados en Python que coordina: visión → transformación → robot → gripper.
- Convertir las coordenadas de la pieza que entrega la visión en trayectorias dentro de la zona autorizada (rechazar coords. fuera de límites).

### Persona 3 — Gripper + piezas
- Conectar y programar los 4 servomotores con Arduino.
- Protocolo serial Python↔Arduino: comandos OPEN/CLOSE, confirmación (ACK) y watchdog (si se cae la comunicación, el sistema queda seguro).
- Calibrar la abertura del gripper contra las piezas reales (sujeción sin aplastar).
- Seleccionar/diseñar las piezas: 2 categorías por color, altura uniforme, base plana estable; imprimirlas en 3D si hace falta.

### Compartidos entre los tres
- Cada uno debe poder explicar el subsistema de los otros (entrevistas individuales del docente).
- Colocar piezas en condiciones seguras y protocolo de seguridad del lab.
- Documentación (un redactor, los demás revisan) y video de funcionamiento.
- Ronda de prueba conjunta: piezas en varias posiciones + ambas categorías + caso sin pieza.

---

## 8. Cronograma

| Encuentro | Meta |
|---|---|
| **1** | Presentar este plan, confirmar slot. Definir/imprimir piezas. Empieza visión con muestras de prueba. |
| **2** | **Prototipo funcional:** detección + coords en pantalla correctas sobre el tablero real; calibración de homografía lista; trayectorias del UR5 validadas dry-run (sin pieza real). |
| **3** | **Integración:** ciclo completo con pieza real (agarre/transporte). Tensión en caja de abertura del gripper. Pruebas por variación de posición + ambas categorías. |
| **4** | Demo: docente cambia posiciones de piezas, ambas categorías, respuesta correcta ante piece no detectada. |

---

## 9. Pruebas que el docente hará (prepararlas explícitamente)

- Piezas en distintas posiciones de la zona (incl. posiciones que no usamos para calibrar).
- Las dos categorías con sus destinos correctos.
- Ausencia de pieza → no debe haber trayectoria.
- **Confirmación de agarre (Closed-Loop):** si la pieza resbala o no se agarra, la visión detecta su presencia en la mesa tras el intento de pick y aborta el ciclo sin viajar en vacío.
- Widget/monitor de estado: qué hace el sistema en cada momento (incluyendo estado `VERIFICANDO`).
- Nuevo trabajo sin tocar código entre trabajo y trabajo.

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Homografía desviada por altura de pieza | Piezas de altura uniforme; planicie entre cámara y superficie; calibración a la altura correspondiente |
| Horario de acceso al UR5 compartido | Simular todo en Python/Mock antes; llegar con visión y coords validadas cuando toque robot |
| Gripper pierde la pieza | Ajuste de fuerza/apertura; transporte lento; ensayos previos |
| Curva de aprendizaje UR5 (venimos de ABB) | Empezar con docs oficiales y el simulador URSim en el Encuentro 1 — la cinemática es la misma que estudiamos |
| Comunicación RPi/serial colgada | watchdog + timeouts + confirmación en el Arduino; robot nunca actúa sin ACK |

---

## 11. Seguridad (no negociable según la consigna)

- Trayectorias nuevas validadas dry-run antes de mano a mano.
- Nadie dentro del espacio de movimiento durante operar automático.
- Colocar/quitar piezas solo con robot en condición segura.
- Visión no puede ejecutar movs fuera de la zona (control de límites en la app también).
- La función de protección de la celda **no** depende del código Python.

---

## 12. Entregables para cerrar

Código fuente + programa del UR5 + control del gripper + configs de calibración + STL si imprimimos + README + video + demo presencial en el Encuentro 4.

---

## Apéndice A — UR5: arranque y operativo (CB3 típico)

1. Interruptor principal del control box → boot (~1 min) hasta que arranque UR PolyScope en el teach pendant.
2. Init Screen: `POWER ON` → `RELEASE BRAKES` (verde: NORMAL + BRAKES RELEASED = listo).
3. Switch de 3 posiciones en el frente del gabinete: `OFF` (programar por pendant) / `MANUAL` (seguridad reducida, hand-guiding) / `AUTO` (control remoto desde PC). Para la integración con Python: AUTO.
4. Pantalla `Move`: jog en 5 modos, slider de velocidad bajo para pruebas, distintas posiciones y propias.
5. Safety del lab no se toca sin el docente (planos de seguridad, límites de fuerza/velocidad).
6. Botón de emergencia físico + botón de parada en pantalla.

**Control desde Python:**
- IP del robot (preguntar en Setup > Network / al docente) → ping.
- Librería `ur_rtde` (RTDE Control Interface) o socket TCP directo al puerto **30002** enviando URScript como texto (`movel(p[x,y,z,rx,ry,rz], a=..., v=...)`).
- La Z de la pieza viene del plano calibrado (altura uniforme de piezas), no de la visión.

**URSim (sin robot real):** imagen Docker oficial `UniversalRobots/Docker-URSim` o VirtualBox. Learn URScript + pantalla de programación antes de tocar el robot.

**Preguntar al docente en E1:** ¿CB3 o eSeries? ¿IP y acceso desde la PC? ¿Config de Safety (planos/límites)? ¿Se puede usar el modo AUTO? ¿Hay backup de la config?
