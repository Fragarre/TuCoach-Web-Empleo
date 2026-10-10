"""Calendario de días inhábiles de la Comunitat Valenciana y fecha de referencia.

Sustituye al diccionario fijo de 2026 de ``estado_proceso``. Problemas que
resuelve:

* Desde enero de 2027 el cálculo de plazos dejaba de funcionar (no había
  calendario) y el botón "en plazo" perdía las convocatorias con plazo literal.
* Un plazo que empieza en diciembre y cruza a enero usaba solo los festivos del
  año de publicación. Ahora se consulta cada día con el calendario de SU año.

Los festivos se calculan por reglas (fechas fijas + Viernes Santo + Lunes de
Pascua), que reproducen exactamente la lista oficial de 2026 usada hasta ahora.
Los años calculados NO están verificados contra el DOGV: las comunidades pueden
trasladar un festivo que cae en domingo. Para fijar un año con el calendario
oficial basta rellenar ``CALENDARIOS_VERIFICADOS``; mientras tanto, la
respuesta marca ``calendario_verificado = False`` y el plazo debe mostrarse con
la advertencia de confirmar días inhábiles.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

# Calendarios oficiales ya contrastados (tienen prioridad sobre el cálculo).
# 2026: lista que ya usaba el sistema.
CALENDARIOS_VERIFICADOS: dict[int, frozenset[date]] = {
    2026: frozenset({
        date(2026, 1, 1), date(2026, 1, 6), date(2026, 3, 19),
        date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1),
        date(2026, 6, 24), date(2026, 8, 15), date(2026, 10, 9),
        date(2026, 10, 12), date(2026, 12, 8), date(2026, 12, 25),
    }),
}

# Festivos fijos anuales (mes, día): Año Nuevo, Reyes, San José, Trabajo,
# San Juan, Asunción, 9 d'Octubre, Fiesta Nacional, Todos los Santos,
# Inmaculada y Navidad.
_FIJOS = ((1, 1), (1, 6), (3, 19), (5, 1), (6, 24), (8, 15),
          (10, 9), (10, 12), (11, 1), (12, 8), (12, 25))


def pascua(anio: int) -> date:
    """Domingo de Pascua (algoritmo de Meeus/Jones/Butcher)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


@lru_cache(maxsize=64)
def festivos_cv(anio: int) -> frozenset[date]:
    """Festivos del año: calendario verificado si existe; si no, calculado."""
    if anio in CALENDARIOS_VERIFICADOS:
        return CALENDARIOS_VERIFICADOS[anio]
    p = pascua(anio)
    calculados = {date(anio, m, d) for m, d in _FIJOS}
    calculados.add(p - timedelta(days=2))   # Viernes Santo
    calculados.add(p + timedelta(days=1))   # Lunes de Pascua
    return frozenset(calculados)


def calendario_verificado(anio: int) -> bool:
    return anio in CALENDARIOS_VERIFICADOS


def es_dia_habil(dia: date) -> bool:
    """Lunes a viernes no festivo (festivos estatales/autonómicos, no locales)."""
    return dia.weekday() < 5 and dia not in festivos_cv(dia.year)


def sumar_dias_habiles(inicio: date, dias: int) -> date:
    """Último día de un plazo de ``dias`` hábiles contados desde el siguiente a
    ``inicio``. Consulta cada día con el calendario de su propio año."""
    if dias <= 0:
        raise ValueError("dias debe ser positivo")
    actual, contados = inicio, 0
    while contados < dias:
        actual += timedelta(days=1)
        if es_dia_habil(actual):
            contados += 1
    return actual


def anios_tocados(inicio: date, fin: date) -> set[int]:
    return set(range(inicio.year, fin.year + 1))


def hoy_es() -> date:
    """Fecha actual en España. Render funciona en UTC: entre las 00:00 y las
    02:00 hora peninsular ``date.today()`` devolvería todavía el día anterior."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid")).date()
    except Exception:  # sin base de datos de zonas horarias: mejor algo que nada
        return date.today()
