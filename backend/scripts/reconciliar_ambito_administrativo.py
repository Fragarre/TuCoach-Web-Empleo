"""Reclasifica de forma conservadora la visibilidad del catálogo administrativo.

Por defecto solo audita. En modo --aplicar conserva procesos y publicaciones:
únicamente retira del catálogo visible los registros que el clasificador actual
determina explícitamente como NO administrativos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from psycopg.rows import dict_row

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ambito_administrativo import clasificar_ambito_administrativo
from app.database import get_connection


def reconciliar(*, aplicar: bool = False, proceso_ids: list[int] | None = None) -> dict:
    with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
        params: list[object] = []
        filtro = ""
        if proceso_ids:
            filtro = " AND p.id = ANY(%s)"
            params.append(proceso_ids)
        cursor.execute(
            f"""
            SELECT p.id,p.identificador_estable,p.denominacion,p.cuerpo_escala,p.grupo
            FROM procesos p
            WHERE p.es_oportunidad=TRUE
              AND p.ambito_administrativo='SI'
              {filtro}
            ORDER BY p.id
            """,
            params,
        )
        retirar = []
        for p in cursor.fetchall():
            clasificacion = clasificar_ambito_administrativo(dict(p))
            if clasificacion == "NO":
                retirar.append({
                    "id": p["id"],
                    "identificador_estable": p["identificador_estable"],
                    "denominacion": p["denominacion"],
                })

        if aplicar:
            for p in retirar:
                cursor.execute(
                    """
                    UPDATE procesos
                    SET es_oportunidad=FALSE, ambito_administrativo='NO', updated_at=NOW()
                    WHERE id=%s AND es_oportunidad=TRUE AND ambito_administrativo='SI'
                    """,
                    (p["id"],),
                )
            conexion.commit()
        else:
            conexion.rollback()

    return {
        "modo": "APLICAR" if aplicar else "SOLO_REVISION",
        "retirar": retirar,
        "total_retirar": len(retirar),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--proceso-id", type=int, action="append", dest="proceso_ids")
    args = parser.parse_args()
    print(json.dumps(reconciliar(aplicar=args.aplicar, proceso_ids=args.proceso_ids), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
