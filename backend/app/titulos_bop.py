"""Denominación legible de las convocatorias locales cargadas desde el BOP.

Los anuncios del BOP de Valencia llegan como título completo del edicto, p. ej.
"Anunci de l'Ajuntament de Cerdà sobre l'aprovació de les bases ... per a la
provisió d'una plaça d'administratiu/va, per concurs oposició, ...". Aquí se
extrae solo el puesto y se pasa al castellano ("Administrativo/a"). El título
original se conserva en ``datos_json.denominacion_original``.

No toca la Diputación: su lector del BOP reescribe el título en cada ciclo
(y registra el cambio), así que ambos se pisarían.

Solo actúa sobre denominaciones que empiezan por "Anunci"/"Anuncio" (o cuyo
original ya se guardó) y solo cambia el texto cuando se reconoce el puesto sin
restos en valenciano.

También lee del título el número de plazas ("dues places", "set llocs", "el
lloc" = 1) y lo guarda únicamente si el proceso no tiene plazas. Con "diverses
places" (varias) no inventa nada.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

_RE_PUESTO = re.compile(
    r"\b(?:places?|plaça|plazas?|llocs?|lugar(?:es)?|puestos?)\s+"
    r"(?:vacants?\s+|vacantes\s+)?(?:(?:de|d'|per\s+a|para)\s*)"
    r"(.+?)"
    r"(?=,?\s+(?:mitjan[cç]ant|per\s+concurs|per\s+oposici|de\s+l'oferta|pel\s+sistema|"
    r"per\s+torn|mediante|por\s+concurso|por\s+oposici|de\s+la\s+oferta)|\.\s*$|$)",
    re.I,
)

# (patrón valenciano, castellano). Se aplican por orden: primero lo más largo.
_TRADUCCIONES = (
    (r"t[eè]cnic/a\s+mitj[aà]/na", "técnico/a medio/a"),
    (r"t[eè]cnic/a\s+superior", "técnico/a superior"),
    (r"t[eè]cnic/a", "técnico/a"),
    (r"administratiu/va", "administrativo/a"),
    (r"cap\s+de\s+negociat", "jefe/a de negociado"),
    (r"administraci[oó]\s+general", "Administración General"),
    (r"pad[rò]n?[oó]?\s+d'habitants", "Padrón de habitantes"),
    (r"pad[ró]\s+d'habitants", "Padrón de habitantes"),
    (r"\bi\b", "y"),
    (r"\bd'", "de "),
)

_RESTOS_VALENCIANO = re.compile(r"[àèòçï·]|\b(?:lloc|plaça|per|amb|dels?|les|els|una?|mitjan[cç]ant)\b(?!\w)", re.I)


def limpiar_titulo(denominacion: str | None) -> str | None:
    """Puesto en castellano o None si no es un título de anuncio reconocible."""
    texto = (denominacion or "").strip()
    if not re.match(r"(?i)^anunci(?:o)?\b", texto):
        return None
    m = _RE_PUESTO.search(texto)
    if not m:
        return None
    puesto = m.group(1).strip().rstrip(".").strip()
    puesto = re.sub(r",\s*[ABC][12]?\s*$", "", puesto)  # ", C1" final
    puesto = re.sub(r"(?i)^personal\s+", "", puesto)
    for patron, castellano in _TRADUCCIONES:
        puesto = re.sub(patron, castellano, puesto, flags=re.I)
    puesto = re.sub(r"\s+", " ", puesto).strip(" ,")
    if not puesto or len(puesto) > 120 or _RESTOS_VALENCIANO.search(puesto):
        return None
    return puesto[0].upper() + puesto[1:]


_NUM = {
    "un": 1, "una": 1, "uno": 1, "dues": 2, "dos": 2, "tres": 3, "quatre": 4, "cuatro": 4,
    "cinc": 5, "cinco": 5, "sis": 6, "seis": 6, "set": 7, "siete": 7, "vuit": 8, "ocho": 8,
    "nou": 9, "nueve": 9, "deu": 10, "diez": 10, "onze": 11, "once": 11, "dotze": 12, "doce": 12,
    "tretze": 13, "trece": 13, "catorze": 14, "catorce": 14, "quinze": 15, "quince": 15,
    "setze": 16, "dieciseis": 16, "disset": 17, "diecisiete": 17, "divuit": 18, "dieciocho": 18,
    "dinou": 19, "diecinueve": 19, "vint": 20, "veinte": 20,
}
_RE_NUMERO_PLAZAS = re.compile(
    r"\b(vint-i-[a-z]+|veinti[a-z]+|\d{1,3}|[a-z]+)\s+(?:places?|plaça|plazas?|llocs?|puestos?)\b", re.I)
_RE_UNA_PLAZA = re.compile(r"\b(?:la|el|del|de\s+la)\s+(?:plaça|plaza|lloc|puesto)\b", re.I)


def _valor_numero(palabra: str) -> int | None:
    p = palabra.lower()
    if p.isdigit():
        return int(p)
    if p.startswith("vint-i-"):
        unidad = _NUM.get(p[len("vint-i-"):])
        return 20 + unidad if unidad and unidad < 10 else None
    return _NUM.get(p)


def plazas_del_titulo(denominacion: str | None) -> int | None:
    """Número de plazas que declara el título, o None si no es inequívoco."""
    texto = (denominacion or "").strip()
    if not re.match(r"(?i)^anunci(?:o)?\b", texto):
        return None
    for m in _RE_NUMERO_PLAZAS.finditer(texto):
        valor = _valor_numero(m.group(1))
        if valor and 0 < valor <= 200:
            return valor
    if re.search(r"(?i)\b(?:diverses|varias|diversas)\s+(?:places?|plazas?|llocs?)\b", texto):
        return None
    if _RE_UNA_PLAZA.search(texto):
        return 1
    return None


SQL_CANDIDATOS = (
    "SELECT p.id, p.denominacion, p.plazas, p.datos_json->>'denominacion_original' AS original, "
    "o.nombre AS organismo "
    "FROM procesos p JOIN organismos o ON o.id = p.organismo_id "
    "WHERE o.tipo IN ('AYUNTAMIENTO','MANCOMUNIDAD') "
    "AND (p.denominacion ILIKE 'Anunci%%' OR p.denominacion ILIKE 'Anuncio%%' "
    "     OR p.datos_json ? 'denominacion_original') "
    "ORDER BY p.id"
)


def limpiar_titulos_bop(*, aplicar: bool = False, abrir_cursor=None) -> dict[str, Any]:
    @contextmanager
    def _cursor_por_defecto():
        from psycopg.rows import dict_row
        from .database import get_connection
        with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            yield cursor

    r: dict[str, Any] = {"modo": "APLICADO" if aplicar else "SOLO_REVISION",
                         "revisados": 0, "limpiados": 0, "plazas_rellenadas": 0,
                         "sin_cambio": 0, "detalle": []}
    with (abrir_cursor or _cursor_por_defecto)() as cursor:
        cursor.execute(SQL_CANDIDATOS)
        for proceso in list(cursor.fetchall()):
            r["revisados"] += 1
            actual = proceso.get("denominacion") or ""
            original = proceso.get("original") or actual
            es_bruto = bool(re.match(r"(?i)^anunci(?:o)?\b", actual))
            nuevo = limpiar_titulo(original) if es_bruto else None
            plazas = plazas_del_titulo(original) if not proceso.get("plazas") else None
            fila = {"proceso_id": proceso["id"], "organismo": proceso.get("organismo"),
                    "antes": actual, "despues": nuevo, "plazas": plazas}
            if not nuevo and not plazas:
                r["sin_cambio"] += 1
                r["detalle"].append(fila)
                continue
            if nuevo:
                r["limpiados"] += 1
            if plazas:
                r["plazas_rellenadas"] += 1
            if aplicar:
                if nuevo:
                    cursor.execute(
                        "UPDATE procesos SET denominacion=%s, "
                        "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s::jsonb, updated_at=NOW() "
                        "WHERE id=%s AND (denominacion ILIKE 'Anunci%%' OR denominacion ILIKE 'Anuncio%%')",
                        (nuevo, json.dumps({"denominacion_original": actual}), proceso["id"]),
                    )
                if plazas:
                    cursor.execute(
                        "UPDATE procesos SET plazas=%s, updated_at=NOW() "
                        "WHERE id=%s AND (plazas IS NULL OR plazas = 0)",
                        (plazas, proceso["id"]),
                    )
            r["detalle"].append(fila)
    return r
