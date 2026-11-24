"""Punto de entrada para iniciar el dashboard de la celda."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> None:
    dashboards = ("web", "escritorio")
    parser = argparse.ArgumentParser(description="Inicia el dashboard de la celda UR5e.")
    parser.add_argument(
        "--dashboard",
        choices=dashboards,
        help="Reemplaza DASHBOARD_DEFAULT para esta ejecución.",
    )
    args = parser.parse_args(argv)

    load_dotenv(Path(__file__).resolve().with_name(".env"))
    elegido = args.dashboard or os.getenv("DASHBOARD_DEFAULT", "web").strip().lower()
    if elegido not in dashboards:
        parser.error("DASHBOARD_DEFAULT debe ser 'web' o 'escritorio'.")

    if elegido == "web":
        from core.dashboard_web import main as iniciar
    else:
        from core.dashboard import main as iniciar

    iniciar()


if __name__ == "__main__":
    main()
