import os
from html import escape

import psycopg
from psycopg.rows import dict_row

from typing import Any
from uuid import UUID
from datetime import datetime

from access import PRIVATE_EMPLOYMENT_USER_IDS
from .database import get_connection
from .email_sender import enviar_email


CAMPOS_CAMBIO_RELEVANTES = (
    "fecha_apertura",
    "fecha_cierre",
    "fecha_examen",
    "lugar_examen",
    "estado",
    "plazas",
    "turno",
    "etapa_actual",
    "tipo_proceso",
    "url_oficial",
)


def suscripciones_usuario(user_id: UUID) -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.proceso_id, s.activa, s.created_at, s.updated_at,
                       p.identificador_estable, p.denominacion, p.organismo_id,
                       o.nombre AS organismo_nombre, p.tipo_proceso, p.plazas,
                       p.estado, p.anio_convocatoria, p.fecha_apertura,
                       p.fecha_cierre, p.fecha_examen, p.ultima_publicacion_at,
                       COALESCE(
                           NULLIF(TRIM(COALESCE(p.datos_json->>'url_detalle','')), ''),
                           NULLIF(TRIM(COALESCE(p.datos_json->>'url_oficial','')), ''),
                           (
                               SELECT pub.url
                               FROM publicaciones pub
                               WHERE pub.proceso_id = p.id
                                 AND pub.url IS NOT NULL
                                 AND TRIM(pub.url) <> ''
                               ORDER BY
                                 CASE
                                   WHEN UPPER(TRIM(COALESCE(pub.tipo, ''))) = 'BASES' THEN 0
                                   WHEN UPPER(TRIM(COALESCE(pub.tipo, ''))) = 'CONVOCATORIA' THEN 1
                                   WHEN LOWER(COALESCE(pub.tipo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 2
                                   WHEN LOWER(COALESCE(pub.titulo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 3
                                   WHEN LOWER(pub.url) LIKE '%%bop.dival.es%%' THEN 4
                                   WHEN LOWER(pub.url) LIKE '%%boe.es%%' THEN 6
                                   ELSE 5
                                 END,
                                 pub.fecha_publicacion ASC NULLS LAST,
                                 pub.id ASC
                               LIMIT 1
                           )
                       ) AS url_oficial
                FROM suscripciones s
                JOIN procesos p ON p.id = s.proceso_id
                JOIN organismos o ON o.id = p.organismo_id
                WHERE s.user_id = %s
                  AND s.activa = TRUE
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                ORDER BY s.created_at DESC, s.id DESC
                """,
                (str(user_id),),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def suscripcion_usuario_proceso(user_id: UUID, proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.proceso_id, s.activa, s.created_at, s.updated_at
                FROM suscripciones s
                JOIN procesos p ON p.id = s.proceso_id
                WHERE s.user_id = %s
                  AND s.proceso_id = %s
                  AND p.es_oportunidad = TRUE
                  AND p.ambito_administrativo = 'SI'
                """,
                (str(user_id), proceso_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description.name for description in cursor.description]
    return dict(zip(columns, row))


def suscribirse(user_id: UUID, proceso_id: int) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT es_oportunidad, ambito_administrativo FROM procesos WHERE id = %s",
                (proceso_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Proceso no encontrado")
            if not row[0] or row[1] != "SI":
                raise ValueError("El proceso no está disponible como convocatoria administrativa")

            cursor.execute(
                """
                INSERT INTO suscripciones (user_id, proceso_id, activa)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (user_id, proceso_id)
                DO UPDATE SET activa = TRUE, updated_at = now()
                RETURNING id, proceso_id, activa, created_at, updated_at
                """,
                (str(user_id), proceso_id),
            )
            row = cursor.fetchone()
            connection.commit()
            columns = [description.name for description in cursor.description]
    return dict(zip(columns, row))


def cancelar_suscripcion(user_id: UUID, proceso_id: int) -> bool:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE suscripciones
                SET activa = FALSE, updated_at = now()
                WHERE user_id = %s AND proceso_id = %s AND activa = TRUE
                RETURNING id
                """,
                (str(user_id), proceso_id),
            )
            changed = cursor.fetchone() is not None
            connection.commit()
    return changed


def cambios_usuario(user_id: UUID, *, limite: int = 100) -> list[dict[str, Any]]:
    """Devuelve únicamente novedades sustantivas de convocatorias seguidas.

    Se excluyen cambios técnicos de captura o normalización (por ejemplo,
    denominación, navegación y enriquecimiento inicial) aunque hayan quedado
    registrados históricamente como significativos.
    """
    limite = max(1, min(limite, 200))
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM (
                    SELECT
                        pub.id AS id,
                        pub.proceso_id,
                        p.identificador_estable,
                        p.denominacion,
                        o.nombre AS organismo_nombre,
                        'PUBLICACION'::text AS novedad_tipo,
                        pub.tipo,
                        NULL::text AS campo,
                        pub.titulo AS resumen,
                        pub.fecha_publicacion::timestamptz AS detectado_at,
                        TRUE AS significativo,
                        pub.url
                    FROM publicaciones pub
                    JOIN suscripciones s ON s.proceso_id = pub.proceso_id
                    JOIN procesos p ON p.id = pub.proceso_id
                    JOIN organismos o ON o.id = p.organismo_id
                    WHERE s.user_id = %s
                      AND s.activa = TRUE
                      AND p.es_oportunidad = TRUE
                      AND p.ambito_administrativo = 'SI'
                      AND COALESCE(LOWER(pub.tipo), '') NOT IN ('navegacion', 'navegación')
                      AND COALESCE(LOWER(pub.titulo), '') <> 'navegación'

                    UNION ALL

                    SELECT
                        c.id AS id,
                        c.proceso_id,
                        p.identificador_estable,
                        p.denominacion,
                        o.nombre AS organismo_nombre,
                        'CAMBIO'::text AS novedad_tipo,
                        c.tipo,
                        c.campo,
                        c.resumen,
                        c.detectado_at,
                        c.significativo,
                        pub.url
                    FROM cambios c
                    JOIN suscripciones s ON s.proceso_id = c.proceso_id
                    JOIN procesos p ON p.id = c.proceso_id
                    JOIN organismos o ON o.id = p.organismo_id
                    LEFT JOIN publicaciones pub ON pub.id = c.publicacion_id
                    WHERE s.user_id = %s
                      AND s.activa = TRUE
                      AND p.es_oportunidad = TRUE
                      AND p.ambito_administrativo = 'SI'
                      AND c.significativo = TRUE
                      AND c.valor_anterior IS NOT NULL
                      AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                      AND LOWER(COALESCE(c.valor_anterior, '')) <> 'navegación'
                      AND LOWER(COALESCE(c.valor_anterior, '')) <> 'navegacion'
                ) novedades
                ORDER BY detectado_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (str(user_id), str(user_id), list(CAMPOS_CAMBIO_RELEVANTES), limite),
            )
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def estado_novedades_usuario(user_id: UUID) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ultima_novedad_vista_at, updated_at
                FROM seguimiento_estado_usuario
                WHERE user_id = %s
                """,
                (str(user_id),),
            )
            row = cursor.fetchone()
            if row is None:
                return {"ultima_novedad_vista_at": None, "updated_at": None}
            return {"ultima_novedad_vista_at": row[0], "updated_at": row[1]}


def marcar_novedades_vistas(user_id: UUID, hasta: datetime | None) -> dict[str, Any]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO seguimiento_estado_usuario
                    (user_id, ultima_novedad_vista_at, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    ultima_novedad_vista_at = CASE
                        WHEN seguimiento_estado_usuario.ultima_novedad_vista_at IS NULL THEN EXCLUDED.ultima_novedad_vista_at
                        WHEN EXCLUDED.ultima_novedad_vista_at IS NULL THEN seguimiento_estado_usuario.ultima_novedad_vista_at
                        WHEN EXCLUDED.ultima_novedad_vista_at > seguimiento_estado_usuario.ultima_novedad_vista_at THEN EXCLUDED.ultima_novedad_vista_at
                        ELSE seguimiento_estado_usuario.ultima_novedad_vista_at
                    END,
                    updated_at = now()
                RETURNING ultima_novedad_vista_at, updated_at
                """,
                (str(user_id), hasta),
            )
            row = cursor.fetchone()
            connection.commit()
    return {"ultima_novedad_vista_at": row[0], "updated_at": row[1]}


def ids_novedades_seguimiento() -> set[tuple[str, int]]:
    """Fotografía las filas que pueden originar novedades de seguimiento."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 'PUBLICACION'::text, pub.id
                FROM publicaciones pub

                UNION ALL

                SELECT 'CAMBIO'::text, c.id
                FROM cambios c
                """
            )
            return {(str(tipo), int(novedad_id)) for tipo, novedad_id in cursor.fetchall()}


def usuarios_con_novedades_nuevas(
    novedades_antes: set[tuple[str, int]],
) -> set[UUID]:
    """Devuelve usuarios con novedades nuevas respetando el acceso privado."""
    nuevas = ids_novedades_seguimiento() - novedades_antes
    if not nuevas:
        return set()

    publicaciones = [novedad_id for tipo, novedad_id in nuevas if tipo == "PUBLICACION"]
    cambios = [novedad_id for tipo, novedad_id in nuevas if tipo == "CAMBIO"]
    usuarios: set[UUID] = set()

    def admitir_filas(filas: list[tuple[Any, ...]]) -> None:
        for user_id_raw, tipo_proceso, categoria_gva in filas:
            user_id = UUID(str(user_id_raw))
            tipo = str(tipo_proceso or "").strip().lower()
            categoria = str(categoria_gva or "").strip().upper()
            privado = tipo in {
                "bolsa de trabajo",
                "difícil cobertura",
                "anuncio difícil cobertura",
                "anuncio difícil cobertura (adc)",
            } or categoria in {"BOLSA", "ADC"}
            if not privado or user_id in PRIVATE_EMPLOYMENT_USER_IDS:
                usuarios.add(user_id)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            if publicaciones:
                cursor.execute(
                    """
                    SELECT DISTINCT s.user_id, p.tipo_proceso,
                           p.datos_json->>'categoria_gva'
                    FROM publicaciones pub
                    JOIN procesos p ON p.id = pub.proceso_id
                    JOIN suscripciones s ON s.proceso_id = pub.proceso_id
                    WHERE pub.id = ANY(%s)
                      AND s.activa = TRUE
                    """,
                    (publicaciones,),
                )
                admitir_filas(cursor.fetchall())

            if cambios:
                cursor.execute(
                    """
                    SELECT DISTINCT s.user_id, p.tipo_proceso,
                           p.datos_json->>'categoria_gva'
                    FROM cambios c
                    JOIN procesos p ON p.id = c.proceso_id
                    JOIN suscripciones s ON s.proceso_id = c.proceso_id
                    WHERE c.id = ANY(%s)
                      AND s.activa = TRUE
                      AND c.significativo = TRUE
                      AND c.valor_anterior IS NOT NULL
                      AND LOWER(COALESCE(c.campo, '')) = ANY(%s)
                    """,
                    (cambios, list(CAMPOS_CAMBIO_RELEVANTES)),
                )
                admitir_filas(cursor.fetchall())

    return usuarios


def emails_usuarios(user_ids: set[UUID]) -> dict[UUID, str]:
    """Resuelve emails de perfiles activos en la base central de Tu Coach."""
    if not user_ids:
        return {}

    database_url = os.getenv("TUCOACH_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TUCOACH_DATABASE_URL no está configurada")

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, email
                FROM public.profiles
                WHERE id = ANY(%s)
                  AND activo = TRUE
                  AND email IS NOT NULL
                  AND TRIM(email) <> ''
                """,
                (list(user_ids),),
            )
            return {
                row["id"]: str(row["email"]).strip()
                for row in cursor.fetchall()
            }


def enviar_avisos_novedades(
    novedades_antes: set[tuple[str, int]],
) -> dict[str, int]:
    """Envía un único correo por usuario con novedades nuevas en este ciclo."""
    usuarios = usuarios_con_novedades_nuevas(novedades_antes)

    if not usuarios:
        return {"usuarios_con_novedades": 0, "enviados": 0, "errores": 0}

    emails = emails_usuarios(usuarios)
    public_app_url = os.getenv(
        "PUBLIC_APP_URL",
        "https://netexamenes.com",
    ).strip().rstrip("/")
    url = f"{public_app_url}/empleo/seguimiento"

    enviados = 0
    errores = 0

    for user_id in usuarios:
        email = emails.get(user_id)
        try:
            if not email:
                raise RuntimeError("No se encontró email activo para el usuario")

            asunto = "Tu Coach — Novedades en tus convocatorias"
            texto = (
                "Hay novedades en una o varias de las oportunidades que sigues.\n\n"
                f"Consúltalas aquí: {url}"
            )
            html = (
                "<p>Hay novedades en una o varias de las oportunidades que sigues.</p>"
                f'<p><a href="{escape(url, quote=True)}">Consultar mis novedades</a></p>'
            )

            enviar_email(
                destinatario=email,
                asunto=asunto,
                html=html,
                texto=texto,
            )
            enviados += 1
        except Exception:
            errores += 1

    return {
        "usuarios_con_novedades": len(usuarios),
        "enviados": enviados,
        "errores": errores,
    }

