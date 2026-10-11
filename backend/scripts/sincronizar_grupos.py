"""Rellena grupo/subgrupo (A1, A2, C1, C2) de convocatorias locales con la
subescala declarada en el anuncio oficial del BOE.

    python scripts/sincronizar_grupos.py            # solo revisión (no escribe)
    python scripts/sincronizar_grupos.py --aplicar  # rellena los vacíos
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clasificacion_subescala import sincronizar_grupos_subescala  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Grupo/subgrupo desde la subescala oficial")
    parser.add_argument("--aplicar", action="store_true", help="Escribe; sin esto solo revisa")
    args = parser.parse_args()
    try:
        r = sincronizar_grupos_subescala(aplicar=args.aplicar)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        print(f"{(d['resultado'] or 'SIN DATO'):8} proceso={d['proceso_id']:<5} {str(d['organismo'])[:36]:<36} "
              f"{str(d['denominacion'])[:40]:<40} {d.get('subescala') or ''}")
    print(f"\nmodo={r['modo']} revisados={r['revisados']} clasificados={r['clasificados']} sin_clasificar={r['sin_clasificar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
