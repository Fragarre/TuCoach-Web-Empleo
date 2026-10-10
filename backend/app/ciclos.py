"""Ciclos periódicos: exclusión mutua, ventanas por fuente y evaluación del
resultado.

* ``dias_necesarios``: la ventana de lectura de cada fuente se amplía desde su
  último éxito, de modo que una caída del cron o de una fuente no deja días sin
  leer (antes la ventana era siempre de 7 días).
* ``iniciar_ciclo`` / ``finalizar_ciclo``: un único ciclo EN_CURSO a la vez
  (índice único parcial en ``ciclos_periodicos``); los ciclos que llevan más de
  ``ABANDONO_MINUTOS`` en curso se consideran abandonados (proceso muerto).
* ``evaluar_resultado``: convierte el payload del ciclo en un código de salida
  para que el cron falle de verdad cuando las fuentes fallan.

Si la migración 004 no está aplicada, todo degrada con un aviso visible en el
payload en lugar de romper el ciclo.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import date
from typing import Any, Callable, ContextManager, Iterator, NamedTuple

logger = logging.getLogger(__name__)

ABANDONO_MINUTOS = 90
SQLSTATE_UNICO = "23505"
SQLSTATE_SIN_TABLA = "42P01"


# ------------------------------------------------------------------ ventanas
def dias_necesarios(
    ultimo_exito: date | None,
    hoy: date,
    *,
    solape_minimo: int,
    tope: int,
) -> tuple[int, bool]:
    """Días hacia atrás (contando hoy) que debe leer una fuente.

    Cubre desde el último día completado con éxito más un día de solape, con
    ``solape_minimo`` como suelo. Devuelve ``(dias, excede_tope)``: si se
    excede el tope ordinario se lee solo el tope y se avisa de que hace falta
    una revisión histórica para no perder publicaciones.
    """
    if ultimo_exito is None:
        return solape_minimo, False
    transcurridos = (hoy - ultimo_exito).days
    dias = max(solape_minimo, transcurridos + 1)
    if dias > tope:
        return tope, True
    return dias, False


# ----------------------------------------------------------------- evaluación
class Evaluacion(NamedTuple):
    codigo: int            # 0 = correcto, 1 = hay que alertar
    nivel: str             # OK | AVISO | ERROR
    mensajes: list[str]


def evaluar_resultado(payload: dict[str, Any], *, errores_tolerados: int = 0) -> Evaluacion:
    """Decide si un ciclo se considera correcto a efectos del cron."""
    mensajes: list[str] = []
    ciclo = payload.get("ciclo") or {}
    if ciclo.get("omitido"):
        return Evaluacion(0, "AVISO", [f"Ciclo omitido: {ciclo.get('motivo', 'otro ciclo en curso')}"])

    resumen = payload.get("resumen_fuentes") or {}
    total = int(resumen.get("total") or 0)
    errores = int(resumen.get("errores") or 0)
    estados = payload.get("estado_fuentes") or {}

    if total == 0:
        return Evaluacion(1, "ERROR", ["El ciclo no ejecutó ninguna fuente"])

    fallidas = sorted(n for n, e in estados.items() if (e or {}).get("estado") == "ERROR")
    degradadas = sorted(n for n, e in estados.items() if (e or {}).get("estado") == "DEGRADADA")
    if degradadas:
        mensajes.append("Fuentes degradadas: " + ", ".join(degradadas))
    if ciclo.get("disponible") is False:
        mensajes.append("Sin tablas de ciclos (migración 004): sin exclusión mutua ni ventanas por fuente")
    for nombre, v in (payload.get("ventanas_excedidas") or {}).items():
        mensajes.append(f"Ventana de {nombre} excede el tope ordinario: ejecutar revisión histórica")

    if errores > errores_tolerados:
        detalle = ", ".join(
            f"{n} ({(estados[n] or {}).get('error', 'sin detalle')[:120]})" for n in fallidas
        )
        prefijo = "TODAS las fuentes fallaron" if errores >= total else f"{errores} de {total} fuentes fallaron"
        return Evaluacion(1, "ERROR", [f"{prefijo}: {detalle}", *mensajes])
    return Evaluacion(0, "AVISO" if mensajes else "OK", mensajes)


def errores_tolerados_env() -> int:
    try:
        return max(0, int(os.getenv("EMPLOYMENT_CRON_ERRORES_TOLERADOS", "0")))
    except ValueError:
        return 0


# ------------------------------------------------------------------ base de datos
def _jsonb(valor: Any) -> Any:
    from psycopg.types.json import Jsonb
    return Jsonb(valor)


@contextmanager
def _abrir_cursor() -> Iterator[Any]:
    from psycopg.rows import dict_row
    from .database import get_connection
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        yield cursor


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)


Abrir = Callable[[], ContextManager[Any]]


def iniciar_ciclo(*, historico: bool = False, abrir: Abrir | None = None) -> tuple[int | None, str]:
    """Reserva el ciclo. Devuelve ``(id, estado)`` con estado INICIADO,
    OMITIDO_CICLO_EN_CURSO o SIN_TABLAS (migración 004 pendiente)."""
    try:
        with (abrir or _abrir_cursor)() as cursor:
            cursor.execute(
                "UPDATE ciclos_periodicos SET estado='ABANDONADO', fin=NOW() "
                "WHERE estado='EN_CURSO' AND inicio < NOW() - make_interval(mins => %s)",
                (ABANDONO_MINUTOS,),
            )
            try:
                cursor.execute(
                    "INSERT INTO ciclos_periodicos (historico) VALUES (%s) RETURNING id",
                    (historico,),
                )
            except Exception as exc:  # noqa: BLE001
                if _sqlstate(exc) == SQLSTATE_UNICO:
                    return None, "OMITIDO_CICLO_EN_CURSO"
                raise
            return int(cursor.fetchone()["id"]), "INICIADO"
    except Exception as exc:  # noqa: BLE001
        if _sqlstate(exc) == SQLSTATE_SIN_TABLA:
            logger.warning("ciclos_periodicos no existe: aplicar database/004_ciclos_periodicos.sql")
            return None, "SIN_TABLAS"
        raise


def finalizar_ciclo(ciclo_id: int | None, estado: str, resumen: dict[str, Any], *, abrir: Abrir | None = None) -> None:
    if ciclo_id is None:
        return
    with (abrir or _abrir_cursor)() as cursor:
        cursor.execute(
            "UPDATE ciclos_periodicos SET estado=%s, fin=NOW(), resumen=%s WHERE id=%s",
            (estado, _jsonb(resumen), ciclo_id),
        )


def registrar_ejecuciones(ciclo_id: int | None, ejecuciones: list[dict[str, Any]], *, abrir: Abrir | None = None) -> None:
    if ciclo_id is None or not ejecuciones:
        return
    with (abrir or _abrir_cursor)() as cursor:
        for e in ejecuciones:
            cursor.execute(
                "INSERT INTO ejecuciones_fuente "
                "(ciclo_id, fuente, estado, duracion_s, hasta_fecha, dias_ventana, error) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (ciclo_id, e["fuente"], e["estado"], e.get("duracion_s"), e.get("hasta_fecha"),
                 e.get("dias_ventana"), e.get("error")),
            )


def ultimos_exitos(*, abrir: Abrir | None = None) -> dict[str, date]:
    """Último día cubierto con éxito por cada fuente ({} si no hay tablas)."""
    try:
        with (abrir or _abrir_cursor)() as cursor:
            cursor.execute(
                "SELECT fuente, MAX(hasta_fecha) AS hasta FROM ejecuciones_fuente "
                "WHERE estado IN ('OK','SIN_NOVEDADES') AND hasta_fecha IS NOT NULL GROUP BY fuente"
            )
            return {f["fuente"]: f["hasta"] for f in cursor.fetchall() if f["hasta"]}
    except Exception as exc:  # noqa: BLE001
        if _sqlstate(exc) == SQLSTATE_SIN_TABLA:
            return {}
        raise
