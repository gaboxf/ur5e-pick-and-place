#!/usr/bin/env bash
# =====================================================================
# Installer del Desafio 2 (Pick & Place UR5) para LINUX
# Crea el entorno virtual .venv en la RAIZ del proyecto e instala
# las dependencias declaradas en requirements.txt.
#
# Uso (desde cualquier carpeta):
#   chmod +x scripts/instalar.sh
#   ./scripts/instalar.sh
#
# Despues de instalar, para activar el entorno (desde la raiz):
#   source .venv/bin/activate
# =====================================================================
set -euo pipefail

echo ""
echo "=== Instalador Desafio 2 (Linux) ==="
echo ""

# --- 0. Ubicar la raiz del proyecto ---------------------------------
# El script vive en scripts/, pero requirements.txt y .venv pertenecen
# a la raiz del proyecto. Se busca requirements.txt en la carpeta del
# script y, si no esta, en la carpeta padre.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
if [ ! -f "$PROJECT_ROOT/requirements.txt" ] && [ -f "$SCRIPT_DIR/../requirements.txt" ]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

REQUIREMENTS="$PROJECT_ROOT/requirements.txt"
if [ ! -f "$REQUIREMENTS" ]; then
    echo "[ERROR] No se encontro requirements.txt." >&2
    echo "  Se esperaba en la raiz del proyecto: $PROJECT_ROOT" >&2
    exit 1
fi

VENV_DIR="$PROJECT_ROOT/.venv"
venv_python="$VENV_DIR/bin/python"
venv_windows="$VENV_DIR/Scripts/python.exe"

echo "Raiz del proyecto: $PROJECT_ROOT"

# Aviso por instalaciones viejas que dejaban el .venv dentro de scripts/.
legacy_venv="$SCRIPT_DIR/.venv"
if [ -d "$legacy_venv" ] && [ "$legacy_venv" != "$VENV_DIR" ]; then
    echo "[AVISO] Se encontro un .venv viejo en $legacy_venv"
    echo "        (instalacion anterior). Se ignora; el entorno valido ahora es:"
    echo "        $VENV_DIR"
    echo "        Podes borrar la carpeta vieja para liberar espacio."
fi

# --- 1. Elegir interprete de Python (3.10 a 3.12, 64 bits) ----------
# ur_rtde 1.6.5 solo publica wheels cp310-cp312, y PyAudio depende de
# que exista wheel para la version elegida. Fuera del rango pip intenta
# compilar desde fuente y la instalacion falla o queda inservible.
es_compatible() {
    local info="$1" ver bits major minor resto
    [ -n "$info" ] || return 1
    ver="${info%%|*}"
    bits="${info##*|}"
    major="${ver%%.*}"
    resto="${ver#*.}"
    minor="${resto%%.*}"
    [ "$major" -eq 3 ] && [ "$minor" -ge 10 ] && [ "$minor" -le 12 ] && [ "$bits" -eq 64 ]
}

python=""
pyver=""
descartados=""

for candidato in python3.12 python3.11 python3.10 python3 python; do
    command -v "$candidato" >/dev/null 2>&1 || continue
    info="$("$candidato" -c 'import sys, struct; print("%d.%d.%d|%d" % (sys.version_info[0], sys.version_info[1], sys.version_info[2], struct.calcsize("P") * 8))' 2>/dev/null || true)"
    [ -n "$info" ] || continue

    ver="${info%%|*}"
    bits="${info##*|}"
    if es_compatible "$info"; then
        python="$candidato"
        pyver="$ver"
        break
    fi
    descartados="${descartados}    - $candidato = Python $ver ($bits bits)"$'\n'
done

if [ -z "$python" ]; then
    echo "[ERROR] No se encontro Python 3.10-3.12 de 64 bits." >&2
    if [ -n "$descartados" ]; then
        echo "  Versiones encontradas pero no compatibles:" >&2
        printf "%s" "$descartados" >&2
    fi
    echo "" >&2
    echo "  ur_rtde 1.6.5 solo tiene wheels para Python 3.10-3.12." >&2
    echo "  Instala python3.12 y python3.12-venv (en Debian/Ubuntu:" >&2
    echo "  sudo apt install python3.12 python3.12-venv python3-pip)" >&2
    exit 1
fi

echo "[1/4] Python detectado: $pyver ($candidato)"

# --- 2. Preparar .venv en la raiz -----------------------------------
reutilizar_venv=0

if [ -f "$venv_windows" ]; then
    echo "[2/4] .venv existente es de WINDOWS y no funciona en Linux. Borrando..." >&2
    rm -rf "$VENV_DIR"
elif [ -f "$venv_python" ]; then
    info_venv="$("$venv_python" -c 'import sys, struct; print("%d.%d.%d|%d" % (sys.version_info[0], sys.version_info[1], sys.version_info[2], struct.calcsize("P") * 8))' 2>/dev/null || true)"
    if es_compatible "$info_venv"; then
        echo "[2/4] .venv ya existe con Python ${info_venv%%|*}; se reutilizara."
        reutilizar_venv=1
    else
        echo "[2/4] .venv existente no es compatible (Python ${info_venv:-desconocida}). Se recrea..." >&2
        rm -rf "$VENV_DIR"
    fi
fi

if [ "$reutilizar_venv" -eq 0 ]; then
    echo "[2/4] Creando entorno virtual en $VENV_DIR ..."
    "$python" -m venv "$VENV_DIR"
fi

if [ ! -f "$venv_python" ]; then
    echo "[ERROR] No se pudo crear .venv en $VENV_DIR" >&2
    exit 1
fi

# --- 3. Actualizar pip e instalar dependencias ----------------------
echo "[3/4] Actualizando pip e instalando requirements.txt (puede tardar unos minutos)..."
"$venv_python" -m pip install --upgrade pip --quiet
if ! "$venv_python" -m pip install -r "$REQUIREMENTS"; then
    echo "[ERROR] Fallo la instalacion de dependencias." >&2
    echo "  - PyAudio en Linux requiere portaudio: sudo apt install portaudio19-dev" >&2
    echo "  - Compilar paquetes requiere headers de Python: sudo apt install python3-dev" >&2
    echo "  - Revisa el mensaje de error anterior: suele indicar que paquete y version fallaron." >&2
    exit 1
fi

# Coherencia entre paquetes (dependencias rotas o versiones incompatibles).
if ! "$venv_python" -m pip check; then
    echo "[ERROR] pip check detecto dependencias incompatibles." >&2
    exit 1
fi

# --- 4. Verificar imports criticos ----------------------------------
echo "[4/4] Verificando instalacion..."
if ! "$venv_python" -c "import cv2, numpy, flask, serial, serial.tools.list_ports, dotenv, rtde_control, rtde_receive, speech_recognition, pyttsx3, pyaudio" 2>/dev/null; then
    echo "[ERROR] Hay modulos que no se pudieron importar." >&2
    echo "  - PyAudio en Linux requiere portaudio: sudo apt install portaudio19-dev" >&2
    exit 1
fi
echo "      Todos los modulos OK"

# --- 5. Crear .env si falta -----------------------------------------
if [ ! -f "$PROJECT_ROOT/.env" ] && [ -f "$PROJECT_ROOT/.env.example" ]; then
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    echo "[OK] .env creado desde .env.example (revisa UR5_IP y ROBOT_MODE)."
fi

# --- Listo -----------------------------------------------------------
echo ""
echo "Instalacion completa."
echo ""
echo "Proximos pasos (desde $PROJECT_ROOT):"
echo "  1. Activa el entorno:      source .venv/bin/activate"
echo "  2. Configura .env          (UR5_IP y ROBOT_MODE=real|mock|sim)"
echo "  3. Configura gripper_port (/dev/ttyUSB0) en vision/config.json"
echo "  4. Ejecuta:                python -m core.dashboard_web"
echo "                    o bien:  python -m core.dashboard"
echo ""
