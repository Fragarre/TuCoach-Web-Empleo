"""Generación idempotente de eventos generales de nuevas oportunidades.

No resuelve destinatarios ni envía correo. El criterio de visibilidad reutiliza
exactamente el mismo predicado que el catálogo público de Empleo.
"""

from __future__ import annotations

import os
from html import escape
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from access import PRIVATE_EMPLOYMENT_USER_IDS, es_categoria_empleo_privada, puede_acceder_categoria_privada
from .database import get_connection
from .email_sender import enviar_email
from .procesos import _condiciones_catalogo


TIPO_NUEVA_OPORTUNIDAD = "NUEVA_OPORTUNIDAD"
ESTADOS_COMERCIALES = ("active", "trialing", "past_due")
EMAIL_PRUEBA = "fragarre@outlook.es"


def destinatarios_generales() -> list[tuple[UUID, str]]:
    """Usuarios comerciales activos más la excepción explícita de prueba."""
    database_url = os.getenv("TUCOACH_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TUCOACH_DATABASE_URL no está configurada")

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT DISTINCT p.id AS user_id, p.email
                FROM public.profiles p
                JOIN public.subscriptions s ON s.user_id = p.id
                WHERE p.activo = TRUE
                  AND p.email IS NOT NULL
                  AND TRIM(p.email) <> ''
                  AND s.proveedor = 'STRIPE'
                  AND s.status = ANY(%s)
                UNION
                SELECT p.id AS user_id, p.email
                FROM public.profiles p
                WHERE p.activo = TRUE
                  AND (LOWER(TRIM(COALESCE(p.email, ''))) = %s OR p.id = ANY(%s::uuid[]))
                """,
                (list(ESTADOS_COMERCIALES), EMAIL_PRUEBA,
                 [str(u) for u in PRIVATE_EMPLOYMENT_USER_IDS]),
            )
            return [(row["user_id"], str(row["email"]).strip()) for row in cursor.fetchall()]


def preparar_envios_eventos(evento_ids: Iterable[int]) -> int:
    """Crea una sola fila PENDIENTE por evento y usuario destinatario."""
    eventos = sorted({int(evento_id) for evento_id in evento_ids})
    if not eventos:
        return 0

    destinatarios = destinatarios_generales()
    if not destinatarios:
        return 0

    creados = 0
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT e.id AS evento_id, p.tipo_proceso, p.datos_json->>'categoria_gva' AS categoria_gva
            FROM empleo_eventos_notificacion e
            JOIN procesos p ON p.id = e.proceso_id
            WHERE e.id = ANY(%s)
            """,
            (eventos,),
        )
        categorias = {int(row["evento_id"]): row for row in cursor.fetchall()}
        for evento_id in eventos:
            proceso = categorias.get(evento_id)
            if proceso is None:
                continue
            privada = es_categoria_empleo_privada(
                proceso["tipo_proceso"],
                proceso["categoria_gva"],
            )
            for user_id, email in destinatarios:
                if privada and not puede_acceder_categoria_privada(user_id):
                    continue
                cursor.execute(
                    """
                    INSERT INTO empleo_envios_notificacion
                        (evento_id, user_id, email, estado)
                    VALUES (%s, %s, %s, 'PENDIENTE')
                    ON CONFLICT (evento_id, user_id) DO NOTHING
                    """,
                    (evento_id, user_id, email),
                )
                creados += cursor.rowcount
        connection.commit()
    return creados


def ids_oportunidades_visibles() -> set[int]:
    """Devuelve los ids que cumplen ahora mismo el filtro del catálogo público."""
    catalogo_sql, params = _condiciones_catalogo()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT p.id
            FROM procesos p
            JOIN organismos o ON o.id = p.organismo_id
            WHERE {catalogo_sql}
            """,
            tuple(params),
        )
        return {int(row[0]) for row in cursor.fetchall()}


def filtrar_nuevas_oportunidades_notificables(proceso_ids: Iterable[int]) -> set[int]:
    """Excluye solo ADC no accionables del aviso general de alta.

    Los ADC se mantienen visibles en Empleo. Esta función afecta únicamente a
    la creación del evento NUEVA_OPORTUNIDAD.
    """
    ids = sorted({int(proceso_id) for proceso_id in proceso_ids})
    if not ids:
        return set()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM procesos
            WHERE id = ANY(%s)
              AND NOT (
                    tipo_proceso = 'Anuncio difícil cobertura (ADC)'
                    AND COALESCE((datos_json->>'accionable')::boolean, FALSE) = FALSE
                  )
            """,
            (ids,),
        )
        return {int(row[0]) for row in cursor.fetchall()}


def registrar_nuevas_oportunidades(proceso_ids: Iterable[int]) -> list[int]:
    """Crea como máximo un evento NUEVA_OPORTUNIDAD por proceso.

    Devuelve únicamente los ids de evento creados en esta llamada.
    """
    ids = sorted({int(proceso_id) for proceso_id in proceso_ids})
    if not ids:
        return []

    creados: list[int] = []
    with get_connection() as connection, connection.cursor() as cursor:
        for proceso_id in ids:
            cursor.execute(
                """
                INSERT INTO empleo_eventos_notificacion (proceso_id, tipo)
                VALUES (%s, %s)
                ON CONFLICT (proceso_id, tipo) DO NOTHING
                RETURNING id
                """,
                (proceso_id, TIPO_NUEVA_OPORTUNIDAD),
            )
            row = cursor.fetchone()
            if row is not None:
                creados.append(int(row[0]))
        connection.commit()
    return creados

MAX_ITEMS_RESUMEN = 40


def _datos_oportunidad(fila: dict) -> list[tuple[str, object]]:
    """Datos mostrados de una oportunidad en el correo."""
    datos_json = fila.get("datos_json") or {}
    es_adc = fila.get("tipo_proceso") == "Anuncio difícil cobertura (ADC)"
    datos = [
        ("Organismo", fila["organismo"]),
        ("Provincia", fila["provincia"]),
        ("Plazas", fila["plazas"]),
        ("Sistema selectivo", fila["sistema_selectivo"]),
        ("Fecha de convocatoria", fila["fecha_convocatoria"]),
        ("Apertura del plazo", fila["fecha_apertura"]),
        ("Cierre del plazo", fila["fecha_cierre"]),
    ]
    if es_adc:
        bolsas = datos_json.get("bolsas_relacionadas") or []
        datos.extend([
            ("ADC", datos_json.get("numero_adc")),
            ("Cuerpo", fila.get("cuerpo_escala")),
            ("Etapa actual", datos_json.get("etapa_actual_gva")),
            ("Bolsas relacionadas", ", ".join(str(x) for x in bolsas) if bolsas else None),
        ])
    return [(e, v) for e, v in datos if v is not None and str(v).strip()]


def componer_resumen(filas: list[dict], public_app_url: str) -> tuple[str, str, str]:
    """Asunto, texto y html de UN correo con todas las oportunidades nuevas."""
    total = len(filas)
    asunto = (
        f"Nueva oportunidad de empleo: {filas[0]['denominacion']}"
        if total == 1
        else f"{total} nuevas oportunidades de empleo"
    )
    intro = (
        "Se ha publicado una nueva oportunidad de empleo."
        if total == 1
        else f"Se han publicado {total} nuevas oportunidades de empleo."
    )
    texto = [intro, ""]
    html = [f"<p>{escape(intro)}</p>"]
    for fila in filas[:MAX_ITEMS_RESUMEN]:
        url = f"{public_app_url}/empleo/proceso/{int(fila['proceso_id'])}"
        denominacion = str(fila["denominacion"])
        datos = _datos_oportunidad(fila)
        es_adc = fila.get("tipo_proceso") == "Anuncio difícil cobertura (ADC)"
        url_oficial = (fila.get("datos_json") or {}).get("url_detalle") if es_adc else None
        texto += [denominacion, *[f"{e}: {v}" for e, v in datos], f"Ver convocatoria: {url}"]
        if url_oficial:
            texto.append(f"Acceso oficial GVA: {url_oficial}")
        texto.append("")
        items = "".join(
            f"<li><strong>{escape(e)}:</strong> {escape(str(v))}</li>" for e, v in datos
        )
        html.append(
            f"<p><strong>{escape(denominacion)}</strong></p><ul>{items}</ul>"
            f'<p><a href="{escape(url)}">Ver convocatoria</a></p>'
            + (f'<p><a href="{escape(str(url_oficial))}">Acceso oficial GVA</a></p>' if url_oficial else "")
        )
    if total > MAX_ITEMS_RESUMEN:
        resto = total - MAX_ITEMS_RESUMEN
        enlace = f"{public_app_url}/empleo"
        texto.append(f"... y {resto} más: {enlace}")
        html.append(f'<p>... y {resto} más: <a href="{escape(enlace)}">ver todas en Empleo</a></p>')
    return asunto, "\n".join(texto), "".join(html)


def enviar_envios_pendientes(*, limite: int = 2000) -> dict[str, int]:
    """Envía un único correo por usuario con todas sus oportunidades pendientes."""
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
                en.id,
                en.user_id,
                en.email,
                e.proceso_id,
                p.denominacion,
                p.cuerpo_escala,
                p.plazas,
                p.sistema_selectivo,
                p.fecha_convocatoria,
                p.fecha_apertura,
                p.fecha_cierre,
                p.tipo_proceso,
                p.datos_json,
                o.nombre AS organismo,
                o.provincia
            FROM empleo_envios_notificacion en
            JOIN empleo_eventos_notificacion e ON e.id = en.evento_id
            JOIN procesos p ON p.id = e.proceso_id
            JOIN organismos o ON o.id = p.organismo_id
            WHERE en.estado = 'PENDIENTE'
              AND en.created_at > NOW() - INTERVAL '3 days'
              AND e.tipo = %s
            ORDER BY en.created_at, en.id
            LIMIT %s
            """,
            (TIPO_NUEVA_OPORTUNIDAD, limite),
        )
        pendientes = list(cursor.fetchall())

    if not pendientes:
        return {"procesadas": 0, "enviadas": 0, "errores": 0, "correos": 0}

    public_app_url = os.getenv("PUBLIC_APP_URL", "https://netexamenes.com").rstrip("/")
    grupos: dict[tuple[str, str], list[dict]] = {}
    for fila in pendientes:
        grupos.setdefault((str(fila["user_id"]), str(fila["email"])), []).append(fila)

    enviadas = errores = correos = 0
    for (_user, email), filas in grupos.items():
        ids = [int(f["id"]) for f in filas]
        asunto, texto, html = componer_resumen(filas, public_app_url)
        try:
            resultado = enviar_email(destinatario=email, asunto=asunto, html=html, texto=texto)
            with get_connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE empleo_envios_notificacion
                    SET estado = 'ENVIADA', proveedor_message_id = %s,
                        enviado_at = NOW(), error = NULL, updated_at = NOW()
                    WHERE id = ANY(%s) AND estado = 'PENDIENTE'
                    """,
                    (resultado.message_id, ids),
                )
                connection.commit()
            enviadas += len(ids)
            correos += 1
        except Exception as exc:
            with get_connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE empleo_envios_notificacion
                    SET error = %s, updated_at = NOW()
                    WHERE id = ANY(%s) AND estado = 'PENDIENTE'
                    """,
                    (f"{type(exc).__name__}: {exc}"[:2000], ids),
                )
                connection.commit()
            errores += len(ids)

    return {"procesadas": len(pendientes), "enviadas": enviadas, "errores": errores, "correos": correos}
