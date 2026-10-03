from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from psycopg.rows import dict_row


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.boe_local_import import recuperar_boe_para_proceso_bop, diagnosticar_eventos_boe_local
from app.database import get_connection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Previsualiza, sin escribir, el matching BOE de un proceso BOP."
    )
    parser.add_argument("proceso_id", type=int, nargs="?", help="ID interno del proceso BOP")
    parser.add_argument("--eventos", action="store_true", help="Diagnostica eventos BOE en solo lectura")
    parser.add_argument(
        "--hasta",
        help="Fecha final YYYY-MM-DD; por defecto usa la fecha actual",
    )
    args = parser.parse_args()

    if args.eventos:
        from datetime import date
        hasta = date.fromisoformat(args.hasta) if args.hasta else date.today()
        print(json.dumps(diagnosticar_eventos_boe_local(hasta=hasta, dias=30), ensure_ascii=False, indent=2, default=str))
        return 0

    if args.proceso_id is None:
        parser.error("proceso_id es obligatorio salvo con --eventos")

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, fecha_convocatoria, estado
            FROM procesos
            WHERE id=%s
            """,
            (args.proceso_id,),
        )
        proceso = cursor.fetchone()
        connection.rollback()

    if not proceso:
        print(json.dumps({"estado": "PROCESO_NO_ENCONTRADO", "proceso_id": args.proceso_id}, indent=2))
        return 2

    if not proceso.get("fecha_convocatoria"):
        print(
            json.dumps(
                {
                    "estado": "SIN_FECHA_CONVOCATORIA",
                    "proceso": dict(proceso),
                    "mensaje": "No se puede hacer matching BOE estricto sin fecha de bases.",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 3

    hasta = None
    if args.hasta:
        from datetime import date
        hasta = date.fromisoformat(args.hasta)

    resultado = recuperar_boe_para_proceso_bop(
        proceso_id=args.proceso_id,
        fecha_bases=proceso["fecha_convocatoria"],
        hasta=hasta,
        aplicar=False,
    )

    print(
        json.dumps(
            {"proceso": dict(proceso), "resultado": resultado},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
