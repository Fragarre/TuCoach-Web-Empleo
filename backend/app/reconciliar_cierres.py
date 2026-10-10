"""Reconciliación de cierres: da de baja las convocatorias cuyo resultado ya
consta en las publicaciones guardadas.

Por qué existe: cada conector decidía por su cuenta si un título cerraba un
proceso y solo lo evaluaba en la ventana de días que estaba leyendo. Una
publicación de resultado ingerida en un momento en que el proceso aún no
estaba vinculado, o con un clasificador distinto, dejaba la convocatoria
"activa" para siempre. Aquí la decisión se toma una sola vez, sobre TODAS las
publicaciones del proceso, con la definición única de ``ciclo_vida``.

* Idempotente: ejecutarla dos veces no cambia nada la segunda.
* No necesita red: solo lee y escribe la base de datos.
* Reversible: guarda en ``datos_json.cierre`` la regla y la publicación que
  cerraron el proceso; si esa evidencia deja de sostenerse (p. ej. se corrige
  el clasificador), el proceso se reabre. Solo reabre cierres hechos por esta
  rutina, nunca los de una fuente estructurada ni los manuales.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, ContextManager, Iterator

from .ciclo_vida import (
    ESTADO_EN_CURSO,
    ESTADOS_TERMINALES_SQL_IN,
    elegir_evidencia,
    es_activo,
)

ORIGEN = "RECONCILIACION"
LIMITE_DETALLE = 100


def _marca_cierre(proceso: dict[str, Any]) -> dict[str, Any] | None:
    datos = proceso.get("datos_json")
    cierre = datos.get("cierre") if isinstance(datos, dict) else None
    return cierre if isinstance(cierre, dict) else None


def decidir(proceso: dict[str, Any], publicaciones: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Decisión pura (sin BD) para un proceso: CERRAR, REABRIR o None."""
    evidencia = elegir_evidencia(publicaciones, proceso.get("tipo_proceso"))
    estado = proceso.get("estado")
    if es_activo(estado):
        if evidencia:
            return {"accion": "CERRAR", "estado_anterior": estado, **evidencia}
        return None
    marca = _marca_cierre(proceso)
    if marca and marca.get("origen") == ORIGEN and evidencia is None:
        return {"accion": "REABRIR", "estado_anterior": estado, "estado": ESTADO_EN_CURSO,
                "motivo": "La evidencia de cierre guardada ya no se sostiene"}
    return None


def _jsonb(valor: Any) -> Any:
    from psycopg.types.json import Jsonb
    return Jsonb(valor)


@contextmanager
def _abrir_cursor() -> Iterator[Any]:
    from psycopg.rows import dict_row
    from .database import get_connection
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        yield cursor


def planificar(cursor: Any) -> list[dict[str, Any]]:
    """Lee procesos y publicaciones y devuelve las acciones necesarias."""
    cursor.execute(
        "SELECT id, estado, tipo_proceso, datos_json FROM procesos "
        "WHERE es_oportunidad = TRUE ORDER BY id"
    )
    procesos = list(cursor.fetchall())
    candidatos = [
        p for p in procesos
        if es_activo(p["estado"]) or (_marca_cierre(p) or {}).get("origen") == ORIGEN
    ]
    if not candidatos:
        return []
    cursor.execute(
        "SELECT id, proceso_id, titulo, fecha_publicacion FROM publicaciones "
        "WHERE proceso_id = ANY(%s) ORDER BY proceso_id, id",
        ([p["id"] for p in candidatos],),
    )
    por_proceso: dict[int, list[dict[str, Any]]] = {}
    for fila in cursor.fetchall():
        por_proceso.setdefault(int(fila["proceso_id"]), []).append(fila)

    acciones: list[dict[str, Any]] = []
    for proceso in candidatos:
        decision = decidir(proceso, por_proceso.get(int(proceso["id"]), []))
        if decision:
            acciones.append({"proceso_id": int(proceso["id"]), **decision})
    return acciones


def _aplicar(cursor: Any, accion: dict[str, Any], *, notificable: bool) -> bool:
    pid = accion["proceso_id"]
    if accion["accion"] == "CERRAR":
        cursor.execute(
            "UPDATE procesos SET estado=%s, "
            "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s, updated_at=NOW() "
            "WHERE id=%s AND LOWER(COALESCE(estado,'')) NOT IN " + ESTADOS_TERMINALES_SQL_IN,
            (
                accion["estado"],
                _jsonb({"cierre": {
                    "origen": ORIGEN,
                    "regla": accion["regla"],
                    "publicacion_id": accion["publicacion_id"],
                    "titulo": accion["titulo"],
                    "fecha_publicacion": accion["fecha_publicacion"],
                }}),
                pid,
            ),
        )
        if not cursor.rowcount:
            return False
        cursor.execute(
            "INSERT INTO cambios (proceso_id,publicacion_id,tipo,campo,valor_anterior,"
            "valor_nuevo,resumen,significativo) VALUES (%s,%s,'ACTUALIZACION','estado',%s,%s,%s,%s)",
            (pid, accion["publicacion_id"], accion["estado_anterior"], accion["estado"],
             "Proceso selectivo resuelto según publicación oficial", notificable),
        )
        return True

    cursor.execute(
        "UPDATE procesos SET estado=%s, datos_json = COALESCE(datos_json,'{}'::jsonb) - 'cierre', "
        "updated_at=NOW() WHERE id=%s",
        (ESTADO_EN_CURSO, pid),
    )
    if not cursor.rowcount:
        return False
    cursor.execute(
        "INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo) "
        "VALUES (%s,'ACTUALIZACION','estado',%s,%s,%s,FALSE)",
        (pid, accion["estado_anterior"], ESTADO_EN_CURSO, accion["motivo"]),
    )
    return True


def reconciliar_cierres(
    *,
    aplicar: bool = False,
    historico: bool = False,
    abrir: Callable[[], ContextManager[Any]] | None = None,
    limite_detalle: int | None = LIMITE_DETALLE,
) -> dict[str, Any]:
    """Planifica y, con ``aplicar``, ejecuta cierres y reaperturas.

    En modo histórico los cambios se guardan como no significativos para que la
    carga inicial no genere avisos a usuarios.
    """
    with (abrir or _abrir_cursor)() as cursor:
        acciones = planificar(cursor)
        aplicadas = 0
        if aplicar:
            for accion in acciones:
                if _aplicar(cursor, accion, notificable=not historico):
                    aplicadas += 1
    cierres = [a for a in acciones if a["accion"] == "CERRAR"]
    reaperturas = [a for a in acciones if a["accion"] == "REABRIR"]
    por_estado: dict[str, int] = {}
    for a in cierres:
        por_estado[a["estado"]] = por_estado.get(a["estado"], 0) + 1
    return {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "escrituras_bd": aplicar,
        "cierres": len(cierres),
        "cierres_por_estado": por_estado,
        "reaperturas": len(reaperturas),
        "aplicadas": aplicadas,
        "detalle": [
            {k: a.get(k) for k in ("proceso_id", "accion", "estado_anterior", "estado", "regla", "titulo", "fecha_publicacion")}
            for a in (acciones if limite_detalle is None else acciones[:limite_detalle])
        ],
    }
