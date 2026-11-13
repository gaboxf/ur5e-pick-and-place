# Guía rápida — Ejecutar en Windows (PC de la universidad)

Esta guía adapta `Arranque.md` a una PC con **Windows**. Las diferencias
principales son: el **puerto COM** del Arduino, el **entorno virtual** y los
**drivers**.

---

## 1. Preparar el proyecto

1. Copiá la carpeta del proyecto a la PC (por ejemplo `C:\UR5_Desafio`).
2. **No copies la carpeta `.venv` de Linux** (no funciona en Windows). Si la
   copiaste, borrala: el entorno se recrea en Windows.
3. Instalá **Python 3.10–3.12 (64 bits)** desde <https://www.python.org/downloads/>
   y marcá **"Add Python to PATH"** al instalar.

## 2. Crear el entorno virtual e instalar dependencias

Abrí **PowerShell** en la carpeta del proyecto:

```powershell
cd C:\UR5_Desafio
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> Si PowerShell bloquea el script por políticas de ejecución:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> También podés usar `cmd.exe` y `.venv\Scripts\activate.bat`.

> **PyAudio** instala con wheel en Windows (0.2.14 tiene wheels para Python
> 3.7–3.13). Si falla, verifica que el Python sea de 64 bits.

> **ur_rtde** 1.6.5 tiene wheels precompilados para Python 3.7–3.12 en Windows,
> así que no debería necesitar compilación.

## 3. Instalar el driver del Arduino (CH340)

El Arduino de este proyecto usa un conversor **CH340**. Si el Administrador de
dispositivos lo muestra con signo de admiración o no aparece el puerto COM:

- Descargá el driver CH341SER (WCH) y ejecutá el instalador.
- En Windows 10/11 muchas veces se instala solo por Windows Update.

## 4. Detectar el puerto COM

1. Conectá el Arduino por USB.
2. **Administrador de dispositivos → Puertos (COM y LPT)**.
   Debe aparecer algo como `USB-SERIAL CH340 (COM4)`.
3. Anotá el número, por ejemplo **COM4**.

También podés listar puertos con:

```powershell
arduino-cli board list
```

## 5. Configurar el proyecto

### 5.1 `vision/config.json`

Cambiá `gripper_port` por el COM detectado:

```json
"gripper_port": "COM4"
```

### 5.2 `.env`

```env
UR5_IP=192.168.1.100
ROBOT_MODE=real
```

> - `UR5_IP`: la IP real del robot (la ves en el pendant:
>   `Settings → System → Network`).
> - Para practicar sin robot, dejá `ROBOT_MODE=sim` y `UR5_IP=127.0.0.1`.

## 6. Cargar el firmware del Arduino

> El firmware en uso es `gripper_1signal` (4 servos con **una sola señal** en
> D3, con watchdog y **sin LEDs / E-STOP / HC-SR04**). La carpeta
> `gripper_4servos/` es la variante histórica: **no la uses**.

### Opción A — Arduino IDE (más simple en Windows)

1. Abrí `gripper/firmware/gripper_1signal/gripper_1signal.ino`.
2. Placa: **Arduino UNO**.
3. Puerto: el COM detectado (COM4).
4. **Upload**.

### Opción B — arduino-cli

```powershell
arduino-cli lib install Servo
arduino-cli compile --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
arduino-cli upload -p COM4 --fqbn arduino:avr:uno gripper/firmware/gripper_1signal
```

## 7. Red: PC ↔ Control Box del UR5

1. Cable Ethernet: **PC ↔ Control Box** (no al teach pendant).
2. Configurá la placa de red de Windows con IP fija en la **misma subred** que el
   robot. Ejemplo si el robot es `192.168.1.100`:
   - IP: `192.168.1.50`
   - Máscara: `255.255.255.0`
3. En el **pendant**: `Settings → System → Remote Control` → **habilitar**.
4. Comprobá:
   ```powershell
   ping 192.168.1.100
   ```

## 8. Ejecutar

```powershell
.\.venv\Scripts\python.exe -m core.dashboard       # escritorio + voz
# o bien
.\.venv\Scripts\python.exe -m core.dashboard_web   # interfaz web
```

> **No ejecutes los dos a la vez**: comparten el puerto COM del gripper.

## 9. Ver la interfaz web desde el celular

1. Averiguá la IP de la PC: `ipconfig` (buscá IPv4, ej. `192.168.1.50`).
2. Abrí en el teléfono: `http://192.168.1.50:5000`
3. Si no abre, permití el puerto en el Firewall (PowerShell **como admin**):

```powershell
New-NetFirewallRule -DisplayName "Dashboard UR5" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow
```

> Al arrancar `core/dashboard_web.py`, Windows puede mostrar un aviso de Firewall:
> aceptá el acceso en **redes privadas**.

---

## Recordatorios

- **Voz:** `SpeechRecognition` usa Google → necesita internet. Sin internet,
  usá los botones/teclas o la interfaz web.
- **Cámara:** el código ya usa DirectShow (`cv2.CAP_DSHOW`) en Windows, con
  fallback automático.
- **Seguridad:** el E-stop del gripper es software; no reemplaza el E-stop
  certificado del UR. No ingreses a la zona de movimiento.
- **Calibración:** ajustá `cerrado1..4` del sketch con la pieza real antes de
  operar.
