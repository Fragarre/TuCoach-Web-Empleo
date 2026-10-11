"""Pasa a un puesto legible los títulos brutos del BOP ("Anunci de l'Ajuntament...").

    python scripts/limpiar_titulos_bop.py            # solo revisión
    python scripts/limpiar_titulos_bop.py --aplicar  # actualiza (guarda el original)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.titulos_bop import limpiar_titulos_bop  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Limpia títulos brutos del BOP")
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    try:
        r = limpiar_titulos_bop(aplicar=args.aplicar)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        print(f"proceso={d['proceso_id']:<5} {str(d['organismo'])[:30]:<30} -> {d['despues'] or '(título igual)'}  plazas={d['plazas'] or '-'}")
    print(f"\nmodo={r['modo']} revisados={r['revisados']} limpiados={r['limpiados']} plazas_rellenadas={r['plazas_rellenadas']} sin_cambio={r['sin_cambio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
