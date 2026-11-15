#!/usr/bin/env bash
# =====================================================================
# Installer del Desafio 2 (Pick & Place UR5) para LINUX
# Crea el entorno virtual .venv e instala las dependencias.
#
# Uso (en la carpeta del proyecto):
#   chmod +x instalar.sh
#   ./instalar.sh
#
# Despues de instalar, para activar el entorno:
#   source .venv/bin/activate
# =====================================================================
set -euo pipefail

echo ""
echo "=== Instalador Desafio 2 (Linux) ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 1. Elegir interprete de Python (3.10 a 3.12) --------------------
python=""
for candidato in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidato" >/dev/null 2>&1; then
        python="$candidato"
        break
    fi
done

if [ -z "$python" ]; then
    echo "[ERROR] No se encontro Python 3." >&2
    echo "  Instala python3 y python3-venv (en Debian/Ubuntu:" >&2
    echo "  sudo apt install python3 python3-venv python3-pip)" >&2
    exit 1
fi

echo "[1/4] Python detectado: $($python --version)"

pyver="$("$python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
pymajor="${pyver%%.*}"
pyminor="${pyver##*.}"
if [ "$pymajor" -ne 3 ] || [ "$pyminor" -lt 10 ] || [ "$pyminor" -gt 12 ]; then
    echo "[AVISO] Python $pyver esta fuera del rango probado (3.10-3.12)." >&2
    echo "  ur_rtde solo tiene wheels para 3.7-3.12; con Python >=3.13 tendra que" >&2
    echo "  compilar desde fuente (requiere cmake y libboost-all-dev)." >&2
fi

# --- 2. Preparar .venv -------------------------------------------------
venv_python="$SCRIPT_DIR/.venv/bin/python"
venv_windows="$SCRIPT_DIR/.venv/Scripts/python.exe"

if [ -f "$venv_windows" ]; then
    echo "[2/4] .venv existente es de WINDOWS y no funciona en Linux. Borrando..." >&2
    rm -rf "$SCRIPT_DIR/.venv"
fi

if [ ! -f "$venv_python" ]; then
    echo "[2/4] Creando entorno virtual .venv..."
    "$python" -m venv "$SCRIPT_DIR/.venv"
else
    echo "[2/4] .venv ya existe (se reutilizara)."
fi

# --- 3. Actualizar pip e instalar dependencias -------------------------
echo "[3/4] Actualizando pip e instalando requirements.txt (puede tardar unos minutos)..."
"$venv_python" -m pip install --upgrade pip --quiet
if ! "$venv_python" -m pip install -r "$SCRIPT_DIR/requirements.txt"; then
    echo "[ERROR] Fallo la instalacion de dependencias." >&2
    echo "  - PyAudio en Linux requiere portaudio: sudo apt install portaudio19-dev" >&2
    echo "  - Compilar paquetes requiere headers de Python: sudo apt install python3-dev" >&2
    echo "  - Revisa el mensaje de error anterior: suele indicar que paquete y version fallaron." >&2
    exit 1
fi

# --- 4. Verificar imports criticos --------------------------------------
echo "[4/4] Verificando instalacion..."
if ! "$venv_python" -c "import cv2, numpy, flask, serial, dotenv, rtde_control, rtde_receive, speech_recognition, pyttsx3, pyaudio" 2>/dev/null; then
    echo "[ERROR] Hay modulos que no se pudieron importar." >&2
    echo "  - PyAudio en Linux requiere portaudio: sudo apt install portaudio19-dev" >&2
    exit 1
fi
echo "      Todos los modulos OK"

# --- Listo ---------------------------------------------------------------
echo ""
echo "Instalacion completa."
echo ""
echo "Proximos pasos:"
echo "  1. Activa el entorno:      source .venv/bin/activate"
echo "  2. Configura .env          (UR5_IP y ROBOT_MODE=real|mock|sim)"
echo "  3. Configura gripper_port (/dev/ttyUSB0) en vision/config.json"
echo "  4. Ejecuta:                python -m core.dashboard_web"
echo "                    o bien:  python -m core.dashboard"
echo ""
