"""Persistencia del plazo de inscripción de convocatorias locales.

Las bases de diputaciones y ayuntamientos fijan casi siempre el plazo como
"N días hábiles a contar desde el siguiente al de la publicación en el BOE".
El BOE ya se lee (fecha y literal quedan en ``datos_json``); aquí se calcula el
primer y último día con el calendario de la Comunitat Valenciana y se GUARDA en
``fecha_apertura`` / ``fecha_cierre``, de modo que la web, los filtros y las
alertas trabajen con fechas reales y no con un cálculo en cada lectura.

Reglas de seguridad:

* Nunca sobrescribe fechas ya guardadas. Si el cálculo difiere de lo guardado
  (p. ej. fechas leídas de unas bases), lo informa como DISCREPANCIA.
* Solo rellena cuando hay fecha de publicación en el BOE y un literal de plazo
  que se sepa interpretar. Si hay varias publicaciones BOE agregadas, no decide.
* Que el plazo haya vencido NO cierra la convocatoria: el proceso sigue vivo
  hasta que conste su resultado final (``ciclo_vida``).
* No incorpora festivos locales: el resultado se marca con
  ``plazo_inscripcion.festivos_locales = false`` para que la web avise.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from . import festivos as _festivos
from .ciclo_vida import ESTADOS_TERMINALES_SQL_IN
from .estado_proceso import _sin, numero_dias_literal


def _fecha_iso(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:
            return None
    return None


def interpretar_literal(literal: str | None) -> tuple[int, str] | None:
    """Devuelve (número de días, "HABILES" | "NATURALES") o None.

    Sin indicación expresa se trata como hábil (Ley 39/2015, art. 30.2).
    """
    n = _sin(literal)
    if not n.strip():
        return None
    tipo = "NATURALES" if re.search(r"\bdias?\s+naturales?\b|\bdies\s+naturals\b", n) else "HABILES"
    dias = numero_dias_literal(literal)
    if not dias or dias <= 0:
        return None
    return dias, tipo


def _siguiente_habil(dia: date) -> date:
    while not _festivos.es_dia_habil(dia):
        dia += timedelta(days=1)
    return dia


def calcular_plazo(datos_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """Plazo de inscripción a partir de lo que guarda el proceso; None si no se
    puede determinar de forma inequívoca. Función pura (sin BD)."""
    datos = datos_json if isinstance(datos_json, dict) else {}
    agregados = datos.get("boe_local_agregados")
    if isinstance(agregados, list) and len(agregados) > 1:
        return None  # varios plazos: no se decide por el proceso

    boe_local = datos.get("boe_local") if isinstance(datos.get("boe_local"), dict) else {}
    if isinstance(agregados, list) and len(agregados) == 1 and isinstance(agregados[0], dict):
        boe_local = {**boe_local, **agregados[0]}
    literal = str(datos.get("plazo_solicitudes_literal") or boe_local.get("plazo_solicitudes_literal") or "").strip()
    fecha_boe = _fecha_iso(datos.get("fecha_boe") or boe_local.get("fecha_boe"))
    if not literal or not fecha_boe:
        return None
    interpretado = interpretar_literal(literal)
    if not interpretado:
        return None
    dias, tipo = interpretado

    apertura = fecha_boe + timedelta(days=1)
    if tipo == "HABILES":
        cierre = _festivos.sumar_dias_habiles(fecha_boe, dias)
    else:
        # Días naturales: si el último día es inhábil, pasa al siguiente hábil.
        cierre = _siguiente_habil(fecha_boe + timedelta(days=dias))
    anios = _festivos.anios_tocados(fecha_boe, cierre)
    return {
        "fecha_boe": fecha_boe,
        "fecha_apertura": apertura,
        "fecha_cierre": cierre,
        "dias": dias,
        "tipo_dias": tipo,
        "literal": literal,
        "calendario": "COMUNITAT_VALENCIANA",
        "calendario_verificado": all(_festivos.calendario_verificado(a) for a in anios),
        "festivos_locales": False,
    }


def decidir(proceso: dict[str, Any]) -> dict[str, Any] | None:
    """Acción para un proceso: RELLENAR, DISCREPANCIA o None. Función pura."""
    plazo = calcular_plazo(proceso.get("datos_json"))
    if plazo is None:
        return None
    apertura = proceso.get("fecha_apertura")
    cierre = proceso.get("fecha_cierre")
    if apertura is None and cierre is None:
        return {"accion": "RELLENAR", "plazo": plazo}
    if apertura is not None and cierre is not None:
        if apertura == plazo["fecha_apertura"] and cierre == plazo["fecha_cierre"]:
            return None
        return {"accion": "DISCREPANCIA", "plazo": plazo}
    return {"accion": "PARCIAL_SIN_TOCAR", "plazo": plazo}


def _marca_datos(plazo: dict[str, Any]) -> dict[str, Any]:
    return {"plazo_inscripcion": {
        "origen": "BOE_CALCULADO",
        "fecha_boe": plazo["fecha_boe"].isoformat(),
        "dias": plazo["dias"],
        "tipo_dias": plazo["tipo_dias"],
        "literal": plazo["literal"],
        "calendario": plazo["calendario"],
        "calendario_verificado": plazo["calendario_verificado"],
        "festivos_locales": plazo["festivos_locales"],
    }}


SQL_CANDIDATOS = (
    "SELECT p.id, p.denominacion, p.fecha_apertura, p.fecha_cierre, p.datos_json, "
    "o.nombre AS organismo "
    "FROM procesos p JOIN organismos o ON o.id = p.organismo_id "
    "WHERE LOWER(COALESCE(p.estado,'')) NOT IN " + ESTADOS_TERMINALES_SQL_IN + " "
    "AND p.es_oportunidad = TRUE AND p.ambito_administrativo = 'SI' "
    "AND (p.datos_json ? 'plazo_solicitudes_literal' "
    "     OR p.datos_json ? 'boe_local' OR p.datos_json ? 'boe_local_agregados') "
    "ORDER BY p.id"
)

LIMITE_DETALLE = 200


def sincronizar_plazos_inscripcion(*, aplicar: bool = False, abrir_cursor=None) -> dict[str, Any]:
    """Rellena fecha_apertura/fecha_cierre desde el BOE. Idempotente."""
    from contextlib import contextmanager

    @contextmanager
    def _cursor_por_defecto():
        from psycopg.rows import dict_row
        from .database import get_connection
        with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            yield cursor

    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "revisados": 0, "rellenados": 0, "discrepancias": 0, "sin_plazo": 0,
        "parciales": 0, "detalle": [],
    }
    with (abrir_cursor or _cursor_por_defecto)() as cursor:
        cursor.execute(SQL_CANDIDATOS)
        procesos = list(cursor.fetchall())
        for proceso in procesos:
            resultado["revisados"] += 1
            d = decidir(proceso)
            if d is None:
                if calcular_plazo(proceso.get("datos_json")) is None:
                    resultado["sin_plazo"] += 1
                continue
            plazo = d["plazo"]
            if d["accion"] == "RELLENAR":
                resultado["rellenados"] += 1
                if aplicar:
                    import json
                    cursor.execute(
                        "UPDATE procesos SET fecha_apertura=%s, fecha_cierre=%s, "
                        "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s::jsonb, updated_at=NOW() "
                        "WHERE id=%s AND fecha_apertura IS NULL AND fecha_cierre IS NULL",
                        (plazo["fecha_apertura"], plazo["fecha_cierre"],
                         json.dumps(_marca_datos(plazo)), proceso["id"]),
                    )
            elif d["accion"] == "DISCREPANCIA":
                resultado["discrepancias"] += 1
            else:
                resultado["parciales"] += 1
            if len(resultado["detalle"]) < LIMITE_DETALLE:
                resultado["detalle"].append({
                    "proceso_id": proceso["id"],
                    "organismo": proceso.get("organismo"),
                    "accion": d["accion"],
                    "guardado": [str(proceso.get("fecha_apertura") or ""), str(proceso.get("fecha_cierre") or "")],
                    "calculado": [plazo["fecha_apertura"].isoformat(), plazo["fecha_cierre"].isoformat()],
                    "literal": f"{plazo['dias']} días {plazo['tipo_dias'].lower()}",
                    "calendario_verificado": plazo["calendario_verificado"],
                })
    return resultado
