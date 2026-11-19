# Arranque — Desafío 2: Pick & Place UR5

Guía de puesta en marcha de la celda: cámara + Arduino (gripper) + robot UR5e,
con control por voz e interfaz web de monitoreo.

> **Sistema:** notebook (cerebro central) + cámara USB cenital + Arduino UNO
> (gripper de 4 servos MG996R) + UR5e por Ethernet (RTDE, puerto 30004).

---

## 0. Orden de conexión (resumen)

1. Cámara USB → notebook. Cenital, fija (cinta/abrazadera). No moverla después
   de calibrar la homografía.
2. Arduino → USB de la notebook (con el sketch `gripper_1signal.ino` cargado).
3. Servos MG996R → **señal puenteada a D3** (las 4 naranjas juntas) + fuente
   externa 5–6 V con **tierra común** con el Arduino y **límite de corriente
   ≥ 5 A** (ver `gripper/README.md` §2 y §2.2).
4. UR5e → **cable Ethernet al Control Box** (no al teach pendant).
5. Verificar `vision/config.json` (`gripper_port`) y `.env` (`UR5_IP`).
6. Encender el robot y liberar frenos (`power on` / `brake release`).
7. Ejecutar **uno** de los dashboards (escritorio o web).

> ⚠️ La palabra del día: **no** conectar los servos al pin 5V del Arduino.
> Usan fuente externa con tierra común. Ver `gripper/README.md`.

> ℹ️ El montaje **no tiene** LEDs, botón de E-STOP, sensor HC-SR04 ni LCD: son
> 4 servos y nada más. La parada de emergencia la da el UR5 (joystick / teach
> pendant) y el operador. Ver §7.

---

## 1. Windows (PC de la universidad)

### 1.1 Requisitos
- **Python 3.10–3.12 (64 bits)** desde <https://www.python.org/downloads/> con
  “Add Python to PATH”.
- **Driver CH340** para el Arduino (conversor USB-serial). Windows 10/11 suele
  instalarlo solo; si no, instalar `CH341SER` (WCH).

### 1.2 Entorno virtual e instalación
```powershell
cd C:\ruta\Desafio_Final
py -3.12 -m venv .venv    # o py -3.11 / py -3.10 (nunca 3.13+)
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **No copiar el `.venv` de Linux**: no funciona en Windows. Se recrea.
> Si PowerShell bloquea el script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### 1.3 Detectar el puerto COM
1. Conectar el Arduino por USB.
2. **Administrador de dispositivos → Puertos (COM y LPT)** →
   `USB-SERIAL CH340 (COM4)`.
3. Anotar el número (p. ej. **COM4**).

También: `arduino-cli board list`.

### 1.4 Configurar el proyecto
- `vision/config.json`:
  ```json
  "gripper_port": "COM4"
  ```
- `.env`:
  ```env
  UR5_IP=192.168.1.100
  ROBOT_MODE=real
  ```
  (`UR5_IP` = IP real del robot; se ve en el pendant.)

### 1.5 Cargar el firmware

> El firmware **en uso** es `gripper/firmware/gripper_1signal/gripper_1signal.ino`
> (4 servos con **una sola señal** en D3, con watchdog y sin LEDs/E-STOP/HC-SR04).
> El sketch `gripper_4servos/` es la variante histórica: **no la uses**.

Opción A — Arduino IDE: abrir
`gripper/firmware/gripper_1signal/gripper_1signal.ino`, placa **UNO**, puerto
**COM4**, Upload.

Opción B — arduino-cli:
```powershell
arduino-cli lib install Servo
arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
arduino-cli upload -p COM4 --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
```

### 1.6 Ejecutar
```powershell
.\.venv\Scripts\python.exe -m core.dashboard       # escritorio + voz
# o bien
.\.venv\Scripts\python.exe -m core.dashboard_web   # interfaz web
```

### 1.7 Firewall (para ver la web desde el celular)
En PowerShell **como administrador**:
```powershell
New-NetFirewallRule -DisplayName "Dashboard UR5" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow
```

---

## 2. Linux (PC de desarrollo)

```bash
cd Desafio_Final
.venv/bin/python -m core.dashboard
# o
.venv/bin/python -m core.dashboard_web
```

- Puerto del gripper típico: `/dev/ttyUSB0` o `/dev/ttyACM0`
  (en `vision/config.json`).
- Permisos: `sudo usermod -aG dialout $USER` (reiniciar sesión).
- Compilar/subir firmware:
  ```bash
  arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
  arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
  ```

---

## 3. Robot UR5e por Ethernet

El robot **no** se conecta por USB.

1. Cable Ethernet: **PC ↔ Control Box** (puerto frontal del gabinete de control).
2. En el **teach pendant**: `Settings → System → Network` → anotar la **IP del
   robot** (suele ser `192.168.1.100`; confirmar).
3. Poner la notebook en la **misma subred**. Ej. robot `192.168.1.100` →
   PC `192.168.1.50` / máscara `255.255.255.0`.
4. En el pendant: `Settings → System → Remote Control` → **habilitar**
   (necesario para RTDE).
5. Verificar: `ping 192.168.1.100`.

> `ur_rtde` habla por el puerto **30004**.

---

## 4. Simulador URSim (sin robot físico)

```bash
./scripts/run_ursim.sh          # levanta URSim y espera a que esté listo
./scripts/run_ursim.sh stop     # lo detiene
```

Interfaz: <http://localhost:6080/vnc.html?autoconnect=true>

`.env` para simulador:
```env
UR5_IP=127.0.0.1
ROBOT_MODE=real
```

> El simulador se comporta como robot real: hay que encenderlo y liberar frenos
> (`power on` / `brake release`) desde el Dashboard Server o la interfaz.
> Cámara y gripper siguen siendo hardware real; para probar solo el robot, usar
> `core/ur5_client.py` directamente.
> La configuración de “Remote Control” vive dentro del contenedor: si se recrea,
> hay que volver a habilitarla en `Settings → System → Remote Control`.

---

## 5. Dashboards (elegir UNO)

| Dashboard | Comando | Para qué |
|---|---|---|
| Escritorio | `python -m core.dashboard` | Uso local, ventana OpenCV + control por voz |
| Web | `python -m core.dashboard_web` | Monitoreo/operación desde navegador o celular |

> **No ejecutar los dos a la vez**: comparten el puerto serial del gripper y se
> pelean.

### Interfaz web
- Misma PC: <http://localhost:5000>
- Otra PC/celular de la red: `http://<IP-de-la-PC>:5000`
- Video MJPEG en vivo, estado por SSE (funciona sin internet), botones de
  control, contadores de producción y alertas de seguridad.

---

## 6. Control por voz (dashboard de escritorio)

Al arrancar `core/dashboard.py`, el micrófono se calibra en segundo plano. Hablar
hacia el micrófono de la notebook:

| Comando | Acción | Respuesta |
|---|---|---|
| “Clasificar” / “Iniciar ciclo” / “Recoger pieza” | Ciclo completo Pick & Place | “Iniciando ciclo automático de clasificación” |
| “Clasificar pieza roja” / “Tomar roja” | Clasifica solo Categoría A | “Clasificando pieza roja hacia Zona A” |
| “Clasificar pieza azul” / “Tomar azul” | Clasifica solo Categoría B | “Clasificando pieza azul hacia Zona B” |
| “Abrir pinza” / “Abrir gripper” / “Soltar” | Abre los 4 servos | “Abriendo gripper” |
| “Cerrar pinza” / “Agarrar” | Cierra los 4 servos | “Cerrando gripper” |
| “Home” / “Reposo” / “Posición inicial” | Postura segura | “Moviendo a posición Home” |
| “Parar” / “Detener” / “Alto” / “Stop” | Aborta el movimiento | “Parada solicitada” |
| “Estado” / “¿Qué ves?” / “Diagnóstico” | Informa estado y piezas | “Sistema en estado… Hay una pieza…” |

- Tecla `[v]`: activa/silencia la voz.
- Teclas `[e]` / `[r]`: parada de seguridad manual / reiniciar parada.

> **La voz necesita internet**: `SpeechRecognition` usa Google. Sin red, usar
> botones, teclas o la interfaz web.

---

## 7. Seguridad física

### Qué hay y qué no hay

El montaje del gripper es **4 servos, una señal y nada más**: **no hay LEDs,
ni botón de E-STOP, ni sensor HC-SR04, ni LCD**. Las capas de seguridad
efectivas son:

| Capa | Qué hace | Dónde |
|---|---|---|
| **E-STOP del UR5e** | Parada física certificada del robot | Teach pendant / botón físico del robot |
| **Parada por software** | Frena el UR5 (`stopJ`) y bloquea ciclos | Tecla `[e]` del dashboard de escritorio, botón en la web o comando de voz “Parar” |
| **Watchdog del gripper (15 s)** | Si la PC muere, el firmware **abre el gripper** y suelta la pieza | `gripper/firmware/gripper_1signal/` |
| **Operador** | Nadie entra a la zona con el robot en movimiento | Persona a cargo |

> El sketch histórico `gripper_4servos` emitía `EMERGENCY` y
> `PROXIMITY_STOP`; **el código de Python ya no los maneja** (se eliminaron:
> con el firmware en uso nunca llegaban). Si alguna vez se recarga ese sketch
> viejo, el cliente los treatará como respuestas desconocidas.

### Botón de emergencia (NO montado)

> En el montaje actual **no hay pulsador**: el E-STOP es el del UR5e.
> El esquema histórico (pulsador NA entre D8 y GND, evento `EMERGENCY`)
> pertenecía al sketch `gripper_4servos`, que ya no se usa.

### Sensor HC-SR04 (NO montado)

> En el montaje actual **no hay sensor**: nada emite `PROXIMITY_STOP`.
> El esquema histórico (TRIG D10 / ECHO D11) pertenecía al sketch
> `gripper_4servos`, que ya no se usa.

> ⚠️ **Esta capa es un complemento de software. No reemplaza el sistema de
> seguridad certificado del UR** (E-stop hardware, protecciones, límites). La
> consigna lo exige: una parada solo por software no habilita el acceso humano a
> la zona peligrosa.

Detalle completo: `Seguridad_E_Interfaz_Web.md`.

---

## 8. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| No aparece el COM | Falta driver CH340 | Instalar CH341SER (WCH) |
| `gripper_ok: false` en la web | COM equivocado o firmware viejo | Revisar `gripper_port` y recargar el sketch |
| `Could not connect ... 30004` | IP mal, cable, o Remote Control off | `ping`, revisar subred y habilitar Remote Control |
| La cámara no abre | Índice ocupado / otro programa | Cerrar apps de cámara; probar otro `indice_camara` |
| La voz no reconoce | Sin internet | Usar teclado/web, o proveedor local |
| Web no abre desde el celular | Firewall de Windows | Regla de puerto 5000 (ver 1.7) |
| El gripper “hace cosas” sin pieza | Calibración `cerrado` / `abierto` del firmware | Ajustar con el sketch de calibración y la pieza real (ver `gripper/README.md` §4) |
| La fuente marca “CC” al moverse el servo | Límite de corriente de la fuente muy bajo | Subir el límite a ≥ 5 A y medir V en los bornes del servo (> 4.8 V). Ver `gripper/README.md` §2.2 |
| El servo tiembla o se resetea | Caída de tensión / falta tierra común | Verificar tierra común, condensador de 1000 µF y cableado (ver `gripper/README.md` §4.0) |

---

## 9. Archivos clave

| Archivo | Función |
|---|---|
| `core/dashboard.py` | Dashboard de escritorio + voz |
| `core/dashboard_web.py` | Interfaz web de monitoreo |
| `templates/dashboard.html` | Página web |
| `core/robot_controller.py` | Orquestador (visión + UR5 + gripper) |
| `core/ur5_client.py` | Cliente RTDE del UR5 (incluye `stop()`) |
| `core/voice_control.py` | Reconocimiento y síntesis de voz |
| `vision/` | Pipeline de visión (cámara, detección, homografía) |
| `gripper/firmware/gripper_1signal/` | Sketch del Arduino (el que va cargado) |
| `gripper/firmware/calibracion_1signal/` | Sketch de laboratorio (calibración de pulsos) |
| `gripper/README.md` | Wiring, fuente, calibración y diagnóstico del gripper |
| `gripper/tools/gripper_client.py` | Cliente serial (heartbeat + watchdog) |
| `tools/gestionar_logs.py` | Listar/limpiar logs por corrida |
| `scripts/instalar.ps1` / `scripts/instalar.sh` | Instalación de dependencias |
| `scripts/run_ursim.sh` | Simulador URSim en Docker |
| `requirements.txt` | Dependencias Python |
| `docs/GUIA_WINDOWS.md` | Guía detallada para Windows |
| `docs/Seguridad_E_Interfaz_Web.md` | Documentación de seguridad y web |
