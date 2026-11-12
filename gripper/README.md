# Gripper E1 — Arranque rápido (Dominio C)

## 1. Verificar el hardware ANTES de comprar/conectar

En el Encuentro 1 preguntar/inspeccionar:
- ¿El gripper del lab ya viene con Arduino/control propio? Si hay algo montado, no desarmar: medir y documentar.
- Modelo confirmado: **MG996R** (metal gears, torque ~11 kg·cm). Cables: marrón = GND, rojo = +V, naranja = señal.
- Corriente: ~1.2-1.4 A por servo bajo carga a 5 V; **hasta ~2.5 A por servo en estancamiento a 6 V**. Los 4 moviéndose juntos generan picos: dimensionar la fuente en consecuencia.
- ¡Torque de sobra! El ángulo de `CLOSE` es crítico: si se pasa, MG996R no cede — aplasta la pieza (o las puntas TPU) y sigue tirando corriente.
- Cantidad: 4 servos + 4 puntas TPU.

## 2. Wiring recomendado (UNO + 4 servos)

| Conexión | De | A |
|---|---|---|
| Señal servo 1 | cable naranja/amarillo servo | D3 UNO |
| Señal servo 2 | idem | D5 UNO |
| Señal servo 3 | idem | D6 UNO |
| Señal servo 4 | idem | D9 UNO |
| V+ servos (los 4 juntos) | servo rojo | **Fuente externa estable: 5 V ≥ 5 A (o 6 V ≥ 4 A)** |
| GND servos | servo marrón/café | Fuente externa GND **+ GND del UNO** (tierra común obligatoria) |
| LED amarillo (moviéndose) | ánodo → resistencia 330 Ω → cátodo | **D2 UNO** |
| LED verde (abierto) | ánodo → resistencia 330 Ω → cátodo | **D4 UNO** |
| LED rojo (cerrado) | ánodo → resistencia 330 Ω → cátodo | **D7 UNO** |

**Reglas de oro del wiring:**
- Los 4 servos JAMÁS se alimentan del pin 5V ni del USB del UNO (el USB limita ~500 mA; un solo MG996R en estancamiento ya supera eso).
- Condensador electrolítico **1000 µF** entre +5 V y GND de la fuente (los MG996R generan picos grandes) + **fusible 5 A** en serie con la línea +V por las dudas.
- Si la fuente disponible cumple por poco (ej. 3 A), avisar a B: se puede cambiar el firmware para mover los servos **de a uno por vez** (menos pico de corriente, ciclo algo más lento).
- Si la fuente es de 6 V: alimenta solo los servos; el UNO se energiza por USB aparte (VIN exige ≥ 7 V). Tierra común igual obligatoria.
- Tierra común: fuente ↔ UNO ↔ (luego) PC. Sin tierra común el pulso PWM no llega bien.
- No usar D0/D1 (son RX/TX del USB).
- Puertos D3/D5/D6/D9 elegidos a propósito; si se cambia uno, editar las variables `pinServo1` a `pinServo4` en el sketch y actualizar este README.
- LEDs de estado en D2/D4/D7 (libres, no chocan con servos ni con RX/TX). Cada LED lleva **resistencia en serie de 220-330 Ω**; si no, se quema el LED o el pin.

### 2.1 Montaje paso a paso (todo de-energizado hasta el paso 9)

```
[FUENTE 5-6 V ≥5A] ──[FUSIBLE 5A]──(+V rail)── capacitor 1000µF (+ a +V)
        (GND)──────────────────(GND rail)───────── capacitor (− a GND)
                                   │
                                   ├──── rojo servo 1..4  (los 4 al mismo rail +V)
                                   └──── marrón servo 1..4 (GND rail común)
                                          │
          UNO GND ────────────────────────┘   ← TIERRA COMÚN (obligatoria)
          UNO D3  ←── señal servo 1 (naranja)
          UNO D5  ←── señal servo 2 (naranja)
          UNO D6  ←── señal servo 3 (naranja)
          UNO D9  ←── señal servo 4 (naranja)
          UNO D2  ──[330Ω]── LED amarillo ── GND   (moviéndose)
          UNO D4  ──[330Ω]── LED verde    ── GND   (abierto)
          UNO D7  ──[330Ω]── LED rojo     ── GND   (cerrado)
```

1. Identificar los cables con multímetro si hay dudas: MG996R = marrón GND, rojo +V, naranja señal (algunos lotes varían tono).
2. Armar el rail de potencia: fuente → fusible 5 A en serie → línea +V de servos. Las líneas de potencia van **soldadas o en bornera** (no por la protoboard: los picos de MG996R queman los rieles).
3. Los 4 servos toman potencia del mismo punto: puenteá sus cables rojos entre sí y sus cables marrones entre sí con cable grueso (20 AWG o más), y de ahí una sola trama +V y una sola GND a la fuente.
4. Conectar el capacitor 1000 µF (≥ 10 V, respetando polaridad, − al GND) cerca del punto medio de los servos.
5. **Tierra común**: jumper desde el rail GND de la fuente a cualquier pin GND del UNO.
6. Señales: cable naranja de cada servo → D3, D5, D6, D9 del UNO. Opcional: resistencia de 330 Ω en serie de cada señal (protección del UNO).
7. El UNO queda alimentado por USB del PC. **Nunca** conectar el +V de la fuente a los pines de potencia del UNO (ni 5V ni VIN), ni invertir la polaridad de un servo (se quema).
8. Etiquetar los servos 1-4 con cinta y anotar cuál es cuál según las variables `pinServo1` a `pinServo4` del sketch.
9. Primera prueba **con un solo servo conectado**: subir el sketch, conectar solo el servo 1 (señal + rieles de potencia), correr `test_serial.py` con OPEN/CLOSE. Si ese responde bien, conectar el resto de a uno a la vez, repitiendo la verificación. Si algo falla, no haber tenido conectado todo el hardware evita quemar de a cuatro.
10. Riesgos físicos: no frenar el eje del servo con los dedos con energizado (muerde fuerte); descargar el capacitor antes de tocar el circuito (desconectar fuente y puentear brevemente +V↔GND con una resistencia).

### 2.1.1 LEDs de estado

| LED | Pin | Significado |
|---|---|---|
| Amarillo | D2 | Los servos se están moviendo |
| Verde | D4 | Quieto y **abierto** |
| Rojo | D7 | Quieto y **cerrado** (agarrando) |

Se enciende uno solo por vez. Al arrancar (posición abierta) queda verde; durante
`OPEN`/`CLOSE` pasa a amarillo y al terminar (`DONE`) queda verde o rojo. Si el
watchdog devuelve el gripper a abierto, también se ve amarillo → verde.

Conexión: `D2 → 330 Ω → ánodo LED amarillo → cátodo → GND`. Ídem D4 (verde) y
D7 (rojo). Los tres cátodos pueden compartir el GND del UNO. Los LEDs se
alimentan del propio UNO por USB (consumo despreciable).

### 2.1.2 Botón de emergencia y sensor ultrasónico (nuevo)

Se agregan dos elementos de seguridad al firmware:

| Elemento | Conexión | Función |
|---|---|---|
| Pulsador NA (E-STOP) | **D8 ↔ GND** (pullup interno) | Al pulsar: frena los servos y emite `EMERGENCY` |
| HC-SR04 VCC | 5 V del UNO | Alimentación del sensor |
| HC-SR04 GND | GND común | — |
| HC-SR04 TRIG | **D10** | Disparo de la medición |
| HC-SR04 ECHO | **D11** | Eco (el UNO es 5 V: conexión directa) |

```
UNO D8  ──[ pulsador NA ]── GND        (LOW = emergencia)
UNO D10 ── TRIG  HC-SR04
UNO D11 ── ECHO  HC-SR04
UNO 5V  ── VCC   HC-SR04
UNO GND ── GND   HC-SR04
```

**Comportamiento:**

- E-STOP: mientras el botón esté pulsado, los servos quedan frenados y el
  Arduino emite `EMERGENCY` una sola vez. Al soltarlo emite `READY`.
- Proximidad: cada 100 ms mide distancia. Si hay un objeto a **< 15 cm**, emite
  `PROXIMITY_STOP` (una sola vez) y se rearma cuando el objeto se aleja a > 20 cm.
- El sketch compila y usa ~20 % de la memoria del UNO.

> El sensor está pensado para montarse en el gripper mirando hacia adelante, de
> modo que detecte el acercamiento de una mano al efector. Los eventos los
> consume el Python (`GripperClient.get_evento()`) para frenar el UR5.

### 2.2 ¿Con qué alimentar los servos? (opciones reales)

Las cuatro formas válidas de energizar — todas terminan igual: +V al punto común de los rojos, GND al de los marrones, y ese GND unido al GND del UNO.

| Opción | Qué es físicamente | Veredicto |
|---|---|---|
| **Fuente de bancada del lab** (recomendada) | Fuente regulada con bornes banana; se fija 6.0 V y se limita la corriente | ✅ Pedir al docente en el E1 — es exactamente el instrumento para esto |
| Adaptador industrial 5-6 V | PSU de pared potente (5 V/10 A o similar) con conector de barril | ✅ Si el lab lo presta |
| Batería portátil | 5×AA NiMH (=6 V) en portapilas, o LiPo 2S + módulo UBEC que baja a 6 V | ✅ Útil para probar lejos de la bancada |
| 12 V del lab + módulo buck (XL4016) | Regulador que baja 12 → 6 V | ⚠️ Solo si es lo que hay; requiere módulo y soldadura |
| ~~Pin 5V / USB del UNO~~ | — | ❌ Nunca: 500 mA contra picos de 2.5 A por servo |

**Orden de energizado (verificación con multímetro):**
1. Con los servos AÚN desconectados: encender la fuente, medir +V real (5.0-6.2 V) y la polaridad. Una inversión quema un MG996R.
2. Con el multímetro en continuidad: chequear UNO GND ↔ rail GND de la fuente **antes** de conectar la primera señal.
3. Recién entonces: señales (naranja) a D3/D5/D6/D9 y UNO por USB.

Si el lab solo dispone de una fuente de 3 A: avisarlo a B — se prepara la variante del firmware que mueve los servos de a uno por vez (menor pico de corriente, ciclo más lento).

## 3. Protocolo (contrato con B — igual que en plan_trabajo_equipo_ur5.md §4.3)

Baud **115200**, líneas ASCII terminadas en `\n`:

| Comando (Python → Arduino) | Respuesta (Arduino → Python) |
|---|---|
| `OPEN` | `DONE` cuando los 4 terminaron de abrirse |
| `CLOSE` | `DONE` cuando los 4 terminaron de cerrarse |
| `STATUS` | `READY` / `BUSY` / `ERROR` (responde en cualquier momento, sin bloquear el movimiento) |
| (sin respuesta en 2 s del lado B) | B toma como ERROR y frena el ciclo |
| (sin mensajes por >5 s desde Arduino) | watchdog interno → vuelve a OPEN de forma segura |
| (evento asincrónico) | `EMERGENCY` → botón de parada física pulsado |
| (evento asincrónico) | `PROXIMITY_STOP` → objeto a menos de 15 cm del sensor |

Movimiento suave: los servos avanzan 1° cada ~15 ms (~2 s por ciclo completo), para no tirar la pieza de un tirón.

## 4. Calibración (hacerla con el gripper físico en la mano)

1. Subir el sketch con los valores por defecto.
2. Con el gripper en la mano y SIN pieza: enviar `CLOSE`; ver si la apertura coincide con el ancho de la pieza.
3. Ajustar los valores `cerrado1` a `cerrado4` (uno por servo, al inicio del sketch) hasta sujetar sin aplastar. Anotar los valores finales.
4. Los valores finos quedan en el sketch (y se anotan en este README como historial).

5. **No forzar a mano los servos con energizado** (pueden quemarse).

## 5. Probar todo SIN robot y sin piezas

1. `pip install pyserial`
2. Conectar el UNO por USB y cerrar el Serial Monitor del Arduino IDE (así el puerto serial queda libre).
3. `python tools/test_serial.py COM3` (Windows: COM3/COM4; Linux: /dev/ttyUSB0)
4. Escribir `OPEN` + Enter → deben moverse los 4 y ver `DONE`.
5. Escribir `CLOSE` + Enter → moverse y `DONE`.
6. Escribir `STATUS` durante el movimiento → `BUSY`.
7. Dejar 6 s sin escribir nada → debe volver a `OPEN` solo (watchdog).

Si un servo no se mueve → revisar pin y conexión de señal. Si todos tiemblan → falta tierra común o el condensador.

## 5.1 Cargar el sketch sin Arduino IDE (arduino-cli)

Con el UNO conectado por USB, no hace falta instalar el IDE. La CLI compila y sube:

```bash
arduino-cli lib install Servo          # una sola vez
arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/gripper_4servos
arduino-cli board list                 # muestra el puerto, ej. /dev/ttyACM0
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno gripper/firmware/gripper_4servos
```

La carpeta del sketch debe llamarse igual que el `.ino` (por eso es
`firmware/gripper_4servos/gripper_4servos.ino` y no un archivo suelto).

## 6. Entregables de este módulo (para el final)

- `firmware/gripper_4servos/gripper_4servos.ino` (sketch final con ángulos calibrados)
- `tools/test_serial.py` (tester interactivo)
- `tools/gripper_client.py` (cliente Python para integración con el orquestador)
- Este README con: valores de calibración finales, modelo de servos, fuente usada, fotos del montaje

## 7. Ficha técnica de piezas (medidas ideales)

> Ajustar solo si la apertura real del gripper medida en el lab es menor que 60 mm; en ese caso escalar todo proporcionalmente.

| Parámetro | Categoría A — cilindro (roja) | Categoría B — cubo (azul) |
|---|---|---|
| Diámetro / lado de agarre | **50 mm** | **50 mm** (mismo agarre que A) |
| Altura | **50 mm** | **50 mm** (igual a A: un solo plano Z) |
| Ancho de agarre (lo que cierra el gripper) | 50 mm | 50 mm |
| Peso objetivo | 50-100 g | 50-100 g |
| Material | PLA sólido (o piezas compradas/pintadas con acrílico) | Ídem |
| Color | Rojo saturado, LISO, sin patrones ni letras | Azul saturado, LISO, idem |
| Superficie | Mate (no brillante: reduce reflejos y confunde HSV) | Mate idem |
| Cantidad | 3 + 2 repuestos | 3 + 2 repuestos |
| Base | Plana, sin chaflán excesivo que resbale | Plana idem |

**Por qué estas medidas:**
- **50 mm de agarre**: zona cómoda para un gripper educativo (apertura típica 60-100 mm); deja holgura de apertura y cierre sin exigir el límite a las puntas.
- **Misma dimensión de agarre en ambas categorías**: un solo ángulo de cierre, un solo plano de agarre, un solo transporte → menos parámetros que mantener.
- **50-100 g**: torque de MG996R + fricción de puntas TPU con margen de sobra; si una pieza pesara más de 200 g, repetir el test de banco al sacudir.
- **Colores lisos y mate**: la segmentación HSV da máscara sólida y limpia; patrones o transparencia traen huecos en la máscara y comprometen el centroide.
- **Alturas iguales**: la pieza descansa sobre el plano de trabajo en la misma medida — buena para la homografía y el descenso del UR5.

**Impresión 3D (si hacemos piezas):**
- Extrusión simple, 3 perímetros, relleno 20-30 %, sin soportes, capa pequeña para uniformidad.
- Verificar diámetro/lado real con calibre tras imprimir: si sale 47 mm y el CLOSE quedó calibrado para 50 → recalibrar o rechazar la impresión.
- Anotar en esta ficha los valores medidos reales de cada pieza.

**Tareas de E1 vinculadas (C):** medir apertura del gripper (min/max) → si ≥ 60 mm, las piezas se quedan como están; si no, ajustar esta tabla ANTES de imprimir.
