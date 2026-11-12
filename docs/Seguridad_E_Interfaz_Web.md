# Seguridad Física + Interfaz Web de Monitoreo

Extensión del Desafío 2 (Pick & Place UR5) con dos capacidades nuevas:

1. **Interfaz web** para monitorear y operar la celda desde cualquier dispositivo de la red.
2. **Capa de seguridad física**: botón de parada de emergencia (E-STOP) y sensor
   ultrasónico de proximidad montados en el gripper, con parada automática del UR5.

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
- Alertas de seguridad (E-stop / proximidad) y uptime.

### Notas técnicas

- El video se sirve como **MJPEG** (`multipart/x-mixed-replace`), sin plugins.
- El estado en tiempo real usa **SSE** (`/stream`), nativo del navegador; no
  requiere librerías JS externas ni conexión a internet.
- El bucle de captura corre en un hilo aparte; Flask atiende en hilos propios.
- Si no hay cámara, se muestra un frame de "SIN CAMARA" y el resto sigue vivo.

---

## 2. Seguridad física

### 2.1 Botón de parada de emergencia (E-STOP)

**Conexión:** pulsador normalmente abierto entre **D8** y **GND**.
El firmware usa `INPUT_PULLUP`, así que no hace falta resistencia externa.

```
UNO D8 ──────[ pulsador NA ]────── GND
```

**Comportamiento:**

1. Al pulsar, el Arduino detiene los servos inmediatamente y envía `EMERGENCY\n`.
2. `GripperClient` (hilo lector) lo encola como evento `emergencia_fisica`.
3. El dashboard (escritorio o web) pone `estop_activo = True`, llama a
   `UR5Client.stop()` (RTDE `stopJ`) y bloquea nuevos ciclos.
4. Al soltar el botón, el Arduino envía `READY\n`; la parada se reinicia desde
   la interfaz (`botón "Reiniciar parada"` en la web o tecla `[r]` en el escritorio).

### 2.2 Sensor ultrasónico HC-SR04

**Conexión:**

| HC-SR04 | Arduino UNO |
|---|---|
| VCC | 5 V |
| GND | GND |
| TRIG | D10 |
| ECHO | D11 |

> El UNO trabaja a 5 V, así que el ECHO se conecta directo. Si se usara una placa
> de 3.3 V (ESP32, Raspberry), habría que poner un divisor de tensión en ECHO.

**Comportamiento:**

- Mide cada 100 ms. Si detecta un objeto a **menos de 15 cm**, envía
  `PROXIMITY_STOP\n` **una sola vez** (anti-spam) y se rearma cuando el objeto
  se aleja más de 20 cm.
- El Python lo trata como parada de seguridad igual que el E-stop: frena el UR5
  y bloquea ciclos hasta reiniciar.
- Está pensado para montarse mirando hacia adelante en el gripper: si una mano
  se acerca al efector durante el movimiento, la celda se detiene.

### 2.3 Protocolo serial ampliado

| Dirección | Línea | Significado |
|---|---|---|
| Arduino → PC | `READY` | quieto / listo |
| Arduino → PC | `BUSY` | servos en movimiento |
| Arduino → PC | `DONE` | movimiento completado |
| Arduino → PC | `ERROR` | comando inválido |
| Arduino → PC | `EMERGENCY` | **botón E-STOP pulsado** |
| Arduino → PC | `PROXIMITY_STOP` | **objeto a < 15 cm** |

El hilo lector de `GripperClient` separa respuestas de comandos (cola de líneas)
y eventos de seguridad (cola de eventos), de modo que estos últimos nunca se
pierden aunque no haya un comando en curso.

> ⚠️ **Importante (seguridad):** esta capa es un complemento de software. **No
> reemplaza** el sistema de seguridad certificado del UR (parada de emergencia
> hardware, protecciones, límites de seguridad). La consigna del Megadesafío lo
> exige explícitamente: una pausa implementada solo por software no es una
> medida de seguridad suficiente. El E-STOP del gripper sirve para detener el
> ciclo del proyecto, no para habilitar el acceso humano a la zona peligrosa.

---

## 3. Archivos modificados / nuevos

| Archivo | Cambio |
|---|---|
| `gripper/firmware/gripper_4servos/gripper_4servos.ino` | E-STOP (D8), HC-SR04 (D10/D11), eventos `EMERGENCY` / `PROXIMITY_STOP`, anti-spam |
| `gripper/tools/gripper_client.py` | Hilo lector serial, colas de respuestas/eventos, `get_evento()`, `get_estado_completo()` |
| `core/ur5_client.py` | `stop()` con `stopJ` en real y bandera de aborto en simulación |
| `core/robot_controller.py` | `detener()` delega en `UR5Client.stop()` |
| `core/dashboard.py` | Atiende eventos de seguridad, bloquea ciclos, teclas `[e]` (paro) y `[r]` (reset) |
| `core/dashboard_web.py` | **Nuevo**: servidor Flask, MJPEG, SSE y API de comandos |
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

1. Cargar el sketch: `arduino-cli compile/upload ...`
2. Conectar el Arduino y verificar el puerto en `vision/config.json`.
3. Pulsar el botón: en el log debe aparecer `Evento de seguridad del gripper: EMERGENCY`
   y la interfaz debe mostrar la alerta.
4. Acercar la mano al HC-SR04 (< 15 cm): debe aparecer `PROXIMITY_STOP`.

### Con URSim

1. `./scripts/run_ursim.sh`
2. `.venv/bin/python -m core.dashboard_web`
3. Ejecutar un ciclo y, durante el movimiento, pulsar el E-STOP: el movimiento
   debe cortarse y el estado pasar a `EMERGENCIA_E_STOP`.
4. Pulsar "Reiniciar parada" y verificar que se puede volver a operar.
