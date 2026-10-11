"""Guarda fecha_apertura/fecha_cierre de convocatorias locales desde el BOE.

    python scripts/sincronizar_plazos.py            # solo revisión (no escribe)
    python scripts/sincronizar_plazos.py --aplicar  # rellena los plazos vacíos

Una línea por proceso: RELLENAR (fechas vacías que se calculan), DISCREPANCIA
(hay fechas guardadas distintas del cálculo; nunca se sobrescriben) o
PARCIAL_SIN_TOCAR. Un plazo vencido NO cierra la convocatoria.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.plazos_inscripcion import sincronizar_plazos_inscripcion  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Plazos de inscripción desde el BOE")
    parser.add_argument("--aplicar", action="store_true", help="Escribe; sin esto solo revisa")
    args = parser.parse_args()
    try:
        r = sincronizar_plazos_inscripcion(aplicar=args.aplicar)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        aviso = "" if d["calendario_verificado"] else "  (calendario no verificado)"
        print(f"{d['accion']:17} proceso={d['proceso_id']:<5} {str(d['organismo'])[:38]:<38} "
              f"guardado={d['guardado'][0] or '-'}..{d['guardado'][1] or '-'}  "
              f"calculado={d['calculado'][0]}..{d['calculado'][1]}  {d['literal']}{aviso}")
    print(f"\nmodo={r['modo']} revisados={r['revisados']} rellenados={r['rellenados']} "
          f"discrepancias={r['discrepancias']} parciales={r['parciales']} sin_plazo={r['sin_plazo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
