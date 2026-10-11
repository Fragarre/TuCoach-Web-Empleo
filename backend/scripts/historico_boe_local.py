"""Carga histórica del BOE local (Valencia, Alicante, Castellón) por tramos.

Pensado para ejecutarse UNA vez (o tras una parada larga del CRON) y recuperar
las convocatorias que salieron antes de la ventana ordinaria de 7 días.

    python scripts/historico_boe_local.py --dias 400            # simulación
    python scripts/historico_boe_local.py --dias 400 --aplicar  # escribe

Por defecto SOLO REVISA (no escribe nada). Trabaja en tramos de 30 días, con una
pausa entre tramos, y el BOE se consulta con su API pública (sin proxy, sin
coste). Con --salida guarda además un CSV con las convocatorias nuevas.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.boe_local_import import previsualizar_importacion_boe_local  # noqa: E402
from app.festivos import hoy_es  # noqa: E402

TRAMO = 30
PAUSA_ENTRE_TRAMOS = 3.0

CAMPOS_RESUMEN = (
    "convocatorias_extraidas", "nuevas", "insertados", "existentes_boe",
    "absorbidas_bop_revision", "posibles_existentes_bop", "vinculadas_bop",
    "revision_solapamiento", "excluidas_turno_interno", "fuera_alcance_provincia",
    "eventos_documentales_revision", "dias_con_error",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga histórica del BOE local por tramos")
    parser.add_argument("--dias", type=int, default=400, help="Días hacia atrás desde hoy")
    parser.add_argument("--aplicar", action="store_true", help="Escribe; sin esto solo revisa")
    parser.add_argument("--salida", default="historico_boe_nuevas.csv", help="CSV con las convocatorias nuevas")
    args = parser.parse_args()
    if args.dias < 1 or args.dias > 800:
        print("--dias debe estar entre 1 y 800", file=sys.stderr)
        return 2

    hoy = hoy_es()
    # Del tramo más antiguo al más reciente, para que las absorciones por fecha
    # se comporten igual que en una ejecución diaria.
    fin = hoy - timedelta(days=args.dias - 1)
    tramos: list[tuple[date, int]] = []
    cursor = fin
    while cursor <= hoy:
        hasta = min(cursor + timedelta(days=TRAMO - 1), hoy)
        tramos.append((hasta, (hasta - cursor).days + 1))
        cursor = hasta + timedelta(days=1)

    totales = {c: 0 for c in CAMPOS_RESUMEN}
    filas: list[dict[str, str]] = []
    errores_totales = 0
    print(f"modo={'APLICAR' if args.aplicar else 'SOLO_REVISION'} tramos={len(tramos)} desde={fin} hasta={hoy}")

    for i, (hasta, dias) in enumerate(tramos, start=1):
        r = previsualizar_importacion_boe_local(hasta=hasta, dias=dias, aplicar=args.aplicar)
        parcial = {}
        for c in CAMPOS_RESUMEN:
            v = r.get(c, 0)
            v = len(v) if isinstance(v, list) else (v or 0)
            parcial[c] = v
            totales[c] += v
        errores_totales += len(r.get("errores") or [])
        print(f"[{i}/{len(tramos)}] {r.get('desde')}..{r.get('hasta')} "
              + " ".join(f"{k}={v}" for k, v in parcial.items() if v))
        for e in (r.get("errores") or [])[:3]:
            print(f"   ERROR {e}")
        for d in r.get("detalle") or []:
            if d.get("estado_importacion") in ("NUEVA", "INSERTADA"):
                filas.append({
                    "fecha_boe": str(d.get("fecha_boe") or ""),
                    "entidad": str(d.get("entidad") or ""),
                    "denominacion": str(d.get("denominacion") or ""),
                    "boe_id": str(d.get("boe_id") or ""),
                    "plazo": str(d.get("plazo_solicitudes_literal") or "")[:90],
                    "estado": str(d.get("estado_importacion") or ""),
                })
        if i < len(tramos):
            time.sleep(PAUSA_ENTRE_TRAMOS)

    if filas:
        with open(args.salida, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(filas)
        print(f"\nGuardado {args.salida} con {len(filas)} filas")
    print("\nTOTALES " + " ".join(f"{k}={v}" for k, v in totales.items() if v))
    print(f"errores_totales={errores_totales}")
    return 0 if errores_totales == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
