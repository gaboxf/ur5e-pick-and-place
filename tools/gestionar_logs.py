"""Listar, conservar o borrar los logs guardados por corrida.

Uso:
  python tools/gestionar_logs.py                 # lista las corridas guardadas
  python tools/gestionar_logs.py --quedar 5      # conserva solo las 5 más recientes
  python tools/gestionar_logs.py --borrar-todos  # borra TODO (pide confirmación)
  python tools/gestionar_logs.py --si            # con --borrar-todos, no pregunta

La carpeta se resuelve igual que en logging_config: --dir > LOG_DIR > logs/
(junto a los fuentes del proyecto).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ejecución directa (python tools/gestionar_logs.py): agregar la raíz del
# proyecto al path para poder importar el paquete "core".
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from core.logging_config import listar_logs, limpiar_logs


def _fmt_tam(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Gestión de los logs por corrida del proyecto UR5."
    )
    ap.add_argument("--dir", default=None,
                    help="Carpeta de logs (por defecto: LOG_DIR o ./logs)")
    ap.add_argument("--quedar", type=int, metavar="N",
                    help="Conserva solo las N corridas más recientes y borra el resto")
    ap.add_argument("--borrar-todos", action="store_true",
                    help="Borra todas las corridas guardadas")
    ap.add_argument("--si", action="store_true",
                    help="No pide confirmación (usar con --borrar-todos)")
    args = ap.parse_args()

    corridas = listar_logs(args.dir)
    if not corridas:
        print("No hay corridas guardadas.")
        return

    print(f"{'Modificado':<22}{'Bytes':>10}  Archivo")
    for c in corridas:
        print(f"{c['modificado']:%Y-%m-%d %H:%M:%S}{'':>2}"
              f"{_fmt_tam(c['tam_bytes']):>8}  {c['nombre']}")
    total = sum(c["tam_bytes"] for c in corridas)
    print(f"\n{len(corridas)} corridas, {_fmt_tam(total)} en total.\n")

    if args.borrar_todos:
        if not args.si:
            r = input(f"¿Borrar TODAS las {len(corridas)} corridas? "
                      "(escribí SI para confirmar): ").strip().lower()
            if r != "si":
                print("Cancelado. No se borró nada.")
                return
        borrados = limpiar_logs(borrar_todos=True, dir_logs=args.dir)
        print(f"Borradas {len(borrados)} corridas.")
        return

    if args.quedar is not None:
        if args.quedar < 1:
            print("--quedar debe ser >= 1 (usá --borrar-todos para vaciar).")
            return
        borrados = limpiar_logs(conservar=args.quedar, dir_logs=args.dir)
        if borrados:
            print(f"Borradas {len(borrados)} corridas viejas "
                  f"(conservadas las {args.quedar} más recientes).")
        else:
            print(f"No había corridas para borrar (había {len(corridas)}).")


if __name__ == "__main__":
    main()
