"""Grupo/subgrupo y plazas desde el documento de las bases (BOP / sede).

    python scripts/sincronizar_grupos_documento.py            # solo revisión
    python scripts/sincronizar_grupos_documento.py --aplicar  # rellena vacíos
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.grupos_documento import sincronizar_grupos_documento  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Grupo/subgrupo desde el documento de las bases")
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    try:
        r = sincronizar_grupos_documento(aplicar=args.aplicar)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        print(f"{(d['resultado'] or 'SIN DATO'):8} proceso={d['proceso_id']:<5} {str(d['organismo'])[:34]:<34} "
              f"plazas={str(d.get('plazas') or '-'):<3} {d.get('origen_dato') or '':<6} {str(d.get('url') or '')[-48:]}")
    print(f"\nmodo={r['modo']} revisados={r['revisados']} clasificados={r['clasificados']} "
          f"sin_clasificar={r['sin_clasificar']} sin_documento={r['sin_documento']} errores={r['errores']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
