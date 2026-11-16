# Seguridad Física + Interfaz Web de Monitoreo

Extensión del Desafío 2 (Pick & Place UR5) con dos capacidades nuevas:

1. **Interfaz web** para monitorear y operar la celda desde cualquier dispositivo de la red.
2. **Capa de seguridad física**: botón de parada de emergencia (E-STOP) y sensor
   ultrasónico de proximidad montados en el gripper, con parada automática del UR5.

> ⚠️ **Estado del hardware en la versión actual:** el gripper que se usa es
> `gripper_1signal` (**4 servos, una sola señal, sin LEDs, sin E-STOP, sin
> HC-SR04, sin LCD**). El E-STOP y el sensor ultrasónico **no están montados**:
> el esquema de §2 describe el firmware histórico `gripper_4servos`, que ya no
> se graba en el UNO. El código de Python **ya no maneja** `EMERGENCY` ni
> `PROXIMITY_STOP` (se eliminaron por ser código muerto: nunca llegaban).
> La seguridad real del montaje actual es: E-STOP del UR5e + parada por
> software del dashboard + watchdog de 15 s del firmware del gripper + operador.
> Ver `gripper/README.md` §1–§2 y `docs/Arranque.md` §7.

---

## 1. Interfaz web (`core/dashboard_web.py`)

### Qué hace

Servidor Flask independiente que comparte el mismo `RobotController` (visión +
gripper + UR5) y expone:

| Ruta | Método | Descripción |
|---|---|---|
| `/` | GET | Página de monitoreo (HTML + CSS + JS, sin CDN) |
| `/video` | GET | Stream MJPEG en vivo con la detección dibujada |
| `/api/state` | GET | Estado completo en JSON |
| `/stream` | GET | Server-Sent Events (estado cada 0.5 s) |
| `/api/command` | POST | Comandos remotos |

Comandos aceptados por `/api/command`:

```json
{"accion": "ciclo"}                  // ciclo completo de clasificación
{"accion": "ciclo", "categoria": "A"} // solo pieza roja
{"accion": "ciclo", "categoria": "B"} // solo pieza azul
{"accion": "abrir"}                  // abrir gripper
{"accion": "cerrar"}                 // cerrar gripper
{"accion": "home"}                   // posición segura
{"accion": "detener"}                // parada de seguridad
{"accion": "reset"}                  // reiniciar una parada
```

### Cómo ejecutarlo

```bash
cd /home/lucio/datos/UPRO/UPrO/Desafio_Final
.venv/bin/python -m core.dashboard_web
```

Abrir en el navegador:

- En la misma PC: <http://localhost:5000>
- Desde el teléfono u otra PC de la red: `http://<IP-de-tu-PC>:5000`

> El servidor escucha en `0.0.0.0:5000`, por lo que es accesible desde la red
> local. Si el firewall de Linux lo bloquea: `sudo ufw allow 5000/tcp`.

### Qué muestra

- Video en vivo con el HUD de estado y la detección (categoría + coordenadas UR5).
- Estado de cámara, gripper y robot (OK / SIMULADO / MOCK).
- Detección actual: categoría, X/Y en metros, confianza y motivo de fallo.
- Contadores de producción: ciclos OK, errores, piezas por categoría.
- Alertas de seguridad (parada de software, watchdog del gripper) y uptime.

### Notas técnicas

- El video se sirve como **MJPEG** (`multipart/x-mixed-replace`), sin plugins.
- El estado en tiempo real usa **SSE** (`/stream`), nativo del navegador; no
  requiere librerías JS externas ni conexión a internet.
- El bucle de captura corre en un hilo aparte; Flask atiende en hilos propios.
- Si no hay cámara, se muestra un frame de "SIN CAMARA" y el resto sigue vivo.

---

## 2. Seguridad física

> Recordatorio: lo de §2.1 y §2.2 describe el **firmware histórico**
> `gripper_4servos`. En el hardware actual **no se montan** ni el pulsador ni
> el sensor. Se conserva la documentación como referencia histórica: el código
> de Python **ya no** soporta esos dos eventos.

### 2.1 Botón de parada de emergencia (E-STOP) — no montado

**Conexión (histórica):** pulsador normalmente abierto entre **D8** y `GND`.
El firmware usaba `INPUT_PULLUP`, así que no hacía falta resistencia externa.

```
UNO D8 ──────[ pulsador NA ]────── GND
```

**Comportamiento que tenía (firmware histórico `gripper_4servos`):**

1. Al pulsar, el Arduino detenía los servos inmediatamente y enviaba `EMERGENCY\n`.
2. `GripperClient` (hilo lector) lo encolaba como evento `emergencia_fisica`.
3. El dashboard (escritorio o web) ponía la parada activa, llamaba a
   `UR5Client.stop()` (RTDE `stopJ`) y bloqueaba nuevos ciclos.
4. Al soltar el botón, el Arduino enviaba `READY\n`; la parada se reiniciaba desde
   la interfaz (botón "Reiniciar parada" en la web o tecla `[r]` en el escritorio).

> **Hoy ese camino ya no existe en el código**: `GripperClient` no reconoce
> `EMERGENCY` ni `PROXIMITY_STOP`, y el dashboard solo tiene la **parada por
> software** (`parada_activa`), que se activa con la tecla `[e]`, el botón
> "detener" de la web o el comando de voz "Parar".

### 2.2 Sensor ultrasónico HC-SR04 — no montado

**Conexión (histórica):**

| HC-SR04 | Arduino UNO |
|---|---|
| VCC | 5 V |
| GND | GND |
| TRIG | D10 |
| ECHO | D11 |

> El UNO trabaja a 5 V, así que el ECHO se conecta directo. Si se usara una placa
> de 3.3 V (ESP32, Raspberry), habría que poner un divisor de tensión en ECHO.

**Comportamiento que tenía (firmware histórico `gripper_4servos`):**

- Medía cada 100 ms. Si detectaba un objeto a **menos de 15 cm**, enviaba
  `PROXIMITY_STOP\n` **una sola vez** (anti-spam) y se rearmaba cuando el objeto
  se alejaba más de 20 cm.
- El Python lo trataba como parada de seguridad igual que el E-stop: frenaba el UR5
  y bloqueaba ciclos hasta reiniciar.
- Estaba pensado para montarse mirando hacia adelante en el gripper: si una mano
  se acercaba al efector durante el movimiento, la celda se detenía.

### 2.3 Protocolo serial ampliado

| Dirección | Línea | Significado |
|---|---|---|
| Arduino → PC | `READY` | quieto / listo |
| Arduino → PC | `BUSY` | servos en movimiento |
| Arduino → PC | `DONE` | movimiento completado |
| Arduino → PC | `ERROR` | comando inválido |
| Arduino → PC | `WATCHDOG_OPEN` | el firmware se abrió solo (la PC dejó de mandar comandos) |
| Arduino → PC | `WATCHDOG_DONE` | terminó esa apertura por watchdog |
| ~~Arduino → PC~~ | ~~`EMERGENCY`~~ | **eliminado**: era del firmware histórico; `GripperClient` ya no lo reconoce |
| ~~Arduino → PC~~ | ~~`PROXIMITY_STOP`~~ | **eliminado**: era del firmware histórico; `GripperClient` ya no lo reconoce |

El hilo lector de `GripperClient` separa respuestas de comandos (cola de líneas)
y eventos de seguridad (cola de eventos), de modo que estos últimos nunca se
pierden aunque no haya un comando en curso.

### 2.4 Handshake determinista UR5 (RTDE) y Confirmación de agarre en lazo cerrado

Como el gripper no dispone de sensores táctiles físicos de fuerza en los dedos TPU, la celda implementa dos mecanismos avanzados de sincronización y validación por software:

1. **Handshake determinista por RTDE (`core/ur5_client.py`):**
   - Tras cada comando `moveL`, el cliente valida por la interfaz `rtde_receive` que el error euclidiano entre la pose real del TCP y el objetivo sea $\le 3\text{ mm}$ (`ROBOT_TOL_M`).
   - Se valida además que el robot esté detenido (`is_steady()`, velocidad residual nula) antes de enviar la orden de cierre o apertura al Arduino.

2. **Confirmación visual de agarre (*Closed-Loop Vision Confirmation*):**
   - El UR5 desciende a cota de pick y comanda el cierre, esperando la confirmación `DONE` del Arduino.
   - Sube a `z_seguro` y se desplaza ligeramente ($8\text{ cm}$) hacia el destino para despejar la vista cenital de la cámara.
   - El sistema pasa transitoriamente al estado **`VERIFICANDO`** y procesa un nuevo frame de la mesa.
   - Si la pieza **sigue presente en la mesa** (menos de $50\text{ mm}$ del punto de pick original):
     - El agarre falló (resbaló o no cerró a tiempo).
     - Se abre el gripper, el robot regresa a Home y se aborta el ciclo de forma segura sin trasladar aire.
   - Si la mesa está libre:
     - Se confirma el agarre (`Confirmación visual OK`) y continúa el transporte al área de depósito.

> ⚠️ **Importante (seguridad):** esta capa es un complemento de software. **No
> reemplaza** el sistema de seguridad certificado del UR (parada de emergencia
> hardware, protecciones, límites de seguridad). La consigna del Megadesafío lo
> exige explícitamente: una pausa implementada solo por software no es una
> medida de seguridad suficiente. La parada por software del dashboard sirve
> para detener el ciclo del proyecto, no para habilitar el acceso humano a la
> zona peligrosa.

---

## 3. Archivos modificados / nuevos

| Archivo | Cambio |
|---|---|
| `gripper/firmware/gripper_4servos/gripper_4servos.ino` | **Histórico, ya no se graba.** Tenía E-STOP (D8), HC-SR04 (D10/D11), eventos `EMERGENCY` / `PROXIMITY_STOP`, anti-spam. El firmware en uso es `gripper/firmware/gripper_1signal/` (sin LEDs, sin E-STOP, sin sensor) |
| `gripper/tools/gripper_client.py` | Hilo lector serial, colas de respuestas/eventos, `get_evento()`, `get_estado_completo()`. **Ya no reconoce `EMERGENCY` ni `PROXIMITY_STOP`** (eventos del firmware histórico) |
| `core/ur5_client.py` | `stop()` con `stopJ` en real, handshake determinista de tolerancia milimétrica (`ROBOT_TOL_M`), verificación de quietud (`is_steady`) y bandera de aborto |
| `core/robot_controller.py` | `detener()` delega en `UR5Client.stop()`; ciclo con validación de agarre por visión en lazo cerrado y confirmación del firmware |
| `core/dashboard.py` | Atiende eventos de seguridad, bloquea ciclos, teclas `[e]` (paro) y `[r]` (reset) |
| `core/dashboard_web.py` | **Servidor Flask**: MJPEG, SSE, API de comandos y orquestación de ciclo con estado `VERIFICANDO` y validación visual de agarre |
| `templates/dashboard.html` | **Nuevo**: interfaz web de monitoreo y control |

---

## 4. Pruebas rápidas

### Sin hardware (modo simulación)

```bash
.venv/bin/python -m core.dashboard_web
# abrir http://localhost:5000
```

- El estado debe mostrar cámara `SIN CÁMARA`, gripper `SIMULADO`, robot `MOCK`.
- Los botones responden y actualizan la interfaz.

### Con Arduino (sin robot)

1. Cargar el sketch: `arduino-cli compile/upload ...` (`gripper_1signal`).
2. Conectar el Arduino y verificar el puerto en `vision/config.json`.
3. `OPEN` / `CLOSE` → la interfaz muestra el estado del gripper.
4. Cortar el cable de datos (o matar la PC) y esperar 15 s con la pieza cerrada:
   debe aparecer `WATCHDOG_OPEN` y luego `WATCHDOG_DONE` en el log.

> Los pasos de E-STOP y HC-SR04 que había antes en esta sección (pulsar el
> botón, acercar la mano al sensor) **no aplican** al hardware actual: no hay
> botón ni sensor montados. Solo servirían con el sketch histórico
> `gripper_4servos`.

### Con URSim

1. `./scripts/run_ursim.sh`
2. `.venv/bin/python -m core.dashboard_web`
3. Ejecutar un ciclo y, durante el movimiento, usar la **parada por software**
   (botón de la web o tecla `[e]` del dashboard de escritorio): el movimiento
   debe cortarse y el estado pasar a `PARADA_SEGURIDAD`.
4. Pulsar "Reiniciar parada" y verificar que se puede volver a operar.

> El E-STOP físico del gripper no existe en el montaje actual: la parada
> certificata es la del **UR5e** (teach pendant).
