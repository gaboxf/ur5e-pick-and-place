# Gripper E1 — 4 servos MG996R con UNA sola señal (gripper_1signal)

Firmware activo: `firmware/gripper_1signal/gripper_1signal.ino` (UNO + 4 servos
movidos por el mismo pulso). En el repo queda también la variante histórica
`firmware/gripper_4servos/` (4 pines independientes + E-STOP + HC-SR04 + LEDs);
**ya no es la que se usa**, pero se conserva como referencia.

Además hay un **sketch de calibración** de laboratorio:
`firmware/calibracion_1signal/calibracion_1signal.ino` (ver sección 4). No es
el firmware de producción: sirve solo para medir pulsos y aberturas.

## 1. Cómo funciona esta variante

- Los 4 cables naranjas (señal) se **puentean juntos a D3**: el mismo pulso PWM
  llega a los 4 servos.
- Los **servos 2 y 4 van montados con el cuerno invertido 180°** en el eje, para
  que el mismo ángulo mueva los dos lados de forma simétrica (los que cierran
  "al revés" compensan la inversión mecánica, no de firmware).
- Sin LEDs, sin E-STOP físico, sin HC-SR04 y sin LCD: solo los 4 servos
  (el detalle de lo que **no** lleva el montaje está en §2).
- La seguridad de persona queda del lado del operador y del UR5. La única capa
  de seguridad por software es el **watchdog de 15 s** del firmware (§3.1).

## 2. Wiring (UNO + 4 servos, una señal)

| Conexión | De | A |
|---|---|---|
| Señal (los 4 naranjas puenteados) | servo naranja ×4 | **D3 UNO** (una sola línea) |
| V+ servos (los 4 juntos) | servo rojo | **Fuente externa estable: 5–6 V, ≥ 6 A** (ver §2.2) |
| GND servos | servo marrón/café | Fuente externa GND **+ GND del UNO** (tierra común obligatoria) |

**Lo que este montaje NO tiene** (no conectar nada en esos pines):

| No lleva | Consecuencia práctica |
|---|---|
| **LEDs** de estado | Nada que indique "moviendo"/"abierto": el estado se lee por serial (`READY`/`BUSY`/`DONE`) |
| **Botón de E-STOP** | La parada de emergencia la da el UR5 (joystick/teach pendant) y el operador |
| **Sensor HC-SR04** | Sin detección de proximidad en el efector |
| **LCD** | Sin display local: el estado se ve en el dashboard (escritorio o web) |

> Los pines **D2, D4, D5, D6, D7, D8, D10 y D11 quedan libres**. D4–D7 los usa
> **solo** el sketch de calibración (modo `N <1..4>`, un servo por pin) para
> diagnosticar servo por servo; el firmware de producción los deja sin usar.
> Los LEDs de la placa de cada MG996R (el del cable de señal) los pone el
> fabricante: no hay que agregarlos ni conectarlos a nada.

**Reglas de oro del wiring:**
- Los 4 servos JAMÁS se alimentan del pin 5V ni del USB del UNO (el USB limita
  ~500 mA; un solo MG996R en estancamiento ya supera eso).
- Condensador electrolítico **1000 µF** entre +V y GND de la fuente + **fusible
  5 A** en serie con la línea +V por las dudas.
- Tierra común: fuente ↔ UNO ↔ (luego) PC. Sin tierra común el pulso PWM no
  llega bien (síntoma típico: los 4 tiemblan).
- No usar D0/D1 (son RX/TX del USB). D3 elegido a propósito; si se cambia,
  editar `pinSenal` en el sketch y este README.
- No forzar a mano los servos con energizado (se pueden quemar).

### 2.1 ¿Con qué alimentar los servos?

| Opción | Qué es físicamente | Veredicto |
|---|---|---|
| **Fuente de bancada del lab** (recomendada) | Fuente regulada con bornes banana; se fija 6.0 V y se limita la corriente | ✅ Pedir al docente |
| Adaptador industrial 5-6 V | PSU de pared potente (5 V/10 A o similar) | ✅ Si el lab lo presta |
| Batería portátil | 5×AA NiMH (=6 V), o LiPo 2S + módulo UBEC a 6 V | ✅ Útil lejos de la bancada |
| ~~Pin 5V / USB del UNO~~ | — | ❌ Nunca: 500 mA contra picos de 2.5 A por servo |

**Orden de energizado (verificación con multímetro):**
1. Con los servos AÚN desconectados: medir +V real (5.0-6.2 V) y polaridad.
2. Con el multímetro en continuidad: UNO GND ↔ rail GND de la fuente, antes de
   conectar la primera señal.
3. Recién entonces: señales puenteadas a D3 y UNO por USB.

Corriente de referencia MG996R: ~1.2-1.4 A por servo bajo carga a 5 V; hasta
~2.5 A en estancamiento a 6 V.

### 2.2 Cómo configurar la fuente (y qué es que marque "CC")

La fuente de bancada tiene dos modos:

| Indicador | Significado | Qué está pasando |
|---|---|---|
| **CV** (tensión constante) | Normal | Mantiene los voltios fijos y entrega la corriente que pida la carga |
| **CC** (corriente constante) | La carga pidió **más corriente que el límite fijado** | La fuente **baja la tensión** para no pasar del límite |

**Configuración obligatoria de la fuente:**

| Parámetro | Valor | Por qué |
|---|---|---|
| Modo | **CV** (tensión constante) | Es el modo normal para alimentar servos |
| Tensión | **5.0 – 6.0 V** | 6 V da más torque; 5 V es más seguro si el cableado es largo |
| **Límite de corriente** | **6 A** (o el máximo que dé la fuente) | 4 servos en movimiento piden ~5.6 A de pico: con el límite en 5 A la fuente entra en CC en cada arranque |

> El **fusible de 5 A** va en el cableado (protege los cables); el **límite de
> corriente** va en la fuente (protege los servos). No son lo mismo: el fusible
> puede seguir en 5 A aunque la fuente dé 6 A.

**Si al moverse el servo la fuente marca "CC" por un momento**, el motor pide
un pico de corriente (aceleración) que supera el límite configurado. Es el
comportamiento esperado de la fuente, **pero es un aviso**: mientras está en CC
la tensión de salida baja.

Consecuencias posibles si la caída es fuerte:

- Con **1 servo**: si la tensión en sus bornes baja de **4.8 V**, el servo se
  resetea (pierde posición, zumba o da un tirón).
- Con **4 servos**: 4 × 1.4 A ≈ **5.6 A** de pico. Si la fuente no puede dar
  eso, la caída es todavía mayor.
- Si el UNO se alimenta de la **misma** fuente: brownout → el UNO se reinicia →
  el **watchdog abre el gripper a los 15 s y tira la pieza**.

Qué hacer, en orden:

1. **Subir el límite de corriente** de la fuente (es el 90% de los casos).
2. **Medir la tensión en los bornes del servo** mientras se mueve: tiene que
   quedar ≥ 4.8 V. Si la fuente muestra 6 V pero en el servo hay 4.2 V, el
   problema es de cableado/splitter, no de la fuente.
3. **Condensador de 1000 µF** en la salida (ya está en el wiring): amortigua el
   pico y evita que la fuente entre en CC.
4. Si el "CC" **se mantiene mientras el servo sostiene la posición** (no solo
   al arrancar), no es un pico: es **estancamiento contra el tope mecánico**.
   Revisar el pulso (debe estar en 1000–2000 µs) y el montaje.

### 2.3 Referencia medida (rail sano)

Medición en la fuente con **1 solo servo** conectado y en movimiento, con el
setpoint en 6.0 V:

| Lectura | Significado |
|---|---|
| 6.05 V | Pico (la fuente sube un poco con la carga liviana) |
| 5.88 V | Mínimo durante el movimiento |

**Es un rail sano**: el MG996R opera de 4.8 a 6 V, así que incluso en el peor
momento está 1.08 V por encima del mínimo. La variación de 0.17 V (≈3 %) es
normal: el servo pide corriente a ráfagas, la fuente tiene impedancia de salida
finita y el multímetro promedia (y mete su propio ruido) en ~0.2–0.5 s.

> ⚠️ **Un multímetro NO ve el transitorio de "CC"**: promedia medio segundo, así
> que un hundimiento de 50–200 ms le pasa por debajo. Para verlo hace falta un
> osciloscopio, o subir el límite de corriente hasta que el LED "CC" no se
> encienda nunca.


## 3. Protocolo (contrato con el lado Python — `tools/gripper_client.py`)

Baud **115200**, líneas ASCII en MAYÚSCULA terminadas en `\n` (el firmware
tolera `\r`):

| Comando (Python → Arduino) | Respuesta (Arduino → Python) |
|---|---|
| `OPEN` | Arranca apertura suave; `DONE` al terminar. Si ya se mueve: redirige el movimiento a abierto y responde `BUSY` |
| `CLOSE` | Arranca cierre suave; `DONE` al terminar. Si ya se mueve: `BUSY` (no se acepta un cierre a mitad de movimiento) |
| `ZERO` | Cierra despacio hasta el **tope mecánico** (mordazas tocándose) y se queda ahí; `DONE` al asentar. Referencia común para alinear los cuernos. Solo con la boca vacía; se sale con `OPEN`/`STOP` (o el watchdog) |
| `STOP` | Parada suave: redirige a **abierto** desde cualquier estado. `BUSY` si estaba en movimiento (luego llega `DONE`), `DONE` si ya estaba abierto |
| `STATUS` | `READY` (quieto) / `BUSY` (moviéndose). Solo consulta; además mantiene vivo el watchdog |
| `PING` | `PONG`. Latido del heartbeat de la PC. Token propio para que no se confunda con la respuesta a un `OPEN`/`CLOSE` en vuelo |
| (comando desconocido) | `ERROR` |
| (sin comandos válidos por >15 s) | **Watchdog interno: vuelve a `OPEN` solo.** Si la PC se muere mientras sujeta una pieza, no queda aplastándola. Emite `WATCHDOG_OPEN` al disparar y `WATCHDOG_DONE` al terminar |

Movimiento suave: **10 µs de pulso cada 30 ms** (`PASO_US`/`PASO_MS`), unos
~0.7 s de carrera completa. El `DONE` se emite recién después de
`ASENTAMIENTO_MS` (250 ms) con el pulso final sostenido, para que los 4 servos
estén quietos antes de que la PC siga con el ciclo.

### 3.1 El heartbeat (PING) es lo que impide soltar la pieza en el aire

El watchdog del firmware abre el gripper a los **15 s** sin recibir comandos
válidos. El UR5 tarda bastante más que eso en un tramo de transporte
(levantar + cruzar la mesa + descender), y en ese tramo **la PC no manda
ningún comando**: solo espera que el robot termine de mover.

Por eso el cliente Python (`GripperClient`) corre un **hilo de heartbeat**
que manda `PING` cada **1.5 s** (`HEARTBEAT_S`). `PING` es un comando
reconocido, así que reinicia el contador del watchdog. Resultado: la pieza
sigue sujetada durante todo el transporte, y si la PC se muere de verdad el
watchdog la suelta igual (fallo seguro).

> Sin este latido, el firmware abría a mitad del transporte y la pieza caía
> al piso. Es el motivo por el que `PING` existe como comando propio y no
> como un `STATUS` más.

Notas de seguridad del firmware:
- `STATUS`, `PING` y todo comando reconocido resetean el watchdog; la basura
  serial (u otro baud) **no** cuenta como "PC viva".
- El buffer serial de entrada tiene tope de 16 chars: si llega basura sin `\n`
  no crece el heap de 2 KB hasta colgar el UNO.
- `WATCHDOG_OPEN` / `WATCHDOG_DONE` llegan al dashboard como evento de
  seguridad: el ciclo se aborta y el robot frena (la pieza ya no está sujeta).

## 4. Calibración (hacerla con el gripper físico en la mano)

> ⚠️ **Los ángulos de la librería `Servo` no son grados mecánicos del servo.**
> `Servo.h` mapea `write(ángulo)` a un pulso que depende de la **versión** de
> la librería:
>
> | Librería | Mapeo | `write(140)` | `write(170)` |
> |---|---|---|---|
> | **1.3.0** (la instalada en esta máquina) | `map(ángulo, 0, 180, 544, 2400)` | **1987 µs** | 2296 µs |
> | 1.1.8 (la clásica) | `ángulo×10 + 544` | 1944 µs | 2244 µs |
>
> El MG996R de fábrica trabaja en **1000–2000 µs**: con `write(170)` el pulso
> queda **fuera de rango** en cualquier versión. Los 4 servos empujan contra su
> tope mecánico, zumban, calientan y consumen corriente de más. Por eso la
> calibración se hace en **microsegundos**, no en grados.
> (Los números exactos están verificados en `tools/_test_parser_calib.py`.)

### 4.0 Antes de calibrar: por qué "solo algunos se mueven"

Como los 4 servos comparten **una sola señal**, si el pulso llegara a todos,
los 4 intentarían moverse. Que solo se muevan algunos **no es un problema de
señal**: el corte está en la **distribución de alimentación** (+V/GND) de esos
servos concretos, o en un servo muerto. Revisión en orden:

| # | Qué revisar | Cómo | Síntoma |
|---|---|---|---|
| A | **Tierra común** UNO ↔ fuente | Multímetro en continuidad, **apagado**: debe dar ~0 Ω | Servos que tiemblan, o que solo funcionan si movés el cable USB |
| B | **+V y GND de cada servo** | Continuidad de cada rojo→+V y marrón→GND | Un servo se mueve y el de al lado no |
| C | **Corte en el splitter de alimentación** | Si usás protoboard/regleta para repartir +V y GND, es el suspecto #1 | Falta un servo o un lado del gripper |
| D | **Servo muerto** | Probalo **solo**, con sus 3 cables, en otro pin | No se mueve nunca, ni en individual |
| E | **Caída de tensión** | Medí V **en los bornes del servo mientras se mueve** (>4.8 V) | El UNO se reinicia al cerrar → el watchdog abre a los 15 s y tira la pieza |
| F | **Fusible de 5 A** | 4 servos estancados piden ~8–10 A | Se dispara el fusible justo al agarrar |
| G | **Corriente en D3** | Multímetro en serie con el pin | Las placas MG996R llevan un LED **propio** en el cable de señal (lo pone el fabricante, no hay que agregarlo): si cada una consume ~13 mA, 4 en D3 = 60 mA y el pin del UNO sólo da 40 mA |
| H | **Límite de corriente de la fuente** | Ver §2.2: la fuente marca "CC" al moverse | La fuente entra en CC y baja la tensión: el servo se resetea o el UNO hace brownout |

**Reglas de oro del wiring:**
- Los 4 servos JAMÁS se alimentan del pin 5V ni del USB del UNO (el USB limita ~500 mA; un solo MG996R en estancamiento ya supera eso).
- Condensador electrolítico **1000 µF** entre +V y GND de la fuente + **fusible 5 A** en serie con la línea +V.
- Tierra común: fuente ↔ UNO ↔ (luego) PC. Sin tierra común el pulso PWM no llega bien (síntoma típico: los 4 tiemblan).
- No usar D0/D1 (son RX/TX del USB). D3 elegido a propósito; si se cambia, editar `pinSenal` en el sketch y este README.
- No forzar a mano los servos con energizado (se pueden quemar).

### 4.1 Sketch de calibración (herramienta de laboratorio)

`firmware/calibracion_1signal/calibracion_1signal.ino` es un sketch **auxiliar**
para medir. **No es el firmware de producción** (no tiene watchdog ni heartbeat
y no habla con el UR5). While esté cargado, el gripper está fuera de servicio:
no corras el dashboard y no mandes `OPEN`/`CLOSE`.

```bash
arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/calibracion_1signal
arduino-cli upload -p COM3 --fqbn arduino:avr:uno gripper/firmware/calibracion_1signal
```

Se maneja por serial a 115200 (Serial Monitor con "carro de retorno"):

| Comando | Qué hace |
|---|---|
| `H` | Ayuda |
| `P <us>` | Pulso objetivo en µs (900–2100). Avisa si sale del rango de fábrica |
| `A <deg>` | Ángulo objetivo, **mapeado a 1000–2000 µs** (no a 544–2400) |
| `G` | Ir YA al objetivo (golpe seco: cuidado) |
| `X` | Reanudar la rampa hacia el objetivo |
| `T` | Parar y sostener la posición |
| `OFF` / `ON` | Soltar (detach) / reconectar (attach) los servos |
| `TAP <us> <ms> <pausa>` | Pulsos cortos: para encontrar topes sin calentar |
| `SW <ini> <fin>` | Barrido automático entre dos pulsos |
| `M` | Los 4 servos unificados en D3 (como producción) |
| `N <1..4>` | **Modo diagnóstico**: mueve SOLO ese servo, por su pin propio (D4/D5/D6/D7) |
| `R` | Informe del estado |

La rampa es de **10 µs cada 20 ms** (500 µs/s): nunca golpea los topes.

**Diagnóstico servo por servo** (tu caso de "solo algunos se mueven"):

1. Desconectá las 4 naranjas del puente de D3.
2. Conectá **una naranja por vez** a D4, D5, D6, D7 (la alimentación queda
   como estaba: así solo se aísla la señal).
3. `N 1` → `P 1500` → el servo 1 ¿se mueve? Después `N 2`, `N 3`, `N 4`.
4. El que no se mueva con buena alimentación tiene el cable de señal o el
   servo averiado. Los que se mueven bien pero **a distinta posición** tienen
   el cuerno desalineado.

### 4.2 Procedimiento de medición

**Fase 0 — Verificación sin mover servos** (apagado): +V 5.0–6.2 V y polaridad;
continuidad UNO↔fuente GND; continuidad D3→cada naranja; continuidad de cada
rojo→+V y marrón→GND.

**Fase 0 bis — Configuración de la fuente** (encendida, servos todavía
desenchufados): la fuente debe quedar en **CV**, con **5.0–6.0 V** y **límite
de corriente ≥ 6 A** (§2.2). Sin mover servos no debería entrar nunca en "CC".
Con **un solo servo** enchufado y en movimiento, la fuente puede pisar el
límite un instante (pico de arranque): es aceptable **si la tensión en los
bornes del servo no baja de 4.8 V** (referencia medida: 5.88–6.05 V, §2.3).
Si baja, o si el "CC" dura más que el arranque, hay que corregir el límite de
corriente o el cableado **antes** de seguir.

**Fase 1 — Un servo a la vez.** Con `N <n>` y `P`, recorré el rango de 1000 a
2100 µs y anotá dónde empieza a zumbar en cada extremo. Después pasá a `M`
(los 4 juntos) y repetí: si el conjunto zumba antes que los individuales, el
problema es mecánico (topes), no eléctrico.

**Fase 2 — Mapa de aberturas.** Los 4 en `M`. Para un barrido de pulsos
(cada 50 µs de 1000 a 2100) medí con calibre la **apertura entre las puntas
TPU**. Anotá: *pulso µs ↔ abertura mm ↔ cuál mordaza toca primero*. Salen los
dos candidatos: apertura máxima simétrica y cierre a 50 mm.

**Fase 3 — `cerrado` sin aplastar.** Poné la pieza real de 50 mm (o un
cilindro Ø50). Cerrá hasta el contacto y **retrocedé 20–40 µs**. Criterio de
aceptación:
- la pieza se levanta y se desliza con la punta del dedo,
- **el servo NO zumba** (si zumba, apretás de más: corriente de estancamiento = calor),
- la fuente no salta su límite de corriente.

Prueba de resistencia: **60 s con la pieza apretada**. ¿Zumba? ¿La fuente se
sobrecarga? ¿La pieza queda marcada?

**Fase 4 — `abierto`.** Abrí al máximo y verificá que **ni las mordazas ni el
marco toquen un tope mecánico** y que los servos no zumben. Si el tope
mecánico limita antes que el pulso, el firmware no lo arregla.

**Fase 5 — Registro.** Anotá los valores finales acá como historial.

Calibrá **con las puntas TPU montadas** (comprimen y cambian la abertura
efectiva). Durante la Fase 3, poné la fuente en modo amps para leer la
corriente real.

### 4.3 Valores actuales en el sketch de producción

```cpp
const int US_ABIERTO = 1900;   // boca abierta
const int US_CERRADO = 1680;   // cierre para pieza de 50 mm
```

El sketch trabaja en **microsegundos** (`writeMicroseconds()`), no en grados: el
mapeo de `write()` cambia con la versión de la librería Servo (1.3.0: 544–2400 µs;
1.1.8: ángulo×10+544), así que "grados" no es portable y dos máquinas pueden
generar pulsos distintos con el mismo número. En µs, el pulso es el mismo acá y
en cualquier otra máquina.

| Valor | Pulso | Margen al tope de fábrica (2000 µs) | Estado |
|---|---|---|---|
| `US_ABIERTO` | 1900 µs | 100 µs (≈10 %) | ok: ningún servo satura antes que otro (Fase 4) |
| `US_CERRADO` | 1680 µs | 320 µs | ok, **sin calibrar** con la pieza real (Fase 3) |

Historia de calibración:
- `cerrado = 110` → 1678 µs → **`US_CERRADO = 1680`**: equivalente, sigue sin
  calibrar con la pieza real.
- `abierto = 170` → **fuera de rango de fábrica** (2296 µs con Servo 1.3.0,
  2244 µs con 1.1.8): los 4 servos empujaban contra el tope mecánico, zumbaban
  y consumían corriente de más.
- `abierto = 140` → 1987 µs: dentro de rango, pero a **13 µs** del tope. Con la
  tolerancia de potenciómetro de cada unidad (±10 %), algunos servos ya estaban
  saturados y otros no: la boca abierta quedaba despareja.
  **Corregido a `US_ABIERTO = 1900`.**
- Pendiente: medir la abertura real con el sketch de calibración y ajustar
  `US_CERRADO` con la pieza real.

> Si al abrir al máximo los servos siguen empujando contra el tope mecánico,
> el problema es el **montaje** (la punta TPU o el marco chocan), no el pulso:
> bajar `US_ABIERTO` solo oculta el síntoma.

### 4.4 "Los 4 servos van desparejos": qué puede y qué no puede el firmware

Con los 4 naranjas en D3 **sale un solo pulso**: los cuatro reciben el mismo
ancho en el mismo frame, así que el firmware no puede moverlos a distinta
velocidad ni desincronizarlos. Si igual van desparejos, el reparto está en otro
lado. En orden de frecuencia:

| # | Causa | Cómo se nota | Qué hacer |
|---|---|---|---|
| A | **Cuernos desalineados** al montar | Aun quietos y con el mismo pulso, las mordazas no quedan simétricas | Comando `ZERO` (tope mecánico), aflojar los cuernos y volver a ajustarlos con las mordazas tocándose; después `OPEN` |
| B | **Caída de tensión** (picos de 4 servos) | A mitad del movimiento uno se reinicia, pega un tirón o se queda atrás | Fuente externa 5–6 V ≥ 6 A, 1000 µF, tierra común (§2.2, filas E–H de §4.0) |
| C | **Señal repartida a 4 entradas** | Arranques desparejos o temblor, peor con cables largos | Cada placa MG996R carga la línea (~13 mA): 4 juntas pueden pasar los 40 mA del pin. Buffear D3 (74HCT245) o acortar el puente (§4.0, fila G) |
| D | **Pulso contra el tope de fábrica** | Al abrir al máximo, unas mordazas abren más que otras y zumban | Ya corregido: `US_ABIERTO = 1900` µs (§4.3) |
| E | **Velocidad de respuesta distinta** con carga | Durante la carrera se ve una mordaza atrás de la otra; al final llegan las dos | Es transitorio: el `DONE` ahora espera `ASENTAMIENTO_MS` con el pulso final sostenido, así la PC no sigue con una mordaza en camino |

El firmware cubre D y E; A–C son montaje/eléctricos. Si después de eso siguen
desparejos, probar servo por servo con el sketch de calibración (`N <1..4>`)
para ver cuál está fuera de rango.

## 5. Probar todo SIN robot y sin piezas

1. `pip install pyserial`
2. Conectar el UNO por USB y cerrar el Serial Monitor del Arduino IDE (así el
   puerto queda libre).
3. `python gripper/tools/probar_gripper.py COM3` (Windows: COM3/COM4; Linux:
   `/dev/ttyUSB0` o `/dev/ttyACM0`). Trae heartbeat propio, `ZERO` y ciclo
   automático; el `test_serial.py` de siempre sigue sirviendo igual.
4. Pulsar `o` (OPEN) → debe abrir y ver `DONE` (0.25 s si ya estaba abierto:
   es el asentamiento).
5. Pulsar `c` (CLOSE) → moverse ~0.9 s y `DONE`.
6. Pulsar `t` (STATUS) durante el movimiento → `BUSY`.
7. Pulsar `z` (ZERO) con la boca vacía → cierra hasta que las mordazas se
   tocan, se queda ahí y responde `DONE`. Es la referencia para alinear los
   cuernos: aflojar los 4, dejar que toquen, ajustar y después `OPEN`.
8. Dejar 15 s sin escribir nada → debe volver a `OPEN` solo (watchdog), con el
   aviso `WATCHDOG_OPEN` y luego `WATCHDOG_DONE`.

> Ojo con el paso 7 si usás el probador: `test_serial.py` usa
> `GripperClient`, que ya tiene el heartbeat `PING` corriendo. El watchdog
> **no** se disparará nunca mientras el cliente esté conectado, porque el
> latido lo mantiene vivo. Para probar el watchdog de verdad: cerrá el
> probador (o desconectá el cable de datos) y esperá 15 s con la pieza
> cerrada — el firmware tiene que abrir solo.

Si un servo no se mueve → revisar el puente de señal a D3 y su conexión. Si
todos tiemblan → falta tierra común o el condensador.

### 5.1 Cargar el sketch sin Arduino IDE (arduino-cli)

Con el UNO conectado por USB, no hace falta instalar el IDE:

```bash
arduino-cli lib install Servo          # una sola vez
arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
arduino-cli board list                 # muestra el puerto, ej. COM3
arduino-cli upload -p COM3 --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
```

La carpeta del sketch debe llamarse igual que el `.ino` (por eso es
`firmware/gripper_1signal/gripper_1signal.ino` y no un archivo suelto).

### 5.2 Verificación rápida desde Python (sin mover servos)

```python
import serial, time
ser = serial.Serial("COM3", 115200, timeout=0.3)
time.sleep(2.0)            # reset por DTR
ser.reset_input_buffer()   # descarta el "READY" del arranque
ser.write(b"STATUS\n"); time.sleep(0.3)
print(ser.readline())      # b"READY\r\n"
```

Si el puerto abre pero no responde `STATUS`: ¿firmware cargado a 115200? ¿es el
sketch correcto? Para confirmar cuál está grabado, enviar `PING`: **`PONG` =
`gripper_1signal`** (la variante vieja de 4 servos no conoce `PING` ni `ZERO` y
responde `ERROR`). El `CLOSE` completo del sketch actual tarda ~0.9 s (rampa de
~0.66 s + `ASENTAMIENTO_MS`).

## 6. Logs del Arduino integrados a las corridas del proyecto

El `GripperClient` puede registrar **todo el tráfico serie** (comandos
enviados y líneas recibidas, con timestamp del PC) bajo el logger
`GripperSerial`. Se activa con `GRIPPER_LOG_SERIE=1` en `.env`; con
`LOG_ARCHIVO=1` las líneas quedan dentro de `logs/corrida_FECHA_HORA.log`
además de la consola.

No hace falta (ni se puede) un lector externo: el puerto COM es excluyente y
cada apertura resetea el UNO, así que las líneas solo se capturan desde el
mismo proceso que controla el gripper.

## 7. Entregables de este módulo (para el final)

- `firmware/gripper_1signal/gripper_1signal.ino` (sketch final con ángulos calibrados)
- `firmware/calibracion_1signal/calibracion_1signal.ino` (sketch de laboratorio)
- `tools/test_serial.py` (tester interactivo)
- `tools/gripper_client.py` (cliente Python para integración con el orquestador)
- `tools/_test_cliente_fake.py` (pruebas automáticas contra un Arduino simulado)
- `tools/_test_modos_calib.py` y `tools/_test_parser_calib.py` (pruebas del sketch
  de calibración: pulsos reales que salen por el pin y parseo de comandos)
- Este README con: valores de calibración finales, modelo de servos, **configuración
  de la fuente (V y límite de corriente)**, fotos del montaje

> El sketch de calibración **no** se entrega cargado: es herramienta de banco.
> En la/demo va el de producción (`gripper_1signal`).

### 7.1 Pruebas automáticas (sin hardware)

Las pruebas del cliente Python corren contra un **Arduino simulado en memoria**
que implementa el mismo protocolo que el firmware real:

```bash
python gripper/tools/_test_cliente_fake.py
```

Verifica: el heartbeat mantiene vivo el watchdog durante un "transporte"
largo, `STOP` redirige a abierto desde cualquier estado, el evento de
watchdog llega al dashboard, el firmware abre solo si la PC muere, y la
basura serial no cuenta como "PC viva".

## 8. Ficha técnica de piezas (medidas ideales)

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
| Base | Plana, sin chaflán excesivo que resbale | — |

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
