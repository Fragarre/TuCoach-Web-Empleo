from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from psycopg.rows import dict_row


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.boe_local_extractor import extraer_convocatorias_boe_local
from app.boe_local_import import recuperar_boe_para_proceso_bop
from app.database import get_connection


def _ventanas(desde: date, hasta: date, dias: int):
    actual = desde
    while actual <= hasta:
        fin = min(actual + timedelta(days=dias - 1), hasta)
        yield actual, fin
        actual = fin + timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconciliación histórica BOE de procesos BOP activos. Previsualiza por defecto."
    )
    parser.add_argument("--proceso-id", type=int)
    parser.add_argument("--hasta", default=date.today().isoformat())
    parser.add_argument("--ventana-dias", type=int, default=60)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    hasta = date.fromisoformat(args.hasta)

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        params: list[object] = []
        filtro_id = ""
        if args.proceso_id is not None:
            filtro_id = " AND p.id=%s"
            params.append(args.proceso_id)
        cursor.execute(
            f"""
            SELECT p.id,p.identificador_estable,
                   COALESCE(
                       p.fecha_convocatoria,
                       (SELECT MIN(pub.fecha_publicacion)
                        FROM publicaciones pub
                        JOIN fuentes fb ON fb.id=pub.fuente_id
                        WHERE pub.proceso_id=p.id AND fb.tipo='BOP'
                          AND UPPER(COALESCE(pub.tipo,'')) IN ('BASES','CONVOCATORIA','BOP')
                          AND (LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'
                               OR COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'))
                   ) AS fecha_bases,
                   p.denominacion
            FROM procesos p
            WHERE p.es_oportunidad=TRUE
              AND p.ambito_administrativo='SI'
              AND p.estado NOT IN ('FINALIZADO','ANULADO','DESISTIDO')
              AND (
                    p.datos_json->>'origen' IN (
                        'BOP_VALENCIA','BOP_VALENCIA_MUNICIPAL','BOP_CASTELLON',
                        'BOP_ALICANTE','DIPUTACION_ALICANTE_OTRAS'
                    )
                    OR p.identificador_estable LIKE 'DVAL:%'
                    OR p.identificador_estable LIKE 'AVAL:%'
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM publicaciones pub
                    JOIN fuentes f ON f.id=pub.fuente_id
                    WHERE pub.proceso_id=p.id AND f.tipo='BOE'
                  )
              AND COALESCE(
                    p.fecha_convocatoria,
                    (SELECT MIN(pub.fecha_publicacion)
                     FROM publicaciones pub
                     JOIN fuentes fb ON fb.id=pub.fuente_id
                     WHERE pub.proceso_id=p.id AND fb.tipo='BOP'
                       AND UPPER(COALESCE(pub.tipo,'')) IN ('BASES','CONVOCATORIA','BOP')
                          AND (LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'
                               OR COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'))
                  ) IS NOT NULL
              {filtro_id}
            ORDER BY fecha_bases,p.id
            """,
            params,
        )
        procesos = list(cursor.fetchall())
        connection.rollback()

    detalle = []
    for proceso in procesos:
        fecha_bases = proceso["fecha_bases"]
        encontrados = []
        errores = []
        for inicio, fin in _ventanas(fecha_bases, hasta, max(1, args.ventana_dias)):
            extraccion = extraer_convocatorias_boe_local(
                hasta=fin,
                dias=(fin - inicio).days + 1,
            )
            resultado = recuperar_boe_para_proceso_bop(
                proceso_id=proceso["id"],
                fecha_bases=fecha_bases,
                hasta=fin,
                max_dias=(fin - fecha_bases).days + 1,
                aplicar=args.aplicar,
                extraccion_boe=extraccion,
            )
            errores.extend(extraccion.get("errores") or [])
            if resultado.get("estado") not in ("SIN_COINCIDENCIA", "FUERA_RANGO"):
                encontrados.append({"ventana": [inicio.isoformat(), fin.isoformat()], **resultado})
                if resultado.get("estado") in ("VINCULADA", "AGREGADO_ACTUALIZADO", "AGREGADO_SIN_CAMBIOS"):
                    break
        detalle.append({
            "proceso_id": proceso["id"],
            "identificador_estable": proceso["identificador_estable"],
            "fecha_bases": fecha_bases.isoformat(),
            "coincidencias": encontrados,
            "errores": errores,
        })

    print(json.dumps({
        "modo": "APLICADO" if args.aplicar else "SOLO_REVISION",
        "procesos": len(procesos),
        "detalle": detalle,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
