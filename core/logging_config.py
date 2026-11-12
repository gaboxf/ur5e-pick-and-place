"""Configuración centralizada de logging para todo el proyecto.

Reglas del proyecto:
- Solo los puntos de entrada (archivos ejecutados con `python archivo.py`)
  llaman a `configurar_logging()`. Los módulos importables NUNCA configuran
  logging: solo obtienen su logger con `get_logger()`.
- Formato único: `AAAA-mm-dd HH:MM:SS LEVEL  nombre: mensaje`.
- Volumen controlado:
    * INFO  = eventos que importan en operación (conexiones, ciclos, seguridad).
    * DEBUG = diagnóstico fino (movimientos intermedios, frames, estado TTS).
      Se activa con la variable de entorno LOG_LEVEL=DEBUG o pasando el nivel.
    * Las librerías de terceros (werkzeug, rtde, urllib3, ...) se limitan a
      WARNING para no inundar la consola.

Logs en archivo (opcional, para revisar una corrida después):
- Se activa con la variable de entorno LOG_ARCHIVO=1 (o pasando archivo=True).
- Cada corrida escribe a su propio archivo: logs/corrida_FECHA_HORA.log, así
  una sesión se puede revisar de punta a punta sin mezclar con otras.
- Al terminar la corrida el archivo queda; los viejos se autolimpian
  conservando las últimas N corridas (LOG_RETENCION, default 10).
- Configuración por variables de entorno (se puede poner en .env):
    LOG_ARCHIVO=1        -> guardar logs de cada corrida (default: no)
    LOG_DIR=logs         -> carpeta destino (default: <proyecto>/logs)
    LOG_RETENCION=10     -> cuántas corridas viejas conservar (0 = no borrar)
- Para listar o borrar corridas manualmente:  python tools/gestionar_logs.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
FECHA = "%Y-%m-%d %H:%M:%S"

# El proyecto vive un nivel arriba de este archivo (core/): así "logs"
# no depende del cwd.
_BASE = Path(__file__).resolve().parent.parent

# Nombre de los archivos por corrida (también lo usa el autolimpieza y el CLI)
PATRON_CORRIDA = "corrida_*.log"

# Librerías ruidosas que no aportan en operación normal: quedan en WARNING.
_LIB_RUIDOSAS = {
    "werkzeug": logging.WARNING,      # access log de Flask (cada petición HTTP)
    "engineio": logging.WARNING,
    "socketio": logging.WARNING,
    "urllib3": logging.WARNING,
    "rtde_control": logging.WARNING,  # driver del UR5
    "rtde_receive": logging.WARNING,
    "comtypes": logging.WARNING,
    "speech_recognition": logging.WARNING,
    "pyttsx3": logging.WARNING,
    "cv2": logging.WARNING,
}


def get_logger(nombre: str) -> logging.Logger:
    """Devuelve el logger del módulo (sin configurarlo)."""
    return logging.getLogger(nombre)


# ------------------------------------------------------------------ helpers
def _dir_logs(dir_logs: str | Path | None = None) -> Path:
    """Resuelve la carpeta de logs: argumento > env LOG_DIR > <proyecto>/logs."""
    if dir_logs is not None:
        return Path(dir_logs)
    env = os.getenv("LOG_DIR")
    if env:
        return Path(env)
    return _BASE / "logs"


def _env_bool(nombre: str, por_defecto: bool = False) -> bool:
    v = os.getenv(nombre)
    if v is None:
        return por_defecto
    return v.strip().lower() in ("1", "true", "si", "on", "yes")


# ------------------------------------------------------------- gestión logs
def listar_logs(dir_logs: str | Path | None = None) -> list[dict]:
    """Lista las corridas guardadas (más viejas primero).

    Devuelve dicts con: ruta, nombre, tam_bytes y modificado (datetime).
    """
    carpeta = _dir_logs(dir_logs)
    if not carpeta.exists():
        return []
    salida: list[dict] = []
    for p in sorted(carpeta.glob(PATRON_CORRIDA)):
        try:
            st = p.stat()
        except OSError:
            continue
        salida.append({
            "ruta": p,
            "nombre": p.name,
            "tam_bytes": st.st_size,
            "modificado": datetime.fromtimestamp(st.st_mtime),
        })
    return salida


def limpiar_logs(
    conservar: int = 10,
    dir_logs: str | Path | None = None,
    borrar_todos: bool = False,
) -> list[Path]:
    """Borra corridas viejas y devuelve la lista de archivos eliminados.

    conservar=0 con borrar_todos=False significa "no borrar nada".
    borrar_todos=True ignora `conservar` y borra absolutamente todo.
    """
    carpeta = _dir_logs(dir_logs)
    if not carpeta.exists():
        return []
    corridas = sorted(carpeta.glob(PATRON_CORRIDA))  # orden por nombre = cronológico
    if borrar_todos:
        a_borrar = corridas
    elif conservar > 0:
        a_borrar = corridas[:-conservar] if len(corridas) > conservar else []
    else:
        a_borrar = []
    borrados: list[Path] = []
    for p in a_borrar:
        try:
            p.unlink()
            borrados.append(p)
        except OSError:
            continue  # en uso u otro problema: no interrumpe la corrida
    return borrados


def _nueva_corrida(dir_logs: str | Path | None = None) -> Path:
    """Crea el archivo de la corrida actual y devuelve su ruta."""
    carpeta = _dir_logs(dir_logs)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"corrida_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    if ruta.exists():  # dos corridas en el mismo segundo: muy raro, pero seguro
        ruta = carpeta / f"corrida_{datetime.now():%Y-%m-%d_%H-%M-%S}_{os.getpid()}.log"
    return ruta


# ------------------------------------------------------------- configuración
def configurar_logging(
    nivel: int | str | None = None,
    archivo: bool | None = None,
    dir_logs: str | Path | None = None,
    retencion: int | None = None,
) -> None:
    """Configura el logging raíz una sola vez (idempotente).

    Args:
        nivel: nivel global, admite int o nombre ("DEBUG", "INFO", ...).
               Por defecto toma la variable de entorno LOG_LEVEL y, si no
               existe, INFO.
        archivo: guardar también a archivo por corrida. Por defecto toma
               LOG_ARCHIVO (default False).
        dir_logs: carpeta destino del archivo. Por defecto LOG_DIR.
        retencion: cuántas corridas viejas conservar. Por defecto LOG_RETENCION.
    """
    raiz = logging.getLogger()
    if getattr(raiz, "_proyecto_configurado", False):
        return

    # Cargar .env antes de leer LOG_* para que la config del archivo funcione
    # (el proyecto ya depende de dotenv; si no está disponible, se ignora).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if nivel is None:
        nivel = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(nivel, str):
        nivel = getattr(logging, str(nivel).upper(), logging.INFO)

    formatter = logging.Formatter(FORMATO, FECHA)

    # 1. Consola (siempre)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    raiz.addHandler(handler)
    raiz.setLevel(nivel)
    raiz._proyecto_configurado = True  # type: ignore[attr-defined]

    # 2. Archivo por corrida (opcional)
    if archivo is None:
        archivo = _env_bool("LOG_ARCHIVO", False)
    if archivo:
        try:
            ruta = _nueva_corrida(dir_logs)
            fh = logging.FileHandler(ruta, encoding="utf-8")
            fh.setFormatter(formatter)
            raiz.addHandler(fh)
            raiz._archivo_log = str(ruta)  # type: ignore[attr-defined]

            if retencion is None:
                try:
                    retencion = int(os.getenv("LOG_RETENCION", "10") or 10)
                except ValueError:
                    retencion = 10
            borrados = limpiar_logs(conservar=retencion, dir_logs=dir_logs)

            get_logger("LoggingConfig").info(
                "Logs de esta corrida en %s%s",
                ruta,
                f" (autolimpieza: {len(borrados)} corridas viejas borradas)"
                if borrados else "",
            )
        except OSError as e:
            # Nunca romper la corrida por no poder escribir logs
            get_logger("LoggingConfig").warning(
                "No se pudo activar el log en archivo: %s", e
            )

    for nombre, nivel_lib in _LIB_RUIDOSAS.items():
        logging.getLogger(nombre).setLevel(nivel_lib)


def ruta_log_actual() -> str | None:
    """Ruta del archivo de log de la corrida actual, si se está guardando."""
    return getattr(logging.getLogger(), "_archivo_log", None)
