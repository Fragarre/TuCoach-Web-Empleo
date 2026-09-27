"""Auditoría conservadora de grupo, subgrupo y escala de oportunidades.

No infiere datos desde la denominación. Solo aprovecha literales explícitos de
documentos oficiales enlazados, y el modo aplicado nunca sustituye un dato ya
existente.
"""
from __future__ import annotations

from typing import Any

import httpx
from psycopg.rows import dict_row

from .bop_valencia import _obtener_texto
from .clasificacion_puesto import extraer_clasificacion_declarada
from .database import get_connection


def _urls_proceso(cursor: Any, proceso_id: int, datos_json: dict[str, Any] | None) -> list[tuple[str, str | None]]:
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
    urls_vistas = {existente[0] for existente in urls}
    for fila in cursor.fetchall():
        url, contenido = fila["url"], fila["contenido_texto"]
        if url not in urls_vistas:
            urls.append((url, contenido))
            urls_vistas.add(url)
    return urls


def revisar_clasificacion_puestos(
    *,
    aplicar: bool = False,
    limite: int | None = None,
    desde_id: int = 0,
    ordenar_por_reciente: bool = False,
) -> dict[str, Any]:
    """Revisa oportunidades con clasificación incompleta y deja trazabilidad."""
    with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.denominacion, p.grupo, p.subgrupo, p.cuerpo_escala, p.datos_json
            FROM procesos p
            WHERE p.es_oportunidad=TRUE
              AND p.ambito_administrativo='SI'
              AND (NULLIF(BTRIM(p.grupo),'') IS NULL
                   OR NULLIF(BTRIM(p.subgrupo),'') IS NULL
                   OR NULLIF(BTRIM(p.cuerpo_escala),'') IS NULL)
              AND p.id > %s
            ORDER BY
                CASE WHEN %s THEN p.updated_at END DESC NULLS LAST,
                p.id
            """,
            (desde_id, ordenar_por_reciente),
        )
        procesos = list(cursor.fetchall())
        if limite is not None:
            procesos = procesos[:limite]

        revisadas = clasificadas = sin_documento = errores_documento = 0
        detalles: list[dict[str, Any]] = []
        cabeceras = {"User-Agent": "NetReto-Empleo/1.0 (https://netexamenes.com)", "Accept-Language": "es-ES,ca;q=0.9"}
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), headers=cabeceras, follow_redirects=True) as cliente:
            for proceso in procesos:
                revisadas += 1
                clasificacion: dict[str, str | None] = {"grupo": None, "subgrupo": None, "cuerpo_escala": None}
                urls = _urls_proceso(cursor, proceso["id"], proceso["datos_json"])
                if not urls:
                    sin_documento += 1
                    continue
                url_evidencia: str | None = None
                for url, contenido in urls:
                    try:
                        texto = contenido or _obtener_texto(cliente, url)
                    except Exception:
                        errores_documento += 1
                        continue
                    encontrada = extraer_clasificacion_declarada(texto)
                    campos_nuevos = False
                    for campo, valor in encontrada.items():
                        if clasificacion[campo] is None and valor is not None:
                            clasificacion[campo] = valor
                            campos_nuevos = True
                    if campos_nuevos:
                        url_evidencia = url
                    if all(clasificacion.values()):
                        break

                cambios = {campo: valor for campo, valor in clasificacion.items() if valor is not None and not (proceso[campo] or "").strip()}
                if not cambios:
                    continue
                clasificadas += 1
                detalles.append({"proceso_id": proceso["id"], "denominacion": proceso["denominacion"], "campos": cambios, "url_evidencia": url_evidencia})
                if aplicar:
                    cursor.execute(
                        """
                        UPDATE procesos
                        SET grupo=COALESCE(NULLIF(BTRIM(grupo),''),%s),
                            subgrupo=COALESCE(NULLIF(BTRIM(subgrupo),''),%s),
                            cuerpo_escala=COALESCE(NULLIF(BTRIM(cuerpo_escala),''),%s),
                            updated_at=NOW()
                        WHERE id=%s
                        """,
                        (clasificacion["grupo"], clasificacion["subgrupo"], clasificacion["cuerpo_escala"], proceso["id"]),
                    )
        if aplicar:
            conexion.commit()
        else:
            conexion.rollback()
    ultimo_id_revisado = procesos[-1]["id"] if procesos else None
    return {"revisadas": revisadas, "clasificadas": clasificadas, "sin_documento": sin_documento, "errores_documento": errores_documento, "desde_id": desde_id, "orden_reciente": ordenar_por_reciente, "ultimo_id_revisado": ultimo_id_revisado, "modo": "APLICAR" if aplicar else "SOLO_REVISION", "detalles": detalles}
