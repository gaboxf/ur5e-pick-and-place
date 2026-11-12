# =====================================================================
# Installer del Desafio 2 (Pick & Place UR5) para WINDOWS
# Crea el entorno virtual .venv e instala las dependencias.
#
# Uso (PowerShell en la carpeta del proyecto):
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # solo si lo bloquea
#   .\instalar.ps1
#
# Despues de instalar, para activar el entorno:
#   .\.venv\Scripts\Activate.ps1
# =====================================================================

$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "=== Instalador Desafio 2 (Windows) ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Elegir interprete de Python ---------------------------------
# Busca el launcher 'py' (recomendado) y prefiere 3.10 a 3.12,
# versiones con wheels de ur_rtde y PyAudio en Windows.
$python = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($ver in @("-3.10", "-3.11", "-3.12")) {
        $candidato = "py $ver"
        try {
            $out = & py $ver --version 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidato; break }
        } catch { }
    }
    if (-not $python) {
        # Cualquier Python 3 disponible via launcher
        try {
            $out = & py -3 --version 2>$null
            if ($LASTEXITCODE -eq 0) { $python = "py -3" }
        } catch { }
    }
}

if (-not $python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $python = "python"
}

if (-not $python) {
    Write-Host "[ERROR] No se encontro Python." -ForegroundColor Red
    Write-Host "  Instala Python 3.10 - 3.12 (64 bits) desde https://www.python.org/downloads/"
    Write-Host "  y marca 'Add Python to PATH' durante la instalacion."
    exit 1
}

$verPython = Invoke-Expression "$python --version"
Write-Host "[1/4] Python detectado: $verPython" -ForegroundColor Green

# --- 2. Preparar .venv ----------------------------------------------
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvLinux  = Join-Path $PSScriptRoot ".venv\bin\python"

if (Test-Path $venvLinux) {
    Write-Host "[2/4] .venv existente es de LINUX y no funciona en Windows. Borrando..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force (Join-Path $PSScriptRoot ".venv")
}

if (-not (Test-Path $venvPython)) {
    Write-Host "[2/4] Creando entorno virtual .venv..." -ForegroundColor Yellow
    Invoke-Expression "$python -m venv `"$PSScriptRoot\.venv`""
} else {
    Write-Host "[2/4] .venv ya existe (se reutilizara)." -ForegroundColor Yellow
}

if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] No se pudo crear .venv." -ForegroundColor Red
    exit 1
}

# --- 3. Actualizar pip e instalar dependencias ----------------------
Write-Host "[3/4] Actualizando pip e instalando requirements.txt (puede tardar unos minutos)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Fallo al actualizar pip." -ForegroundColor Red
    exit 1
}

& $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "" -ForegroundColor Red
    Write-Host "[ERROR] Fallo la instalacion de dependencias." -ForegroundColor Red
    Write-Host "  - Si falla PyAudio:  verifica que el Python sea 64 bits (PyAudio 0.2.14 tiene wheels para 3.7-3.13)."
    Write-Host "  - Revisa el mensaje de error anterior: suele indicar que paquete y version fallaron."
    exit 1
}

# --- 4. Verificar imports criticos ----------------------------------
Write-Host "[4/4] Verificando instalacion..." -ForegroundColor Yellow
& $venvPython -c "import cv2, numpy, flask, serial, dotenv, rtde_control, rtde_receive, speech_recognition, pyttsx3, pyaudio; print('      Todos los modulos OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Hay modulos que no se pudieron importar." -ForegroundColor Red
    exit 1
}

# --- Listo -----------------------------------------------------------
Write-Host ""
Write-Host "Instalacion completa." -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos:"
Write-Host "  1. Activa el entorno:      .\.venv\Scripts\Activate.ps1"
Write-Host "  2. Configura .env          (UR5_IP y ROBOT_MODE=real|mock|sim)"
Write-Host "  3. Configura el puerto COM del gripper en vision\config.json"
Write-Host "  4. Ejecuta:                .\.venv\Scripts\python.exe -m core.dashboard_web"
Write-Host "                    o bien:  .\.venv\Scripts\python.exe -m core.dashboard"
Write-Host ""
