from __future__ import annotations

import hashlib
import re

from . import bop_valencia as _bop
from .database import get_connection
from .ciclo_vida import ESTADOS_TERMINALES_SQL_IN
from .estado_proceso import clasificar_evento_terminal

_BASE_IMPORTAR_BOP = _bop.importar_bop_valencia


def _es_convocatoria_base(titulo: str, texto: str) -> bool:
    """Distingue la convocatoria base por el acto descrito en el título oficial."""
    titulo_n = _bop._sin(titulo)
    contenido_n = _bop._sin(titulo + " " + texto)

    posteriores = (
        "designacion de miembros", "designacio de membres",
        "designacion del organo", "designacio de l'organ",
        "designacion del tribunal", "designacio del tribunal",
        "modificacion del organo", "modificacio de l'organ",
        "composicion del organo", "composicio de l'organ",
        "relacion provisional", "relacio provisional",
        "relacion definitiva", "relacio definitiva",
        "lista provisional", "llista provisional",
        "lista definitiva", "llista definitiva",
        "personas admitidas", "persones admeses",
        "personas excluidas", "persones excloses",
        "fecha de examen", "data d'examen", "data de l'exercici",
        "primer ejercicio", "primer exercici",
        "calificaciones", "qualificacions", "resultados", "resultats",
        "nombramiento", "nomenament", "constitucion de bolsa", "constitucio de borsa",
    )
    if any(x in titulo_n for x in posteriores):
        return False

    bases = (
        "aprobacion de las bases", "aprovacio de les bases",
        "aprobacion de bases", "aprovacio de bases",
        "bases que han de regir", "bases especifiques", "bases especificas",
        "convocatoria para la seleccion", "convocatoria per a la seleccio",
        "convocatoria de la oposicion", "convocatoria de l'oposicio",
        "convocatoria del proceso selectivo", "convocatoria del proces selectiu",
    )
    return any(x in titulo_n for x in bases) or (
        not titulo_n.strip() and any(x in contenido_n for x in bases)
    )


def _familia_perfil(texto: str) -> str | None:
    """Familias inequívocas usadas solo para detectar colisiones de código."""
    n = _bop._sin(texto)
    patrones = (
        ("auxiliar_administrativo", ("auxiliar administratiu", "auxiliar administrativo")),
        ("administrativo", ("administratiu", "administrativo")),
        ("recaudacion", ("recaptacio", "recaudacion")),
        ("taller_imprenta", ("taller d'impremta", "taller de imprenta")),
        ("obras_publicas", ("obres publiques", "obras publicas")),
        ("ingenieria", ("enginyer", "ingenier")),
        ("educacion", ("professor", "profesor", "educacio", "educacion")),
    )
    for familia, terminos in patrones:
        if any(t in n for t in terminos):
            return familia
    return None


def _identificador_estable(titulo: str, texto: str) -> str:
    """Usa el código de convocatoria, separando colisiones reales de perfiles."""
    contenido = titulo + " " + texto
    convocatoria = _bop._convocatoria(contenido)
    if not convocatoria:
        return "DVAL:T:" + hashlib.sha256(_bop._sin(titulo).encode("utf-8")).hexdigest()[:24]

    base = f"DVAL:{convocatoria}"
    familia_nueva = _familia_perfil(contenido)
    if not familia_nueva:
        return base

    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT denominacion FROM procesos WHERE identificador_estable=%s LIMIT 1", (base,))
            row = cursor.fetchone()
    except Exception:
        return base

    if not row:
        return base
    familia_existente = _familia_perfil(row[0] or "")
    if not familia_existente or familia_existente == familia_nueva:
        return base

    sufijo = hashlib.sha256(familia_nueva.encode("utf-8")).hexdigest()[:8]
    return f"{base}:{sufijo}"


def _recalcular_ultima_publicacion() -> int:
    """Mantiene ultima_publicacion_at igual a la fecha máxima realmente persistida."""
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE procesos p
            SET ultima_publicacion_at = u.ultima_publicacion_at,
                updated_at = NOW()
            FROM (
                SELECT proceso_id,
                       MAX(fecha_publicacion)::timestamp AT TIME ZONE 'UTC' AS ultima_publicacion_at
                FROM publicaciones
                WHERE fecha_publicacion IS NOT NULL
                GROUP BY proceso_id
            ) u
            WHERE p.id = u.proceso_id
              AND p.identificador_estable LIKE 'DVAL:%%'
              AND p.ultima_publicacion_at IS DISTINCT FROM u.ultima_publicacion_at
            RETURNING p.id
            """
        )
        corregidos = len(cursor.fetchall())
        connection.commit()
    return corregidos


def _postprocesar_estado_terminal() -> int:
    """Cierra procesos solo con evidencia oficial terminal en el título."""
    finalizados = 0
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.estado, p.tipo_proceso, pub.id, pub.titulo
            FROM procesos p
            JOIN LATERAL (
                SELECT id,titulo,fecha_publicacion
                FROM publicaciones
                WHERE proceso_id=p.id
                ORDER BY fecha_publicacion DESC NULLS LAST,id DESC
            ) pub ON TRUE
            WHERE p.identificador_estable LIKE 'DVAL:%%'
              AND COALESCE(LOWER(p.estado),'') NOT IN """ + ESTADOS_TERMINALES_SQL_IN + """
            ORDER BY p.id, pub.fecha_publicacion DESC NULLS LAST, pub.id DESC
            """
        )
        terminal_por_proceso: dict[int, tuple[str, int, str]] = {}
        for proceso_id, estado_anterior, tipo_proceso, publicacion_id, titulo in cursor.fetchall():
            if proceso_id in terminal_por_proceso:
                continue
            estado_nuevo = clasificar_evento_terminal(tipo_proceso, titulo)
            if estado_nuevo:
                terminal_por_proceso[proceso_id] = (estado_anterior, publicacion_id, estado_nuevo)

        for proceso_id, (estado_anterior, publicacion_id, estado_nuevo) in terminal_por_proceso.items():
            cursor.execute(
                "UPDATE procesos SET estado=%s,updated_at=NOW() WHERE id=%s AND COALESCE(LOWER(estado),'') NOT IN " + ESTADOS_TERMINALES_SQL_IN,
                (estado_nuevo, proceso_id),
            )
            if not cursor.rowcount:
                continue
            cursor.execute(
                """
                INSERT INTO cambios (proceso_id,publicacion_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo)
                VALUES (%s,%s,'ACTUALIZACION','estado',%s,%s,'Proceso selectivo resuelto según publicación oficial',TRUE)
                """,
                (proceso_id, publicacion_id, estado_anterior, estado_nuevo),
            )
            finalizados += 1
        connection.commit()
    return finalizados


def _importar_con_integridad(historico: bool = False, dias: int = 1):
    stats = _BASE_IMPORTAR_BOP(historico=historico, dias=dias)
    stats["ultima_publicacion_recalculada"] = _recalcular_ultima_publicacion()
    stats["procesos_finalizados"] = _postprocesar_estado_terminal()
    return stats


def aplicar_integridad_bop() -> None:
    _bop._es_convocatoria_base = _es_convocatoria_base
    _bop._tipo_publicacion = lambda titulo, texto: "CONVOCATORIA" if _es_convocatoria_base(titulo, texto) else "BOP"
    _bop._identificador_estable = _identificador_estable
    _bop.importar_bop_valencia = _importar_con_integridad
