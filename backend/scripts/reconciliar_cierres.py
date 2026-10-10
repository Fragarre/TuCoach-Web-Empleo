"""Revisa o aplica la reconciliación de cierres, mostrando TODAS las decisiones.

Pensado para auditar con títulos reales tras la carga histórica:

    python scripts/reconciliar_cierres.py            # solo revisión (no escribe)
    python scripts/reconciliar_cierres.py --aplicar  # cierra/reabre

Salida: una línea por decisión (proceso, estado, regla y título que la motiva)
y el resumen. Si alguna regla cierra un proceso que no debería, el título
aparece aquí y basta con ajustar app/ciclo_vida.py y añadir un caso a
tests/test_ciclo_vida.py. Con --aplicar, usa --historico para no generar avisos.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.reconciliar_cierres import reconciliar_cierres


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconciliación de cierres de convocatorias")
    parser.add_argument("--aplicar", action="store_true", help="Escribe los cierres; sin esto solo revisa")
    parser.add_argument("--historico", action="store_true", help="No genera avisos a usuarios")
    args = parser.parse_args()
    try:
        r = reconciliar_cierres(aplicar=args.aplicar, historico=args.historico, limite_detalle=None)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for d in r["detalle"]:
        print(f"{d['accion']:8} proceso={d['proceso_id']:<7} {str(d['estado_anterior'])}->{d['estado']:<10} "
              f"{d.get('regla') or '-':<28} {d.get('fecha_publicacion') or '':<10} {(d.get('titulo') or '')[:110]}")
    print(f"\nmodo={r['modo']} cierres={r['cierres']} {r['cierres_por_estado']} "
          f"reaperturas={r['reaperturas']} aplicadas={r['aplicadas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
