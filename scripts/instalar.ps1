# =====================================================================
# Installer del Desafio 2 (Pick & Place UR5) para WINDOWS
# Crea el entorno virtual .venv en la RAIZ del proyecto e instala
# las dependencias declaradas en requirements.txt.
#
# Uso (PowerShell, desde cualquier carpeta):
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # solo si lo bloquea
#   .\scripts\instalar.ps1
#
# Despues de instalar, para activar el entorno (desde la raiz):
#   .\.venv\Scripts\Activate.ps1
# =====================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Instalador Desafio 2 (Windows) ===" -ForegroundColor Cyan
Write-Host ""

# --- 0. Ubicar la raiz del proyecto ---------------------------------
# El script vive en scripts\, pero requirements.txt y .venv pertenecen
# a la raiz del proyecto. Se busca requirements.txt en la carpeta del
# script y, si no esta, en la carpeta padre.
$ProjectRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot "requirements.txt"))) {
    $parent = Split-Path -Parent $PSScriptRoot
    if ($parent -and (Test-Path (Join-Path $parent "requirements.txt"))) {
        $ProjectRoot = $parent
    }
}

$requirements = Join-Path $ProjectRoot "requirements.txt"
if (-not (Test-Path $requirements)) {
    Write-Host "[ERROR] No se encontro requirements.txt." -ForegroundColor Red
    Write-Host "  Se esperaba en la raiz del proyecto: $ProjectRoot"
    Write-Host "  Verifica que instalar.ps1 siga dentro de la carpeta scripts\ del proyecto."
    exit 1
}

$venvDir    = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvLinux  = Join-Path $venvDir "bin\python"

Write-Host "Raiz del proyecto: $ProjectRoot" -ForegroundColor Green

# Aviso por instalaciones viejas que dejaban el .venv dentro de scripts\.
$legacyVenv = Join-Path $PSScriptRoot ".venv"
if ((Test-Path $legacyVenv) -and ($legacyVenv -ne $venvDir)) {
    Write-Host "[AVISO] Se encontro un .venv viejo en $legacyVenv" -ForegroundColor Yellow
    Write-Host "        (instalacion anterior). Se ignora; el entorno valido ahora es:"
    Write-Host "        $venvDir"
    Write-Host "        Podes borrar la carpeta vieja para liberar espacio."
}

# --- 1. Elegir interprete de Python ---------------------------------
# ur_rtde 1.6.5 y PyAudio 0.2.14 solo publican wheels para Python
# 3.10-3.12 en Windows de 64 bits. Fuera de ese rango pip intenta
# compilar desde fuente y la instalacion falla (o queda inservible).
function Get-PythonInfo {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Argumentos = @()
    )
    try {
        $out = & $Exe @Argumentos -c "import sys, struct; print('%d.%d.%d|%d' % (sys.version_info[0], sys.version_info[1], sys.version_info[2], struct.calcsize('P') * 8))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }

        $linea = ($out | Select-Object -Last 1).ToString().Trim()
        $partes = $linea -split '\|'
        if ($partes.Count -ne 2) { return $null }

        $nums = $partes[0] -split '\.'
        if ($nums.Count -ne 3) { return $null }

        return [pscustomobject]@{
            Exe        = $Exe
            Argumentos = $Argumentos
            Major      = [int]($nums[0])
            Minor      = [int]($nums[1])
            Micro      = [int]($nums[2])
            Bits       = [int]($partes[1])
            Version    = $partes[0]
        }
    } catch {
        return $null
    }
}

function Test-PythonCompatible {
    param($Info)
    return ($Info -and
            $Info.Major -eq 3 -and
            $Info.Minor -ge 10 -and
            $Info.Minor -le 12 -and
            $Info.Bits -eq 64)
}

$seleccion  = $null
$descartados = @()

# Preferir 3.12, luego 3.11 y 3.10. Todos tienen wheels de ur_rtde/PyAudio.
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($ver in @("3.12", "3.11", "3.10")) {
        $info = Get-PythonInfo -Exe "py" -Argumentos @("-$ver")
        if (Test-PythonCompatible $info) { $seleccion = $info; break }
        if ($info) { $descartados += "py -$ver = Python $($info.Version) ($($info.Bits) bits)" }
    }
}

# Fallback: python / python3 del PATH (solo si cumplen el rango).
if (-not $seleccion) {
    foreach ($exe in @("python", "python3")) {
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $info = Get-PythonInfo -Exe $exe
        if (Test-PythonCompatible $info) { $seleccion = $info; break }
        if ($info) { $descartados += "$exe = Python $($info.Version) ($($info.Bits) bits)" }
    }
}

if (-not $seleccion) {
    Write-Host "[ERROR] No se encontro Python 3.10-3.12 de 64 bits." -ForegroundColor Red
    if ($descartados.Count -gt 0) {
        Write-Host "  Versiones encontradas pero no compatibles:"
        foreach ($d in $descartados) { Write-Host "    - $d" }
    }
    Write-Host ""
    Write-Host "  ur_rtde 1.6.5 solo tiene wheels para Python 3.10-3.12 (Windows 64 bits)."
    Write-Host "  Instala Python 3.12 (64 bits) desde https://www.python.org/downloads/"
    Write-Host "  y marca 'Add Python to PATH' durante la instalacion."
    Write-Host "  Si usas el launcher 'py', verifica con:  py -0p"
    exit 1
}

$comandoPython = $seleccion.Exe
if ($seleccion.Argumentos.Count -gt 0) {
    $comandoPython += " " + ($seleccion.Argumentos -join " ")
}
Write-Host "[1/4] Python detectado: $($seleccion.Version) ($($seleccion.Bits) bits) -> $comandoPython" -ForegroundColor Green

# --- 2. Preparar .venv en la raiz -----------------------------------
$reutilizarVenv = $false

if (Test-Path $venvLinux) {
    Write-Host "[2/4] .venv existente es de LINUX y no funciona en Windows. Borrando..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvDir
} elseif (Test-Path $venvPython) {
    $infoVenv = Get-PythonInfo -Exe $venvPython
    if (Test-PythonCompatible $infoVenv) {
        Write-Host "[2/4] .venv ya existe con Python $($infoVenv.Version) ($($infoVenv.Bits) bits); se reutilizara." -ForegroundColor Yellow
        $reutilizarVenv = $true
    } else {
        if ($infoVenv) {
            $verVenv = "$($infoVenv.Version) ($($infoVenv.Bits) bits)"
        } else {
            $verVenv = "desconocida"
        }
        Write-Host "[2/4] .venv existente no es compatible (Python $verVenv). Se recrea..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venvDir
    }
}

if (-not $reutilizarVenv) {
    Write-Host "[2/4] Creando entorno virtual en $venvDir ..." -ForegroundColor Yellow
    $crearArgs = @()
    if ($seleccion.Argumentos.Count -gt 0) { $crearArgs += $seleccion.Argumentos }
    $crearArgs += @("-m", "venv", $venvDir)
    & $seleccion.Exe @crearArgs
}

if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] No se pudo crear .venv en $venvDir" -ForegroundColor Red
    exit 1
}

# --- 3. Actualizar pip e instalar dependencias ----------------------
Write-Host "[3/4] Actualizando pip e instalando requirements.txt (puede tardar unos minutos)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Fallo al actualizar pip." -ForegroundColor Red
    exit 1
}

& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] Fallo la instalacion de dependencias." -ForegroundColor Red
    Write-Host "  - Si falla PyAudio:  verifica que el Python sea 64 bits (PyAudio 0.2.14 tiene wheels para 3.10-3.13)."
    Write-Host "  - Si falla ur_rtde:  usa Python 3.10-3.12 (no hay wheels para 3.13+ en Windows)."
    Write-Host "  - Revisa el mensaje de error anterior: suele indicar que paquete y version fallaron."
    exit 1
}

# Coherencia entre paquetes (dependencias rotas o versiones incompatibles).
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] pip check detecto dependencias incompatibles." -ForegroundColor Red
    exit 1
}

# --- 4. Verificar imports criticos ----------------------------------
Write-Host "[4/4] Verificando instalacion..." -ForegroundColor Yellow
& $venvPython -c "import cv2, numpy, flask, serial, serial.tools.list_ports, dotenv, rtde_control, rtde_receive, speech_recognition, pyttsx3, pyaudio; print('      Todos los modulos OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Hay modulos que no se pudieron importar." -ForegroundColor Red
    exit 1
}

# --- 5. Crear .env si falta -----------------------------------------
$envFile    = Join-Path $ProjectRoot ".env"
$envExample = Join-Path $ProjectRoot ".env.example"
if ((-not (Test-Path $envFile)) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envFile
    Write-Host "[OK] .env creado desde .env.example (revisa UR5_IP y ROBOT_MODE)." -ForegroundColor Green
}

# --- Listo -----------------------------------------------------------
Write-Host ""
Write-Host "Instalacion completa." -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos (desde $ProjectRoot):"
Write-Host "  1. Activa el entorno:      .\.venv\Scripts\Activate.ps1"
Write-Host "  2. Configura .env          (UR5_IP y ROBOT_MODE=real|mock|sim)"
Write-Host "  3. Configura el puerto COM del gripper en vision\config.json"
Write-Host "  4. Ejecuta:                .\.venv\Scripts\python.exe -m core.dashboard_web"
Write-Host "                    o bien:  .\.venv\Scripts\python.exe -m core.dashboard"
Write-Host ""
