from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import get_connection
from .estado_proceso import clasificar_evento_terminal
from .gva_estatal_persist import _resolver_identidad_gva
from .gva_estatal_seguimiento import (
    ESTADOS_TERMINALES,
    _obtener_html,
    _referencia_estatal,
    extraer_seguimientos_validos,
)
from .gva_estatal_source import nuevo_cliente


def _cargar_bolsas_activas() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, tipo_proceso, estado, datos_json
            FROM procesos
            WHERE organismo_id=1
              AND es_oportunidad=TRUE
              AND ambito_administrativo='SI'
              AND LOWER(COALESCE(tipo_proceso,'')) LIKE '%bolsa%'
            ORDER BY id
            """
        )
        filas = list(cursor.fetchall())

    salida: list[dict[str, Any]] = []
    for fila in filas:
        if str(fila.get("estado") or "").strip().lower() in ESTADOS_TERMINALES:
            continue
        datos = fila.get("datos_json") or {}
        ref = _referencia_estatal(datos)
        id_emp = datos.get("id_emp") or datos.get("codigo_gva") or datos.get("codigo_gva_resuelto")
        try:
            id_emp = int(id_emp) if id_emp is not None else None
        except (TypeError, ValueError):
            id_emp = None
        if ref is None and id_emp is None:
            continue
        item = dict(fila)
        item["referencia_estatal"] = ref
        item["id_emp"] = id_emp
        salida.append(item)
    return salida


def _planificar(procesos: list[dict[str, Any]], resultados: dict[int, dict[str, Any]]) -> dict[str, Any]:
    acciones: list[dict[str, Any]] = []
    for proceso in procesos:
        proceso_id = int(proceso["id"])
        referencia = proceso.get("referencia_estatal")
        id_emp = proceso.get("id_emp")
        extraido = resultados[proceso_id]
        datos = proceso.get("datos_json") or {}
        estado = datos.get("seguimiento_gva") if isinstance(datos.get("seguimiento_gva"), dict) else None
        actuales = {str(x["signatura"]) for x in extraido.get("validos") or []}

        base = {
            "proceso_id": proceso_id,
            "identificador_estable": proceso.get("identificador_estable"),
            "tipo_proceso": proceso.get("tipo_proceso"),
            "referencia_estatal": int(referencia) if referencia is not None else None,
            "id_emp": int(id_emp) if id_emp is not None else None,
            "rechazados": extraido.get("rechazados") or [],
        }

        if not estado or not estado.get("inicializado"):
            acciones.append({**base, "accion": "BASELINE", "vistos": sorted(actuales)})
            continue

        vistos = {str(x) for x in (estado.get("vistos") or [])}
        nuevos = [x for x in (extraido.get("validos") or []) if str(x["signatura"]) not in vistos]
        if not nuevos:
            acciones.append({**base, "accion": "SIN_CAMBIOS"})
            continue

        acciones.append({
            **base,
            "accion": "PUBLICAR",
            "nuevos": nuevos,
            "vistos": sorted(vistos | actuales),
        })

    return {
        "acciones": acciones,
        "resumen": {
            "procesos": len(procesos),
            "baseline": sum(a["accion"] == "BASELINE" for a in acciones),
            "sin_cambios": sum(a["accion"] == "SIN_CAMBIOS" for a in acciones),
            "procesos_con_novedades": sum(a["accion"] == "PUBLICAR" for a in acciones),
            "publicaciones_nuevas": sum(len(a.get("nuevos") or []) for a in acciones),
            "seguimientos_rechazados": sum(len(a.get("rechazados") or []) for a in acciones),
        },
    }


def _guardar_estado(cursor, proceso_id: int, referencia_estatal: int, vistos: list[str]) -> None:
    estado = {
        "version": 1,
        "inicializado": True,
        "fuente": "administracion.gob.es",
        "referencia_estatal": referencia_estatal,
        "vistos": vistos,
    }
    cursor.execute(
        """
        UPDATE procesos
        SET datos_json = COALESCE(datos_json, '{}'::jsonb) || %s,
            updated_at = NOW()
        WHERE id=%s
        """,
        (Jsonb({"seguimiento_gva": estado}), proceso_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo guardar el seguimiento de la bolsa GVA {proceso_id}")


def _insertar_publicacion(
    cursor,
    *,
    fuente_dogv_id: int,
    proceso_id: int,
    referencia_estatal: int,
    item: dict[str, Any],
) -> int | None:
    referencia = f"GVAESTATAL:{referencia_estatal}:SEGUIMIENTO:{item['signatura']}"
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id, fuente_id, referencia, tipo, titulo,
            fecha_publicacion, url, datos_json, detectada_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (fuente_id, referencia, url) DO NOTHING
        RETURNING id
        """,
        (
            proceso_id,
            fuente_dogv_id,
            referencia,
            item.get("tipo") or "SEGUIMIENTO_OFICIAL",
            item.get("titulo"),
            item.get("fecha_publicacion"),
            item["url"],
            Jsonb({
                "origen": "DOGV",
                "descubierta_via": "administracion.gob.es",
                "referencia_estatal": referencia_estatal,
                "signatura": item["signatura"],
                "coincidencias_identidad": item.get("coincidencias_identidad") or [],
            }),
        ),
    )
    fila = cursor.fetchone()
    return int(fila["id"]) if fila else None


def actualizar_bolsas_gva_simplificadas(*, aplicar: bool = False) -> dict[str, Any]:
    """Seguimiento conservador de bolsas GVA mediante la ficha estatal.

    Mantiene la línea base silenciosa histórica. Solo publica enlaces DOGV nuevos
    que comparten identificadores fuertes con la disposición primaria de la ficha.
    """
    procesos = _cargar_bolsas_activas()
    resultados: dict[int, dict[str, Any]] = {}
    with nuevo_cliente() as client:
        for proceso in procesos:
            referencia = proceso.get("referencia_estatal")
            if referencia is not None:
                html = _obtener_html(client, int(referencia))
                resultados[int(proceso["id"])] = extraer_seguimientos_validos(html)
                continue

            # Las bolsas descubiertas directamente en sede.gva.es no tienen
            # referencia estatal. De momento se incorporan al plan sin
            # inventar una referencia; su seguimiento directo se implementa
            # separadamente antes de habilitar persistencia.
            resultados[int(proceso["id"])] = {
                "validos": [],
                "rechazados": [],
                "fuente": "sede.gva.es",
            }

    plan = _planificar(procesos, resultados)
    if not aplicar:
        return {"modo": "SOLO_REVISION", "escrituras_bd": False, **plan}

    publicaciones_creadas = 0
    baseline_creados = 0
    procesos_finalizados = 0
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        _, fuente_dogv_id = _resolver_identidad_gva(cursor)
        for accion in plan["acciones"]:
            if accion["accion"] == "SIN_CAMBIOS":
                continue
            if accion["accion"] == "BASELINE":
                if accion.get("referencia_estatal") is None:
                    # No se escribe un baseline estatal falso para bolsas GVA directas.
                    continue
                _guardar_estado(
                    cursor,
                    int(accion["proceso_id"]),
                    int(accion["referencia_estatal"]),
                    list(accion["vistos"]),
                )
                baseline_creados += 1
                continue
            if accion["accion"] != "PUBLICAR":
                raise RuntimeError(f"Acción inesperada en bolsa GVA: {accion['accion']}")

            fechas: list[str] = []
            for item in accion.get("nuevos") or []:
                publicacion_id = _insertar_publicacion(
                    cursor,
                    fuente_dogv_id=fuente_dogv_id,
                    proceso_id=int(accion["proceso_id"]),
                    referencia_estatal=int(accion["referencia_estatal"]),
                    item=item,
                )
                if publicacion_id is None:
                    continue
                publicaciones_creadas += 1
                if item.get("fecha_publicacion"):
                    fechas.append(str(item["fecha_publicacion"]))

                estado_terminal = clasificar_evento_terminal(
                    accion.get("tipo_proceso"),
                    item.get("titulo"),
                )
                if estado_terminal:
                    cursor.execute("SELECT estado FROM procesos WHERE id=%s", (int(accion["proceso_id"]),))
                    fila = cursor.fetchone()
                    anterior = fila.get("estado") if fila else None
                    if str(anterior or "").upper() not in {"FINALIZADO", "DESISTIDO", "ANULADO", "CANCELADO"}:
                        cursor.execute(
                            "UPDATE procesos SET estado=%s,updated_at=NOW() WHERE id=%s",
                            (estado_terminal, int(accion["proceso_id"])),
                        )
                        cursor.execute(
                            """
                            INSERT INTO cambios (
                                proceso_id,publicacion_id,tipo,campo,
                                valor_anterior,valor_nuevo,resumen,significativo
                            ) VALUES (%s,%s,'ACTUALIZACION','estado',%s,%s,%s,TRUE)
                            """,
                            (
                                int(accion["proceso_id"]),
                                publicacion_id,
                                anterior,
                                estado_terminal,
                                f"Proceso selectivo cerrado por publicación oficial: {estado_terminal}",
                            ),
                        )
                        procesos_finalizados += 1

            _guardar_estado(
                cursor,
                int(accion["proceso_id"]),
                int(accion["referencia_estatal"]),
                list(accion["vistos"]),
            )
            if fechas:
                fecha_max = max(fechas)
                cursor.execute(
                    """
                    UPDATE procesos
                    SET ultima_publicacion_at = GREATEST(
                        COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                        %s::date::timestamptz
                    ), updated_at=NOW()
                    WHERE id=%s
                    """,
                    (fecha_max, fecha_max, int(accion["proceso_id"])),
                )

    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        **plan,
        "baseline_creados": baseline_creados,
        "publicaciones_creadas": publicaciones_creadas,
        "procesos_finalizados": procesos_finalizados,
    }
