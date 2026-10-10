from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import gva_clean as base
from .database import get_connection
from .ambito_administrativo import clasificar_ambito_administrativo
from .ciclo_vida import ESTADOS_TERMINALES_SQL_IN

_BASE_PARSEAR_DETALLE = base.parsear_detalle
_BASE_IMPORTAR_GVA_ROBUSTO = base.importar_gva_robusto
_BASE_DESCUBRIR_DETALLES = base.descubrir_detalles
_BASE_TIPO_CONVOCATORIA = base._tipo_convocatoria
_BASE_TURNO = base._turno
_BASE_ES_INCLUIDO = base._es_incluido

TIPOS_INCLUIDOS = {
    "oposicion", "concurso-oposicion", "concurso oposicion",
    "bolsa de trabajo", "bolsa de empleo", "proceso selectivo",
    "seleccion", "selección",
}

PATRONES_BOLSA_NO_OPORTUNIDAD = (
    "consulta de baremo y posicion", "consulta de estados voluntarios",
    "consulta de estado voluntario", "inscripcion en las listas extraordinarias",
)

CAMPOS_NOVEDAD_GVA = (
    "fecha_apertura", "fecha_cierre", "fecha_examen", "lugar_examen",
    "estado", "plazas", "turno", "etapa_actual", "tipo_proceso", "url_oficial",
)

_ULTIMO_ERROR_DESCUBRIMIENTO_GVA: str | None = None


def _sin(s: str) -> str:
    return base._sin_acentos(s or "")


def _tipo_convocatoria(texto: str) -> str | None:
    n = _sin(texto).lower()
    if any(p in n for p in PATRONES_BOLSA_NO_OPORTUNIDAD):
        return "Consulta administrativa"
    return _BASE_TIPO_CONVOCATORIA(texto)


def _turno(texto: str) -> str | None:
    return _BASE_TURNO(texto)


def _es_incluido(tipo: str | None) -> bool:
    n = _sin(tipo or "").lower()
    if any(p in n for p in PATRONES_BOLSA_NO_OPORTUNIDAD):
        return False
    return _BASE_ES_INCLUIDO(tipo)


def _resolver_organismo(proceso: dict[str, Any]) -> tuple[int | None, str | None, str | None]:
    texto = " ".join(str(proceso.get(k) or "") for k in ("denominacion", "organismo", "organismo_texto", "datos_json"))
    n = _sin(texto).lower()
    if "generalitat valenciana" in n or "conselleria" in n:
        return base.GVA_ORGANISMO_ID, "generalitat_valenciana", None
    organismo_enlace = proceso.get("organismo_enlace")
    if organismo_enlace:
        host = urlparse(str(organismo_enlace)).netloc.lower()
        if host.endswith("gva.es"):
            return base.GVA_ORGANISMO_ID, "generalitat_valenciana", organismo_enlace
    return base.GVA_ORGANISMO_ID, "generalitat_valenciana", organismo_enlace


def _etapa_actual(texto: str) -> str | None:
    m = re.search(
        r"Etapa actual\s*:\s*(.+?)(?=\s+C[oó]digo SIA\s*:|\s+C[oó]digo GVA\s*:|\s+Descargar informaci[oó]n\b)",
        texto, re.I,
    )
    return base._normalizar(m.group(1)) if m else None


def _estado_plazo_solicitud(texto: str) -> str | None:
    cabecera = texto.split("Información básica", 1)[0].split("Informació bàsica", 1)[0]
    n = _sin(cabecera)
    if "plazo abierto" in n:
        return "ABIERTO"
    if "plazo cerrado" in n:
        return "CERRADO"
    if "plazo pendiente" in n:
        return "PENDIENTE"
    return None


def _es_etapa_solicitud(etapa: str | None) -> bool:
    """Identifica solo etapas cuyo plazo corresponde a presentar solicitudes."""
    n = _sin(etapa or "")
    return any(
        patron in n
        for patron in (
            "bases y apertura de plazo",
            "bases i obertura de termini",
            "apertura de plazo",
            "obertura de termini",
            "presentacion de solicitudes",
            "presentacio de sollicituds",
        )
    )


def _estado_ciclo_selectivo(texto: str) -> str:
    """Estado del proceso selectivo, independiente del plazo de inscripción."""
    etapa = _sin(_etapa_actual(texto) or "").strip()
    if not etapa:
        return "EN_CURSO"
    terminales = (
        "finalizacion del proceso selectivo", "finalitzacio del proces selectiu",
        "toma de posesion", "presa de possessio",
        "adjudicacion de destinos y fecha de cese/toma de posesion",
        "adjudicacio de destinacions",
    )
    if any(x in etapa for x in terminales):
        return "FINALIZADO"
    if etapa.startswith("nombramiento") and "tribunal" not in etapa:
        return "FINALIZADO"
    if etapa.startswith("nomenament") and "tribunal" not in etapa:
        return "FINALIZADO"
    if any(x in etapa for x in ("desistimiento", "desistiment", "anulacion", "anul·lacio")):
        return "FINALIZADO"
    return "EN_CURSO"


def _es_del_ambito_activo(_: dict[str, Any]) -> bool:
    """La antigüedad no excluye una oportunidad que todavía se sigue oficialmente."""
    return True


def _detalles_existentes_a_seguir() -> list[tuple[int, str]]:
    """Recupera fichas GVA ya conocidas aunque su plazo de solicitud esté cerrado."""
    resultado: list[tuple[int, str]] = []
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT identificador_estable, datos_json
            FROM procesos
            WHERE organismo_id=%s
              AND es_oportunidad=TRUE
              AND ambito_administrativo IN ('SI','REVISION')
              AND COALESCE(LOWER(estado),'') NOT IN """ + ESTADOS_TERMINALES_SQL_IN + """
              AND identificador_estable LIKE 'GVA:%%'
            ORDER BY id
            """, (base.GVA_ORGANISMO_ID,),
        )
        for identificador, datos in cursor.fetchall():
            datos = datos or {}
            id_emp = datos.get("id_emp")
            if id_emp is None:
                m = re.fullmatch(r"GVA:(\d+)", identificador or "")
                id_emp = int(m.group(1)) if m else None
            try:
                id_emp = int(id_emp)
            except (TypeError, ValueError):
                continue
            url = str(datos.get("url_detalle") or f"{base.GVA_BASE_URL}/detall-ocupacio-publica?id_emp={id_emp}")
            resultado.append((id_emp, url))
    return resultado


def descubrir_detalles(client, max_paginas: int = 10) -> list[tuple[int, str]]:
    """Une nuevas oportunidades con procesos conocidos sin bloquear su seguimiento."""
    global _ULTIMO_ERROR_DESCUBRIMIENTO_GVA
    _ULTIMO_ERROR_DESCUBRIMIENTO_GVA = None
    encontrados: dict[int, str] = {}

    try:
        encontrados.update(_BASE_DESCUBRIR_DETALLES(client, max_paginas=max_paginas))
    except Exception as exc:
        _ULTIMO_ERROR_DESCUBRIMIENTO_GVA = f"{type(exc).__name__}: {exc}"

    for id_emp, url in _detalles_existentes_a_seguir():
        encontrados[id_emp] = url
    return sorted(encontrados.items())


def parsear_detalle(url: str, html: str, id_emp: int) -> dict[str, Any]:
    proceso = _BASE_PARSEAR_DETALLE(url, html, id_emp)
    soup = BeautifulSoup(html, "html.parser")
    texto = base._normalizar(soup.get_text(" ", strip=True))
    titulo = str(proceso.get("denominacion") or "")
    proceso["tipo_proceso"] = proceso.get("tipo_proceso") or _tipo_convocatoria(texto)
    proceso["turno"] = _turno(titulo) or proceso.get("turno") or _turno(texto)

    organismo_id, motivo, organismo_enlace = _resolver_organismo(proceso)
    organismo_texto = str(proceso.get("organismo") or proceso.get("datos_json", {}).get("organismo_detectado") or "")
    etapa_actual = _etapa_actual(texto)
    estado_plazo = _estado_plazo_solicitud(texto)
    fecha_etapa = proceso.get("fecha_etapa")
    etapa_fecha_apertura = proceso.get("fecha_apertura")
    etapa_fecha_cierre = proceso.get("fecha_cierre")
    etapa_solicitud = _es_etapa_solicitud(etapa_actual)

    proceso["organismo_id"] = organismo_id
    proceso["estado"] = _estado_ciclo_selectivo(texto)
    if fecha_etapa and etapa_actual and "base" in _sin(etapa_actual):
        proceso["fecha_convocatoria"] = fecha_etapa

    # fecha_apertura/fecha_cierre representan exclusivamente el plazo original
    # de presentación de solicitudes. Los plazos de alegaciones u otras etapas
    # se conservan aparte en datos_json y nunca sustituyen al plazo original.
    if not etapa_solicitud:
        proceso["fecha_apertura"] = None
        proceso["fecha_cierre"] = None

    datos_nuevos = {
        "organismo_detectado": organismo_texto,
        "organismo_enlace": organismo_enlace,
        "organismo_id_resuelto": organismo_id,
        "organismo_motivo": motivo,
        "etapa_actual": etapa_actual,
        "estado_plazo_etapa_actual": estado_plazo,
        "etapa_actual_fecha_publicacion": fecha_etapa.isoformat() if fecha_etapa else None,
        "etapa_actual_fecha_apertura": etapa_fecha_apertura.isoformat() if etapa_fecha_apertura else None,
        "etapa_actual_fecha_cierre": etapa_fecha_cierre.isoformat() if etapa_fecha_cierre else None,
    }
    if etapa_solicitud:
        datos_nuevos.update({
            "estado_plazo_solicitud": estado_plazo,
            "fecha_apertura_solicitud": etapa_fecha_apertura.isoformat() if etapa_fecha_apertura else None,
            "fecha_cierre_solicitud": etapa_fecha_cierre.isoformat() if etapa_fecha_cierre else None,
        })

    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        **datos_nuevos,
    }
    return proceso


base._tipo_convocatoria = _tipo_convocatoria
base._turno = _turno
base._es_incluido = _es_incluido
base._es_del_ambito = _es_del_ambito_activo
base._estado = _estado_ciclo_selectivo
base.descubrir_detalles = descubrir_detalles
base.parsear_detalle = parsear_detalle


def _etapas_guardadas() -> dict[int, str | None]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, datos_json->>'etapa_actual' FROM procesos WHERE organismo_id=%s AND identificador_estable LIKE 'GVA:%%'",
            (base.GVA_ORGANISMO_ID,),
        )
        return {int(proceso_id): etapa for proceso_id, etapa in cursor.fetchall()}


def _registrar_cambios_etapa(anteriores: dict[int, str | None]) -> int:
    cambios = 0
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, datos_json->>'etapa_actual' FROM procesos WHERE organismo_id=%s AND identificador_estable LIKE 'GVA:%%'",
            (base.GVA_ORGANISMO_ID,),
        )
        for proceso_id, nueva in cursor.fetchall():
            anterior = anteriores.get(int(proceso_id))
            if not anterior or not nueva or anterior == nueva:
                continue
            cursor.execute(
                """
                INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo)
                VALUES (%s,'ACTUALIZACION','etapa_actual',%s,%s,%s,TRUE)
                """,
                (proceso_id, anterior, nueva, f"Nueva etapa del proceso: {nueva}"),
            )
            cambios += 1
        connection.commit()
    return cambios


def _desactivar_cambios_tecnicos_gva() -> int:
    """Impide que correcciones de captura se conviertan en novedades del opositor."""
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE cambios c
            SET significativo = FALSE
            FROM procesos p
            WHERE p.id = c.proceso_id
              AND p.organismo_id = %s
              AND c.significativo = TRUE
              AND (
                    LOWER(COALESCE(c.campo, '')) <> ALL(%s)
                    OR LOWER(COALESCE(c.valor_anterior, '')) IN ('navegación', 'navegacion')
                    OR LOWER(COALESCE(c.valor_nuevo, '')) IN ('navegación', 'navegacion')
                    OR (
                        LOWER(COALESCE(c.campo, '')) = 'estado'
                        AND UPPER(COALESCE(c.valor_anterior, '')) IN ('ABIERTO','PENDIENTE','CERRADO','EN_SEGUIMIENTO')
                        AND UPPER(COALESCE(c.valor_nuevo, '')) = 'EN_CURSO'
                    )
                  )
            """, (base.GVA_ORGANISMO_ID, list(CAMPOS_NOVEDAD_GVA)),
        )
        actualizados = cursor.rowcount
        connection.commit()
        return actualizados


def _errores_fuente(stats: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in stats.get("diagnostico", [])
        if item.get("motivo") in {"error_descubrimiento", "error_detalle"}
    ]


def importar_gva_robusto(*, max_paginas: int = 10, max_detalles: int | None = None) -> dict[str, Any]:
    """Importa GVA y sigue las fichas conocidas hasta su cierre selectivo real.

    El cierre del plazo de solicitudes se conserva como dato y no finaliza el
    proceso. Las decisiones manuales SI/NO nunca se sobrescriben. Si la fuente
    oficial queda incompleta, no se ejecutan tareas de mantenimiento globales
    sobre registros GVA existentes.
    """
    etapas_antes = _etapas_guardadas()
    stats = _BASE_IMPORTAR_GVA_ROBUSTO(max_paginas=max_paginas, max_detalles=max_detalles)
    if _ULTIMO_ERROR_DESCUBRIMIENTO_GVA:
        stats["diagnostico"].append({
            "motivo": "error_descubrimiento",
            "fuente": base.GVA_SEARCH_URL,
            "error": _ULTIMO_ERROR_DESCUBRIMIENTO_GVA,
        })

    errores = _errores_fuente(stats)
    stats["estado_importacion"] = "INCOMPLETA" if errores else "COMPLETA"
    stats["errores_fuente"] = len(errores)
    stats["cambios_etapa"] = _registrar_cambios_etapa(etapas_antes)

    if errores:
        stats["ambito_administrativo_actualizados"] = 0
        stats["cambios_tecnicos_desactivados"] = 0
        stats["mantenimiento_global_omitido"] = True
        return stats

    actualizados = 0
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, denominacion, cuerpo_escala, grupo FROM procesos WHERE organismo_id=%s AND ambito_administrativo='REVISION'",
            (base.GVA_ORGANISMO_ID,),
        )
        for proceso_id, denominacion, cuerpo_escala, grupo in cursor.fetchall():
            ambito = clasificar_ambito_administrativo({"denominacion": denominacion, "cuerpo_escala": cuerpo_escala, "grupo": grupo})
            if ambito == "REVISION":
                continue
            cursor.execute(
                "UPDATE procesos SET ambito_administrativo=%s, updated_at=NOW() WHERE id=%s AND ambito_administrativo='REVISION'",
                (ambito, proceso_id),
            )
            actualizados += cursor.rowcount
        connection.commit()
    stats["ambito_administrativo_actualizados"] = actualizados
    stats["cambios_tecnicos_desactivados"] = _desactivar_cambios_tecnicos_gva()
    stats["mantenimiento_global_omitido"] = False
    return stats


def limpiar_gva_navegacion() -> dict[str, int]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM procesos WHERE organismo_id=%s AND denominacion='Navegación' AND datos_json->>'organismo_detectado'='Navegación'",
                (base.GVA_ORGANISMO_ID,),
            )
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                connection.commit()
                return {"procesos_eliminados": 0, "publicaciones_eliminadas": 0, "cambios_eliminados": 0}
            cursor.execute("DELETE FROM cambios WHERE proceso_id=ANY(%s)", (ids,))
            cambios = cursor.rowcount
            cursor.execute("DELETE FROM publicaciones WHERE proceso_id=ANY(%s)", (ids,))
            publicaciones = cursor.rowcount
            cursor.execute("DELETE FROM procesos WHERE id=ANY(%s)", (ids,))
            procesos = cursor.rowcount
            connection.commit()
    return {"procesos_eliminados": procesos, "publicaciones_eliminadas": publicaciones, "cambios_eliminados": cambios}
