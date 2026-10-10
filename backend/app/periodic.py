from __future__ import annotations

from datetime import date, timedelta
import logging
import os
import time
import traceback
from typing import Any, Callable

import httpx

from .boe_local_extractor import extraer_convocatorias_boe_local
from .boe_local_import import previsualizar_importacion_boe_local, recuperar_boe_para_proceso_bop
from .database import get_connection
from psycopg.rows import dict_row
from .organismos_cron import ejecutar_organismo
from .notificaciones_generales import enviar_envios_pendientes, filtrar_nuevas_oportunidades_notificables, ids_oportunidades_visibles, preparar_envios_eventos, registrar_nuevas_oportunidades
from .seguimiento import ids_novedades_seguimiento, enviar_avisos_novedades
from .clasificacion_auditoria import revisar_clasificacion_puestos
from .ciclos import (
    dias_necesarios,
    finalizar_ciclo,
    iniciar_ciclo,
    registrar_ejecuciones,
    ultimos_exitos,
)
from .festivos import hoy_es
from .reconciliar_cierres import reconciliar_cierres


DIAS_SOLAPE_DEFECTO = 7
LIMITE_DIAS_ORDINARIO = 30


def _validar_dias_solape(*, dias_solape: int, historico: bool) -> None:
    """Protege el cron ordinario y exige una intención explícita para históricos."""
    if dias_solape < 1:
        raise ValueError("dias_solape debe ser al menos 1")
    if not historico and dias_solape > LIMITE_DIAS_ORDINARIO:
        raise ValueError(
            f"dias_solape debe estar entre 1 y {LIMITE_DIAS_ORDINARIO} "
            "en modo ordinario; use historico=True para una revisión excepcional"
        )


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


def _recuperar_boe_pendientes_activos(*, hasta: date, dias: int, aplicar: bool) -> dict[str, Any]:
    """Cruza pendientes BOP activos únicamente con la ventana BOE ordinaria del cron."""
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id,
                   COALESCE(
                       p.fecha_convocatoria,
                       (SELECT MIN(pub.fecha_publicacion)
                        FROM publicaciones pub
                        JOIN fuentes fb ON fb.id=pub.fuente_id
                        WHERE pub.proceso_id=p.id
                          AND fb.tipo='BOP'
                          AND UPPER(COALESCE(pub.tipo,'')) IN ('BASES','CONVOCATORIA','BOP')
                          AND (LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'
                               OR COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'))
                   ) AS fecha_bases
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
              AND COALESCE(
                    p.fecha_convocatoria,
                    (SELECT MIN(pub.fecha_publicacion)
                     FROM publicaciones pub
                     JOIN fuentes fb ON fb.id=pub.fuente_id
                     WHERE pub.proceso_id=p.id
                       AND fb.tipo='BOP'
                       AND UPPER(COALESCE(pub.tipo,'')) IN ('BASES','CONVOCATORIA','BOP')
                          AND (LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'
                               OR COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'))
                  ) IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1
                    FROM publicaciones pboe
                    JOIN fuentes fboe ON fboe.id=pboe.fuente_id
                    WHERE pboe.proceso_id=p.id
                      AND fboe.tipo='BOE'
                  )
              AND (
                    p.datos_json->>'origen' IN (
                        'BOP_VALENCIA','BOP_VALENCIA_MUNICIPAL','BOP_CASTELLON','BOP_ALICANTE',
                        'DIPUTACION_ALICANTE_OTRAS'
                    )
                    OR p.identificador_estable LIKE 'DVAL:%'
                    OR p.identificador_estable LIKE 'AVAL:%'
                  )
            ORDER BY p.id
            """
        )
        pendientes = list(cursor.fetchall())

    extraccion_boe = (
        extraer_convocatorias_boe_local(hasta=hasta, dias=dias)
        if pendientes
        else None
    )

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
            fecha_bases=proceso["fecha_bases"],
            hasta=hasta,
            aplicar=aplicar,
            extraccion_boe=extraccion_boe,
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


def _ejecutar_ciclo(
    *,
    aplicar: bool = False,
    hoy: date | None = None,
    dias_solape: int = DIAS_SOLAPE_DEFECTO,
    historico: bool = False,
    ultimos_exitos: dict[str, date] | None = None,
) -> dict[str, Any]:
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
    El modo histórico permite una ventana superior a 30 días únicamente cuando
    se solicita expresamente y nunca envía notificaciones.
    """
    _validar_dias_solape(dias_solape=dias_solape, historico=historico)

    fecha_hoy = hoy or hoy_es()
    desde = fecha_hoy - timedelta(days=dias_solape - 1)
    notificaciones_habilitadas = bool(aplicar and not historico)
    visibles_antes = ids_oportunidades_visibles() if notificaciones_habilitadas else set()
    novedades_seguimiento_antes = ids_novedades_seguimiento() if notificaciones_habilitadas else set()
    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "escrituras_bd": aplicar,
        "desde": desde.isoformat(),
        "hasta": fecha_hoy.isoformat(),
        "dias_solape": dias_solape,
        "historico": historico,
        "notificaciones_habilitadas": notificaciones_habilitadas,
        "fuentes": {},
        "estado_fuentes": {},
        "duraciones_fuentes_segundos": {},
        "ventanas": {},
        "ventanas_excedidas": {},
    }

    def dias_de(nombre: str) -> int:
        """Ventana de la fuente: cubre desde su último éxito (ordinario) o la
        indicada por el operador (histórico). dias_solape es el mínimo."""
        if historico or not ultimos_exitos:
            dias = dias_solape
        else:
            dias, excede = dias_necesarios(
                ultimos_exitos.get(nombre),
                fecha_hoy,
                solape_minimo=dias_solape,
                tope=LIMITE_DIAS_ORDINARIO,
            )
            if excede:
                resultado["ventanas_excedidas"][nombre] = (fecha_hoy - ultimos_exitos[nombre]).days
        resultado["ventanas"][nombre] = dias
        return dias

    def desde_de(nombre: str) -> date:
        return fecha_hoy - timedelta(days=dias_de(nombre) - 1)

    def registrar(nombre: str, funcion: Callable[[], Any]) -> None:
        inicio = time.monotonic()
        logging.getLogger(__name__).warning("PERIODIC INICIO fuente=%s", nombre)
        valor, estado = _ejecutar_fuente(funcion)
        duracion = time.monotonic() - inicio
        logging.getLogger(__name__).warning(
            "PERIODIC FIN fuente=%s estado=%s duracion=%.1fs",
            nombre,
            estado,
            duracion,
        )
        resultado["fuentes"][nombre] = valor
        resultado["estado_fuentes"][nombre] = estado
        resultado["duraciones_fuentes_segundos"][nombre] = round(duracion, 1)

    registrar(
        "bop_valencia_diputacion",
        lambda: ejecutar_organismo("diputacion_valencia", hoy=fecha_hoy, dias=dias_de("bop_valencia_diputacion"), aplicar=aplicar),
    )

    registrar(
        "bop_valencia_municipios",
        lambda: ejecutar_organismo("ayuntamientos_valencia", hoy=fecha_hoy, dias=dias_de("bop_valencia_municipios"), aplicar=aplicar),
    )

    registrar(
        "bop_castellon",
        lambda: ejecutar_organismo("diputacion_castellon", hoy=fecha_hoy, dias=dias_de("bop_castellon"), aplicar=aplicar),
    )

    registrar(
        "bop_castellon_municipios",
        lambda: ejecutar_organismo("ayuntamientos_castellon", hoy=fecha_hoy, dias=dias_de("bop_castellon_municipios"), aplicar=aplicar),
    )

    registrar(
        "alicante_otras_entidades",
        lambda: ejecutar_organismo("diputacion_alicante", hoy=fecha_hoy, dias=dias_de("alicante_otras_entidades"), aplicar=aplicar),
    )

    registrar(
        "bop_alicante",
        lambda: ejecutar_organismo("ayuntamientos_alicante", hoy=fecha_hoy, dias=dias_de("bop_alicante"), aplicar=aplicar),
    )

    registrar(
        "boe_pendientes_activos",
        lambda: _recuperar_boe_pendientes_activos(hasta=fecha_hoy, dias=dias_de("boe_local"), aplicar=aplicar),
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
            dias=dias_de("boe_local"),
            aplicar=aplicar,
            boe_ids_absorbidos=boe_absorbidos_revision,
        ),
    )

    registrar(
        "gva",
        lambda: ejecutar_organismo("gva", hoy=fecha_hoy, dias=dias_de("gva"), aplicar=aplicar),
    )

    # Se activa explícitamente tras validar la auditoría histórica. Procesa una
    # tanda pequeña, aislada del resto de fuentes, y nunca reescribe campos.
    if aplicar and os.getenv("EMPLOYMENT_CLASSIFICATION_ENRICHMENT", "false").lower() == "true":
        registrar(
            "clasificacion_puestos",
            lambda: revisar_clasificacion_puestos(
                aplicar=True,
                limite=15,
                ordenar_por_reciente=True,
            ),
        )

    # Da de baja todo proceso cuyo resultado final ya consta en publicaciones,
    # provenga de la fuente que provenga. Va después de todas las fuentes para
    # ver también lo ingerido en este mismo ciclo.
    registrar(
        "reconciliacion_cierres",
        lambda: reconciliar_cierres(aplicar=aplicar, historico=historico),
    )

    estados = [fuente["estado"] for fuente in resultado["estado_fuentes"].values()]
    if notificaciones_habilitadas:
        visibles_despues = ids_oportunidades_visibles()
        nuevos_visibles = visibles_despues - visibles_antes
        nuevos_notificables = filtrar_nuevas_oportunidades_notificables(nuevos_visibles)
        eventos_creados = registrar_nuevas_oportunidades(nuevos_notificables)
        envios_preparados = preparar_envios_eventos(eventos_creados)
        envios_enviados = enviar_envios_pendientes()
        resultado["notificaciones_generales"] = {
            "nuevas_oportunidades_visibles": len(nuevos_visibles),
            "nuevas_oportunidades_notificables": len(nuevos_notificables),
            "eventos_creados": len(eventos_creados),
            "envios_preparados": envios_preparados,
            "envio": envios_enviados,
        }

        seguimiento_enviado = enviar_avisos_novedades(novedades_seguimiento_antes)
        resultado["notificaciones_seguimiento"] = {
            "envio": seguimiento_enviado,
        }
    elif aplicar:
        resultado["notificaciones_generales"] = {"omitidas_modo_historico": True}
        resultado["notificaciones_seguimiento"] = {"omitidas_modo_historico": True}

    resultado["resumen_fuentes"] = {
        "total": len(estados),
        "ok": sum(e == "OK" for e in estados),
        "sin_novedades": sum(e == "SIN_NOVEDADES" for e in estados),
        "degradadas": sum(e == "DEGRADADA" for e in estados),
        "errores": sum(e == "ERROR" for e in estados),
    }
    return resultado


def ejecutar_periodico(
    *,
    aplicar: bool = False,
    hoy: date | None = None,
    dias_solape: int = DIAS_SOLAPE_DEFECTO,
    historico: bool = False,
) -> dict[str, Any]:
    """Punto de entrada del ciclo: exclusión mutua + ventanas + trazabilidad.

    Solo el modo APLICADO reserva ciclo (SOLO_REVISION no escribe y puede
    ejecutarse en paralelo). Si hay otro ciclo en curso devuelve un payload
    ``ciclo.omitido`` sin tocar nada.
    """
    _validar_dias_solape(dias_solape=dias_solape, historico=historico)
    ciclo_id: int | None = None
    info_ciclo: dict[str, Any] = {"disponible": True}
    if aplicar:
        ciclo_id, estado_ciclo = iniciar_ciclo(historico=historico)
        if estado_ciclo == "OMITIDO_CICLO_EN_CURSO":
            return {
                "modo": "APLICADO",
                "ciclo": {"omitido": True, "motivo": "otro ciclo periódico sigue en curso"},
                "resumen_fuentes": {"total": 0, "ok": 0, "sin_novedades": 0, "degradadas": 0, "errores": 0},
            }
        info_ciclo["disponible"] = estado_ciclo != "SIN_TABLAS"
    try:
        exitos = {} if historico else ultimos_exitos()
        resultado = _ejecutar_ciclo(
            aplicar=aplicar,
            hoy=hoy,
            dias_solape=dias_solape,
            historico=historico,
            ultimos_exitos=exitos,
        )
    except Exception as exc:
        finalizar_ciclo(ciclo_id, "CON_ERRORES", {"excepcion": f"{type(exc).__name__}: {str(exc)[:500]}"})
        raise

    resultado["ciclo"] = {**info_ciclo, "id": ciclo_id}
    hasta = date.fromisoformat(resultado["hasta"])
    ejecuciones = []
    for nombre, estado in resultado["estado_fuentes"].items():
        ejecuciones.append({
            "fuente": nombre,
            "estado": estado["estado"],
            "duracion_s": resultado["duraciones_fuentes_segundos"].get(nombre),
            "hasta_fecha": hasta,
            "dias_ventana": resultado["ventanas"].get(nombre),
            "error": estado.get("error"),
        })
    if aplicar:
        registrar_ejecuciones(ciclo_id, ejecuciones)
        con_errores = resultado["resumen_fuentes"]["errores"] > 0
        finalizar_ciclo(
            ciclo_id,
            "CON_ERRORES" if con_errores else "OK",
            {"resumen_fuentes": resultado["resumen_fuentes"], "ventanas": resultado["ventanas"]},
        )
    return resultado
