"""Completa grupo, subgrupo y escala desde documentos oficiales ya vinculados.

La ejecución por defecto es de solo revisión. En modo aplicar nunca reemplaza
datos existentes y únicamente persiste clasificaciones declaradas explícitamente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bop_valencia import _obtener_texto
from app.clasificacion_puesto import extraer_clasificacion_declarada
from app.database import get_connection


def _urls_proceso(cursor, proceso_id: int, datos_json: dict | None) -> list[tuple[str, str | None]]:
    urls: list[tuple[str, str | None]] = []
    for clave in ("url_detalle", "url_oficial", "url_convocatoria", "url_ultima_publicacion"):
        url = (datos_json or {}).get(clave)
        if isinstance(url, str) and url.strip():
            urls.append((url.strip(), None))
    cursor.execute(
        """
        SELECT url, contenido_texto FROM publicaciones
        WHERE proceso_id=%s AND url IS NOT NULL AND TRIM(url) <> ''
        ORDER BY CASE WHEN UPPER(COALESCE(tipo,'')) IN ('BASES','CONVOCATORIA','BOP','BOE') THEN 0 ELSE 1 END,
                 fecha_publicacion ASC NULLS LAST, id ASC
        """,
        (proceso_id,),
    )
    for fila in cursor.fetchall():
        url, contenido = fila["url"], fila["contenido_texto"]
        if url not in {existente[0] for existente in urls}:
            urls.append((url, contenido))
    return urls


def revisar_clasificacion_puestos(*, aplicar: bool = False, limite: int | None = None) -> dict[str, int | str]:
    """Audita todas las oportunidades visibles con algún dato de puesto pendiente."""
    with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.denominacion, p.grupo, p.subgrupo, p.cuerpo_escala, p.datos_json
            FROM procesos p
            WHERE p.es_oportunidad=TRUE
              AND p.ambito_administrativo='SI'
              AND (p.grupo IS NULL OR p.subgrupo IS NULL OR p.cuerpo_escala IS NULL)
            ORDER BY p.id
            """
        )
        procesos = list(cursor.fetchall())
        if limite is not None:
            procesos = procesos[:limite]

        revisadas = clasificadas = sin_documento = errores_documento = 0
        cabeceras = {"User-Agent": "NetReto-Empleo/1.0 (https://netexamenes.com)", "Accept-Language": "es-ES,ca;q=0.9"}
        with httpx.Client(timeout=45, headers=cabeceras, follow_redirects=True) as cliente:
            for proceso in procesos:
                revisadas += 1
                clasificacion = {"grupo": None, "subgrupo": None, "cuerpo_escala": None}
                urls = _urls_proceso(cursor, proceso["id"], proceso["datos_json"])
                if not urls:
                    sin_documento += 1
                    continue
                for url, contenido in urls:
                    try:
                        texto = contenido or _obtener_texto(cliente, url)
                    except Exception:
                        errores_documento += 1
                        continue
                    encontrada = extraer_clasificacion_declarada(texto)
                    for campo, valor in encontrada.items():
                        if clasificacion[campo] is None and valor is not None:
                            clasificacion[campo] = valor
                    if all(clasificacion.values()):
                        break

                cambios = {
                    campo: valor for campo, valor in clasificacion.items()
                    if valor is not None and proceso[campo] is None
                }
                if not cambios:
                    continue
                clasificadas += 1
                print(f"{proceso['id']} | {cambios} | {proceso['denominacion']}")
                if aplicar:
                    cursor.execute(
                        """
                        UPDATE procesos
                        SET grupo=COALESCE(grupo,%s), subgrupo=COALESCE(subgrupo,%s),
                            cuerpo_escala=COALESCE(cuerpo_escala,%s), updated_at=NOW()
                        WHERE id=%s
                        """,
                        (clasificacion["grupo"], clasificacion["subgrupo"], clasificacion["cuerpo_escala"], proceso["id"]),
                    )
        if aplicar:
            conexion.commit()
        else:
            conexion.rollback()
    return {"revisadas": revisadas, "clasificadas": clasificadas, "sin_documento": sin_documento, "errores_documento": errores_documento, "modo": "APLICAR" if aplicar else "SOLO_REVISION"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--limite", type=int)
    args = parser.parse_args()
    print(revisar_clasificacion_puestos(aplicar=args.aplicar, limite=args.limite))
