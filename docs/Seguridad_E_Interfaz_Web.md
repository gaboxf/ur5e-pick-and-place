# Seguridad Física + Interfaz Web de Monitoreo

Extensión del Desafío 2 (Pick & Place UR5) con dos capacidades:

1. **Interfaz web** para monitorear y operar la celda desde cualquier dispositivo de la red.
2. **Capas de seguridad del montaje actual**: E-STOP del UR5e, parada por
   software del dashboard y watchdog de 15 s del firmware del gripper.

> **Hardware actual:** `gripper_1signal` (4 servos, una sola señal, sin LEDs,
> sin E-STOP en el Arduino, sin HC-SR04, sin LCD). La seguridad de persona
> depende del E-STOP del UR5e y del operador; el Arduino no tiene parada física.
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
cd <carpeta-del-proyecto>
.venv/bin/python main.py
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

## 2. Seguridad

### 2.1 Capas activas en el montaje actual

| Capa | Qué hace | Dónde |
|---|---|---|
| **E-STOP del UR5e** | Parada física certificada del robot | Teach pendant / botón del robot |
| **Parada por software** | Detiene el ciclo y bloquea comandos hasta el reset | Dashboard web (`detener`) / escritorio (`[e]`) |
| **Watchdog del gripper (15 s)** | Si la PC deja de mandar comandos, el firmware abre el gripper y suelta la pieza | `gripper_1signal` |
| **Zona de trabajo y radio mínimo** | Rechaza coordenadas fuera de los límites calibrados | `core/robot_controller.py` |
| **Verificación visual de agarre** | Confirma que la pieza salió de la mesa antes de transportar | `core/robot_controller.py` |

> El Arduino **no** tiene botón de E-STOP ni sensor de proximidad. La parada de
> emergencia válida es la del **UR5e**; el dashboard solo detiene el ciclo del
> proyecto y no habilita el acceso humano a la zona de movimiento.

### 2.2 Protocolo serial

| Dirección | Línea | Significado |
|---|---|---|
| Arduino → PC | `READY` | quieto / listo |
| Arduino → PC | `BUSY` | servos en movimiento |
| Arduino → PC | `DONE` | movimiento completado |
| Arduino → PC | `ERROR` | comando inválido |
| Arduino → PC | `WATCHDOG_OPEN` | el firmware se abrió solo (la PC dejó de mandar comandos) |
| Arduino → PC | `WATCHDOG_DONE` | terminó esa apertura por watchdog |

El hilo lector de `GripperClient` separa respuestas de comandos (cola de líneas)
y eventos de seguridad (cola de eventos), de modo que estos últimos nunca se
pierden aunque no haya un comando en curso.

### 2.3 Handshake determinista UR5 (RTDE) y Confirmación de agarre en lazo cerrado

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
| `gripper/firmware/gripper_1signal/gripper_1signal.ino` | Firmware en uso: 4 servos con una sola señal, watchdog de 15 s, sin LEDs/E-STOP/HC-SR04 |
| `gripper/tools/gripper_client.py` | Hilo lector serial, colas de respuestas/eventos, `get_evento()`, `get_estado_completo()` |
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

### Con URSim

1. `./scripts/run_ursim.sh`
2. `.venv/bin/python -m core.dashboard_web`
3. Ejecutar un ciclo y, durante el movimiento, usar la **parada por software**
   (botón de la web o tecla `[e]` del dashboard de escritorio): el movimiento
   debe cortarse y el estado pasar a `PARADA_SEGURIDAD`.
4. Pulsar "Reiniciar parada" y verificar que se puede volver a operar.

> El E-STOP físico del gripper no existe en el montaje actual: la parada
> certificada es la del **UR5e** (teach pendant).
