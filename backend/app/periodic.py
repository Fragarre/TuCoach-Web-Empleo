from __future__ import annotations

from datetime import date, timedelta
import traceback
from typing import Any, Callable

import httpx

from .boe_local_import import previsualizar_importacion_boe_local, recuperar_boe_para_proceso_bop
from .database import get_connection
from psycopg.rows import dict_row
from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .bop_valencia_municipios import importar_municipales_bop
from .bop_castellon import importar_bop_castellon
from .bop_alicante import importar_bop_alicante
from .alicante_otras_entidades import bootstrap_otras_entidades_alicante
from .gva_estatal_service import importar_gva_estatal
from .notificaciones_generales import enviar_envios_pendientes, ids_oportunidades_visibles, preparar_envios_eventos, registrar_nuevas_oportunidades
from .seguimiento import ids_novedades_seguimiento, enviar_avisos_novedades


DIAS_SOLAPE_DEFECTO = 7


def _estado_fuente(valor: Any) -> str:
    """Clasifica el resultado sin reinterpretar la semántica propia de cada conector."""
    if isinstance(valor, dict):
        if valor.get("errores") or valor.get("dias_con_error"):
            return "DEGRADADA"
        contadores = (
            "descubiertos",
            "convocatorias_extraidas",
            "insertados",
            "publicaciones_creadas",
            "cambios_creados",
            "nuevas",
        )
        presentes = [valor.get(k) for k in contadores if isinstance(valor.get(k), int)]
        if presentes and not any(presentes):
            return "SIN_NOVEDADES"
    return "OK"


def _ejecutar_fuente(funcion: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    """Aísla una fuente sin alterar el payload histórico cuando tiene éxito."""
    try:
        valor = funcion()
        return valor, {"estado": _estado_fuente(valor)}
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        traza = traceback.format_exc()
        return {"error": error, "traceback": traza}, {"estado": "ERROR", "error": error, "traceback": traza}


def _recuperar_boe_pendientes_activos(*, hasta: date, aplicar: bool) -> dict[str, Any]:
    """Busca BOE para oportunidades BOP activas sin BOE simple o con seguimiento agregado."""
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id,p.fecha_convocatoria
            FROM procesos p
            JOIN organismos o ON o.id=p.organismo_id
            WHERE p.es_oportunidad=TRUE
              AND p.estado='EN_CURSO'
              AND p.ambito_administrativo='SI'
              AND o.activo=TRUE
              AND (
                    (o.tipo='AYUNTAMIENTO' AND LOWER(COALESCE(o.provincia,'')) IN
                        ('valencia','valència','alicante','castellón','castellon'))
                    OR
                    (o.tipo='DIPUTACION' AND LOWER(COALESCE(o.provincia,'')) IN
                        ('valencia','valència','alicante','castellón','castellon'))
                  )
              AND p.fecha_convocatoria IS NOT NULL
              AND (
                    p.datos_json->'boe_local' IS NULL
                    OR p.datos_json->'boe_local_agregados' IS NOT NULL
                  )
              AND p.datos_json->>'origen' IN ('BOP_VALENCIA','BOP_CASTELLON','BOP_ALICANTE')
            ORDER BY p.id
            """
        )
        pendientes = list(cursor.fetchall())

    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "pendientes": len(pendientes),
        "coincidencias_unicas": 0,
        "vinculadas": 0,
        "sin_coincidencia": 0,
        "revision_solapamiento": 0,
        "agregados": 0,
        "agregados_actualizados": 0,
        "detalle": [],
    }
    for proceso in pendientes:
        r = recuperar_boe_para_proceso_bop(
            proceso_id=proceso["id"],
            fecha_bases=proceso["fecha_convocatoria"],
            hasta=hasta,
            aplicar=aplicar,
        )
        estado = r.get("estado")
        if estado in ("COINCIDENCIA_UNICA", "VINCULADA"):
            resultado["coincidencias_unicas"] += 1
        if estado == "VINCULADA":
            resultado["vinculadas"] += 1
        elif estado == "SIN_COINCIDENCIA":
            resultado["sin_coincidencia"] += 1
        elif estado == "REVISION_SOLAPAMIENTO":
            resultado["revision_solapamiento"] += 1
        if estado in ("AGREGADO_PARCIAL", "AGREGADO_COMPLETO", "AGREGADO_ACTUALIZADO", "AGREGADO_SIN_CAMBIOS"):
            resultado["agregados"] += 1
        if estado == "AGREGADO_ACTUALIZADO":
            resultado["agregados_actualizados"] += 1
        resultado["detalle"].append({"proceso_id": proceso["id"], **r})
    return resultado


def ejecutar_periodico(*, aplicar: bool = False, hoy: date | None = None, dias_solape: int = DIAS_SOLAPE_DEFECTO) -> dict[str, Any]:
    """Orquesta las fuentes validadas de Empleo con aislamiento por fuente.

    Fuentes incluidas:
    - BOP Valencia Diputación.
    - BOP Valencia municipal: altas y seguimientos de ayuntamientos.
    - BOP Castellón: altas y seguimientos administrativos validados.
    - BOP Alicante: altas y seguimientos administrativos validados.
    - BOE local: altas/vinculaciones de convocatorias administrativas valencianas.
    - GVA estatal + seguimiento DOGV directo de convocatorias activas.

    En SOLO_REVISION no se escribe en BD. Cada fuente se ejecuta de forma
    aislada: un fallo se registra como ERROR y el ciclo continúa con las demás.
    En APLICADO todas las fuentes usan la misma ventana solapada para tolerar
    caídas puntuales sin depender de que el cron haya ejecutado el día anterior.
    """
    if dias_solape < 1 or dias_solape > 30:
        raise ValueError("dias_solape debe estar entre 1 y 30")

    fecha_hoy = hoy or date.today()
    desde = fecha_hoy - timedelta(days=dias_solape - 1)
    visibles_antes = ids_oportunidades_visibles() if aplicar else set()
    novedades_seguimiento_antes = ids_novedades_seguimiento() if aplicar else set()
    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "escrituras_bd": aplicar,
        "desde": desde.isoformat(),
        "hasta": fecha_hoy.isoformat(),
        "dias_solape": dias_solape,
        "fuentes": {},
        "estado_fuentes": {},
    }

    def registrar(nombre: str, funcion: Callable[[], Any]) -> None:
        valor, estado = _ejecutar_fuente(funcion)
        resultado["fuentes"][nombre] = valor
        resultado["estado_fuentes"][nombre] = estado

    def ejecutar_bop_diputacion() -> Any:
        if aplicar:
            return importar_bop_valencia(historico=True, dias=dias_solape)
        headers = {
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            return {
                "modo": "SOLO_DIAGNOSTICO",
                "resultado": diagnosticar_bop(client, fecha=fecha_hoy.isoformat()),
            }

    registrar("bop_valencia_diputacion", ejecutar_bop_diputacion)

    # El BOP municipal se mantiene antes que BOE: en modo aplicado, BOE puede
    # vincular con bases municipales detectadas en esta misma ejecución.
    registrar(
        "bop_valencia_municipios",
        lambda: importar_municipales_bop(
            hasta=fecha_hoy,
            dias=dias_solape,
            aplicar=aplicar,
        ),
    )

    registrar(
        "bop_castellon",
        lambda: importar_bop_castellon(
            desde=desde,
            hasta=fecha_hoy,
            aplicar=aplicar,
        ),
    )

    registrar(
        "alicante_otras_entidades",
        lambda: bootstrap_otras_entidades_alicante(
            max_items=200,
            aplicar=aplicar,
        ),
    )

    registrar(
        "bop_alicante",
        lambda: importar_bop_alicante(
            dias_solape=dias_solape,
            hasta=fecha_hoy,
            aplicar=aplicar,
        ),
    )

    registrar(
        "boe_pendientes_activos",
        lambda: _recuperar_boe_pendientes_activos(
            hasta=fecha_hoy,
            aplicar=aplicar,
        ),
    )

    # En SOLO_REVISION la recuperación anterior no persiste las publicaciones
    # reconocidas. El importador BOE ordinario no puede, por tanto, interpretar
    # esos mismos documentos como altas nuevas durante el mismo diagnóstico.
    # Conservamos sus boe_id como sombra exclusivamente en memoria.
    boe_absorbidos_revision: set[str] = set()
    if not aplicar:
        pendientes_revision = resultado["fuentes"].get("boe_pendientes_activos") or {}
        for detalle in pendientes_revision.get("detalle", []):
            boe_id = detalle.get("boe_id")
            if boe_id:
                boe_absorbidos_revision.add(boe_id)
            for candidato in detalle.get("boe_local_agregados_propuestos", []) or []:
                boe_id = candidato.get("boe_id")
                if boe_id:
                    boe_absorbidos_revision.add(boe_id)

    registrar(
        "boe_local",
        lambda: previsualizar_importacion_boe_local(
            hasta=fecha_hoy,
            dias=dias_solape,
            aplicar=aplicar,
            boe_ids_absorbidos=boe_absorbidos_revision,
        ),
    )

    registrar(
        "gva",
        lambda: importar_gva_estatal(
            desde=desde,
            hasta=fecha_hoy,
            aplicar=aplicar,
        ),
    )

    estados = [fuente["estado"] for fuente in resultado["estado_fuentes"].values()]
    if aplicar:
        visibles_despues = ids_oportunidades_visibles()
        nuevos_visibles = visibles_despues - visibles_antes
        eventos_creados = registrar_nuevas_oportunidades(nuevos_visibles)
        envios_preparados = preparar_envios_eventos(eventos_creados)
        envios_enviados = enviar_envios_pendientes()
        resultado["notificaciones_generales"] = {
            "nuevas_oportunidades_visibles": len(nuevos_visibles),
            "eventos_creados": len(eventos_creados),
            "envios_preparados": envios_preparados,
            "envio": envios_enviados,
        }

        seguimiento_enviado = enviar_avisos_novedades(novedades_seguimiento_antes)
        resultado["notificaciones_seguimiento"] = {
            "envio": seguimiento_enviado,
        }

    resultado["resumen_fuentes"] = {
        "total": len(estados),
        "ok": sum(e == "OK" for e in estados),
        "sin_novedades": sum(e == "SIN_NOVEDADES" for e in estados),
        "degradadas": sum(e == "DEGRADADA" for e in estados),
        "errores": sum(e == "ERROR" for e in estados),
    }
    return resultado
