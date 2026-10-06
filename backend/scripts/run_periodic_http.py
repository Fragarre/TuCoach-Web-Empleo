from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Render ejecuta este archivo desde el directorio backend. Añadimos ese
# directorio al path para importar la aplicación sin depender del servidor web.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.periodic import ejecutar_periodico


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta directamente el ciclo periódico de TuCoach Empleo")
    parser.add_argument("--aplicar", action="store_true", help="Aplica cambios; sin esta opción solo revisa")
    parser.add_argument("--dias", type=int, default=7, help="Días de solape (1-30 en modo ordinario)")
    parser.add_argument(
        "--historico",
        action="store_true",
        help="Autoriza una ventana superior a 30 días y desactiva las notificaciones",
    )
    args = parser.parse_args()

    try:
        payload = ejecutar_periodico(
            aplicar=args.aplicar,
            dias_solape=args.dias,
            historico=args.historico,
        )
    except Exception as exc:
        print(f"Error ejecutando ciclo periódico: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
