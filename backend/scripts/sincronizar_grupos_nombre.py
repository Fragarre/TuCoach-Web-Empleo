"""Grupo/subgrupo deducido del nombre oficial del puesto (último recurso).

    python scripts/sincronizar_grupos_nombre.py            # solo revisión
    python scripts/sincronizar_grupos_nombre.py --aplicar  # rellena vacíos
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.grupos_por_nombre import sincronizar_grupos_por_nombre  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Grupo/subgrupo deducido del nombre del puesto")
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    try:
        r = sincronizar_grupos_por_nombre(aplicar=args.aplicar)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        print(f"{(d['resultado'] or 'SIN DATO'):8} proceso={d['proceso_id']:<5} {str(d['organismo'])[:34]:<34} {str(d['denominacion'])[:60]}")
    print(f"\nmodo={r['modo']} revisados={r['revisados']} clasificados={r['clasificados']} sin_clasificar={r['sin_clasificar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
