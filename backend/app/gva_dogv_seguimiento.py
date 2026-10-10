from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .festivos import hoy_es
from .database import get_connection
from .estado_proceso import clasificar_evento_terminal
from .ciclo_vida import es_estado_terminal
from .gva_bolsas_seguimiento import actualizar_bolsas_gva_simplificadas
from .gva_dogv_diagnostico import (
    DOGV_API,
    _descubrir_dogv_en_fechas,
    _fecha_url_dogv,
    limpiar_cache_dogv,
)
from .gva_estatal_persist import _resolver_identidad_gva
from .gva_estatal_seguimiento import (
    ESTADOS_TERMINALES,
    _obtener_html,
    _referencia_estatal,
    _tipo_seguimiento,
    _tokens_identidad,
    extraer_seguimientos_validos,
)
from .gva_estatal_source import nuevo_cliente, nuevo_cliente_dogv


VERSION_ESTADO_DOGV = 2
SOLAPE_DIAS = 3


def _cargar_procesos_activos() -> list[dict[str, Any]]:
    """Carga solo oportunidades GVA activas con referencia estatal conocida."""
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.identificador_estable, p.denominacion, p.tipo_proceso,
                   p.fecha_convocatoria, p.fecha_apertura, p.fecha_cierre,
                   p.estado, p.datos_json
            FROM procesos p
            JOIN organismos o ON o.id = p.organismo_id
            WHERE LOWER(TRIM(o.nombre)) = 'generalitat valenciana'
              AND p.es_oportunidad=TRUE
              AND p.ambito_administrativo='SI'
            ORDER BY p.id
            """
        )
        filas = list(cursor.fetchall())

    salida: list[dict[str, Any]] = []
    for fila in filas:
        if str(fila.get("estado") or "").strip().lower() in ESTADOS_TERMINALES:
            continue
        referencia = _referencia_estatal(fila.get("datos_json"))
        if referencia is None:
            continue
        item = dict(fila)
        item["referencia_estatal"] = referencia
        salida.append(item)
    return salida


def _es_bolsa(proceso: dict[str, Any]) -> bool:
    return "bolsa" in str(proceso.get("tipo_proceso") or "").lower()


def _estado_actual(proceso: dict[str, Any]) -> dict[str, Any]:
    datos = proceso.get("datos_json") or {}
    estado = datos.get("seguimiento_gva")
    return dict(estado) if isinstance(estado, dict) else {}


def _identidad_y_semillas_migracion(
    estatal: httpx.Client,
    proceso: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Obtiene una vez la identidad fuerte y fechas pista del portal estatal.

    El portal estatal se usa solo durante la migración de estado. La aceptación
    de documentos la realiza siempre DOGV por identificadores fuertes exactos.
    """
    html = _obtener_html(estatal, int(proceso["referencia_estatal"]))
    extraido = extraer_seguimientos_validos(html)
    identidad = set(extraido.get("identidad") or [])
    identidad |= _tokens_identidad(str(proceso.get("denominacion") or ""))

    fechas: set[str] = set()
    for item in list(extraido.get("validos") or []) + list(extraido.get("rechazados") or []):
        fecha = _fecha_url_dogv(item.get("url")) or item.get("fecha_publicacion")
        if fecha:
            fechas.add(str(fecha))
    return identidad, fechas


def _identidad_estado_o_denominacion(proceso: dict[str, Any], estado: dict[str, Any]) -> set[str]:
    identidad = {str(x) for x in (estado.get("identidad") or []) if str(x)}
    if not identidad:
        identidad = _tokens_identidad(str(proceso.get("denominacion") or ""))
    return identidad


def _fechas_revision(estado: dict[str, Any], hoy: date) -> set[str]:
    valor = estado.get("revisado_hasta")
    try:
        ultima = date.fromisoformat(str(valor))
    except (TypeError, ValueError):
        ultima = hoy - timedelta(days=SOLAPE_DIAS)
    inicio = min(ultima, hoy) - timedelta(days=SOLAPE_DIAS)
    return {
        (inicio + timedelta(days=i)).isoformat()
        for i in range((hoy - inicio).days + 1)
    }


def _url_pdf(item: dict[str, Any]) -> str:
    fecha = date.fromisoformat(str(item["fecha_publicacion"]))
    signatura = str(item["signatura"])
    return (
        f"https://dogv.gva.es/datos/{fecha.year:04d}/{fecha.month:02d}/{fecha.day:02d}"
        f"/pdf/{signatura}_es.pdf"
    )


def _preparar_item_publicacion(item: dict[str, Any]) -> dict[str, Any]:
    salida = dict(item)
    salida["url"] = _url_pdf(item)
    salida["tipo"] = _tipo_seguimiento(str(item.get("titulo") or ""))
    return salida


def _planificar(*, solo_oportunidades: bool = False) -> dict[str, Any]:
    limpiar_cache_dogv()
    procesos = _cargar_procesos_activos()
    hoy = hoy_es()
    acciones: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []

    with nuevo_cliente() as estatal, nuevo_cliente_dogv() as dogv:
        for proceso in procesos:
            estado = _estado_actual(proceso)
            base = {
                "proceso_id": int(proceso["id"]),
                "identificador_estable": proceso.get("identificador_estable"),
                "denominacion": proceso.get("denominacion"),
                "tipo_proceso": proceso.get("tipo_proceso"),
                "referencia_estatal": int(proceso["referencia_estatal"]),
            }

            if _es_bolsa(proceso):
                if solo_oportunidades:
                    continue
                acciones.append({
                    **base,
                    "accion": "BOLSA_SEGUIMIENTO_SIMPLIFICADO",
                    "estado_proceso": proceso.get("estado"),
                    "url_detalle": (proceso.get("datos_json") or {}).get("url_detalle"),
                })
                continue

            if int(estado.get("version") or 0) < VERSION_ESTADO_DOGV or estado.get("fuente") != "DOGV_DIRECTO":
                try:
                    identidad, semillas = _identidad_y_semillas_migracion(estatal, proceso)
                    descubiertos = _descubrir_dogv_en_fechas(
                        dogv,
                        identidad=identidad,
                        fechas_semilla=semillas,
                    )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    errores.append({
                        "proceso_id": int(proceso["id"]),
                        "referencia_estatal": int(proceso["referencia_estatal"]),
                        "fase": "migracion_baseline",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                descubiertos = [x for x in descubiertos if not x.get("error")]
                propuestos = sorted({str(x["signatura"]) for x in descubiertos})
                anteriores = sorted({str(x) for x in (estado.get("vistos") or [])})
                acciones.append({
                    **base,
                    "accion": "MIGRAR_BASELINE_DOGV",
                    "identidad": sorted(identidad),
                    "vistos_anteriores": anteriores,
                    "vistos_propuestos": propuestos,
                    "eliminar_del_estado": sorted(set(anteriores) - set(propuestos)),
                    "incorporar_al_baseline": sorted(set(propuestos) - set(anteriores)),
                    "novedades": [],
                    "revisado_hasta": hoy.isoformat(),
                })
                continue

            identidad = _identidad_estado_o_denominacion(proceso, estado)
            fechas = _fechas_revision(estado, hoy)
            try:
                descubiertos = _descubrir_dogv_en_fechas(
                    dogv,
                    identidad=identidad,
                    fechas_semilla=fechas,
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                errores.append({
                    "proceso_id": int(proceso["id"]),
                    "referencia_estatal": int(proceso["referencia_estatal"]),
                    "fase": "revision_dogv",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            descubiertos = [x for x in descubiertos if not x.get("error")]
            vistos = {str(x) for x in (estado.get("vistos") or [])}
            nuevos = [
                _preparar_item_publicacion(x)
                for x in descubiertos
                if str(x["signatura"]) not in vistos
            ]
            vistos_propuestos = sorted(vistos | {str(x["signatura"]) for x in descubiertos})
            acciones.append({
                **base,
                "accion": "PUBLICAR" if nuevos else "SIN_CAMBIOS",
                "identidad": sorted(identidad),
                "vistos_anteriores": sorted(vistos),
                "vistos_propuestos": vistos_propuestos,
                "novedades": nuevos,
                "revisado_hasta": hoy.isoformat(),
            })

    return {
        "fuente": DOGV_API,
        "version_estado": VERSION_ESTADO_DOGV,
        "acciones": acciones,
        "errores": errores,
        "resumen": {
            "procesos": len(acciones),
            "convocatorias": sum(a["accion"] != "BOLSA_SEGUIMIENTO_SIMPLIFICADO" for a in acciones),
            "bolsas_simplificadas": sum(a["accion"] == "BOLSA_SEGUIMIENTO_SIMPLIFICADO" for a in acciones),
            "migraciones_baseline": sum(a["accion"] == "MIGRAR_BASELINE_DOGV" for a in acciones),
            "procesos_con_novedades": sum(a["accion"] == "PUBLICAR" for a in acciones),
            "publicaciones_nuevas": sum(len(a.get("novedades") or []) for a in acciones),
        },
    }


def _guardar_estado(
    cursor,
    *,
    proceso_id: int,
    referencia_estatal: int,
    identidad: list[str],
    vistos: list[str],
    revisado_hasta: str,
) -> None:
    estado = {
        "version": VERSION_ESTADO_DOGV,
        "inicializado": True,
        "fuente": "DOGV_DIRECTO",
        "referencia_estatal": referencia_estatal,
        "identidad": identidad,
        "vistos": vistos,
        "revisado_hasta": revisado_hasta,
    }
    cursor.execute(
        """
        UPDATE procesos
        SET datos_json = COALESCE(datos_json, '{}'::jsonb) || %s
        WHERE id=%s
        """,
        (Jsonb({"seguimiento_gva": estado}), proceso_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"No se pudo guardar seguimiento DOGV del proceso {proceso_id}")


def _insertar_publicacion(
    cursor,
    *,
    fuente_dogv_id: int,
    proceso_id: int,
    referencia_estatal: int,
    item: dict[str, Any],
) -> int | None:
    referencia = f"GVADOGV:{referencia_estatal}:{item['signatura']}"
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
                "descubierta_via": "DOGV_DIRECTO",
                "referencia_estatal": referencia_estatal,
                "signatura": item["signatura"],
                "id_dogv": item.get("id_dogv"),
                "cve": item.get("cve"),
                "coincidencias_identidad": item.get("coincidencias_identidad") or [],
            }),
        ),
    )
    fila = cursor.fetchone()
    return int(fila["id"]) if fila else None


def actualizar_seguimientos_gva_dogv(*, aplicar: bool = False, solo_oportunidades: bool = False) -> dict[str, Any]:
    """Seguimiento GVA por DOGV estructurado.

    La primera ejecución migra el baseline de cada convocatoria de forma
    silenciosa. Las bolsas quedan en seguimiento simplificado. A partir de la
    versión 2 del estado, solo se publican documentos DOGV nuevos que coinciden
    por identidad fuerte exacta.
    """
    plan = _planificar(solo_oportunidades=solo_oportunidades)
    if not aplicar:
        if solo_oportunidades:
            return {"modo": "SOLO_REVISION", "escrituras_bd": False, **plan, "bolsas": None}
        bolsas = actualizar_bolsas_gva_simplificadas(aplicar=False)
        return {"modo": "SOLO_REVISION", "escrituras_bd": False, **plan, "bolsas": bolsas}

    publicaciones_creadas = 0
    migraciones_aplicadas = 0
    procesos_finalizados = 0
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        _, fuente_dogv_id = _resolver_identidad_gva(cursor)
        for accion in plan["acciones"]:
            tipo = accion["accion"]
            if tipo == "BOLSA_SEGUIMIENTO_SIMPLIFICADO":
                continue

            if tipo == "MIGRAR_BASELINE_DOGV":
                _guardar_estado(
                    cursor,
                    proceso_id=int(accion["proceso_id"]),
                    referencia_estatal=int(accion["referencia_estatal"]),
                    identidad=list(accion["identidad"]),
                    vistos=list(accion["vistos_propuestos"]),
                    revisado_hasta=str(accion["revisado_hasta"]),
                )
                migraciones_aplicadas += 1
                continue

            if tipo not in {"SIN_CAMBIOS", "PUBLICAR"}:
                raise RuntimeError(f"Acción DOGV inesperada: {tipo}")

            fechas_creadas: list[str] = []
            for item in accion.get("novedades") or []:
                publicacion_id = _insertar_publicacion(
                    cursor,
                    fuente_dogv_id=fuente_dogv_id,
                    proceso_id=int(accion["proceso_id"]),
                    referencia_estatal=int(accion["referencia_estatal"]),
                    item=item,
                )
                if publicacion_id is not None:
                    publicaciones_creadas += 1
                    if item.get("fecha_publicacion"):
                        fechas_creadas.append(str(item["fecha_publicacion"]))
                    estado_terminal = clasificar_evento_terminal(
                        accion.get("tipo_proceso"),
                        item.get("titulo"),
                    )
                    if estado_terminal:
                        cursor.execute("SELECT estado FROM procesos WHERE id=%s", (int(accion["proceso_id"]),))
                        fila_estado = cursor.fetchone()
                        estado_anterior = fila_estado.get("estado") if fila_estado else None
                        if not es_estado_terminal(estado_anterior):
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
                                    estado_anterior,
                                    estado_terminal,
                                    "Proceso selectivo resuelto según publicación oficial",
                                ),
                            )
                            procesos_finalizados += 1

            _guardar_estado(
                cursor,
                proceso_id=int(accion["proceso_id"]),
                referencia_estatal=int(accion["referencia_estatal"]),
                identidad=list(accion["identidad"]),
                vistos=list(accion["vistos_propuestos"]),
                revisado_hasta=str(accion["revisado_hasta"]),
            )

            if fechas_creadas:
                fecha_max = max(fechas_creadas)
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

    bolsas = None if solo_oportunidades else actualizar_bolsas_gva_simplificadas(aplicar=True)
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        **plan,
        "migraciones_aplicadas": migraciones_aplicadas,
        "publicaciones_creadas": publicaciones_creadas,
        "procesos_finalizados": procesos_finalizados,
        "bolsas": bolsas,
    }
