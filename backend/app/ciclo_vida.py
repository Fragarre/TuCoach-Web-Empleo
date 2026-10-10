"""Ciclo de vida de una convocatoria: ÚNICA fuente de verdad.

Definición de negocio (acordada):

    Una convocatoria está ACTIVA mientras no se haya publicado su resultado
    final. Es independiente de la fecha de publicación y del plazo de
    inscripción (que se deriva aparte en ``estado_proceso.estado_inscripcion``).

Un proceso se da de baja cuando una publicación OFICIAL acredita alguno de:

* FINALIZADO: relación definitiva de aprobados / aspirantes que han superado
  el proceso selectivo, propuesta de nombramiento del tribunal, finalización
  del proceso, proceso declarado desierto, nombramiento como funcionario de
  carrera, toma de posesión o adjudicación definitiva de destinos.
* ANULADO: anulación de LA CONVOCATORIA / bases / proceso (no de una pregunta,
  un ejercicio o un tribunal).
* DESISTIDO: desistimiento DE LA CONVOCATORIA o del proceso (no de un
  aspirante).

NO dan de baja: listas provisionales, resultados de un ejercicio o fase
intermedia, listas de admitidos, anulación de una pregunta, corrección de
errores, desistimiento de un aspirante, nombramientos de personal interino,
y en las bolsas de trabajo cualquier relación de aprobados (la bolsa sigue
vigente).

Este módulo es puro (sin red ni base de datos) para poder probarlo y para que
todos los conectores compartan exactamente la misma definición.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, NamedTuple

ESTADO_EN_CURSO = "EN_CURSO"
ESTADO_FINALIZADO = "FINALIZADO"
ESTADO_ANULADO = "ANULADO"
ESTADO_DESISTIDO = "DESISTIDO"

# Valores en minúsculas porque así se comparan en BD (LOWER(estado)) y en
# Python. Incluye variantes históricas en valenciano.
ESTADOS_TERMINALES: frozenset[str] = frozenset({
    "finalizado", "finalitzado", "finalitzat",
    "cancelado", "cancel·lado", "cancel·lat", "cancelat",
    "desistido", "desistit",
    "anulado", "anul·lat", "anullat",
})

# Tupla ordenada: para parámetros SQL (``NOT IN (%s, %s, ...)``).
ESTADOS_TERMINALES_SQL: tuple[str, ...] = tuple(sorted(ESTADOS_TERMINALES))

# Literal SQL ya formado, solo para constantes internas (nunca datos externos).
ESTADOS_TERMINALES_SQL_IN: str = "(" + ",".join(f"'{e}'" for e in ESTADOS_TERMINALES_SQL) + ")"


def es_estado_terminal(estado: Any) -> bool:
    """True si el estado almacenado corresponde a un proceso ya resuelto."""
    return str(estado or "").strip().lower() in ESTADOS_TERMINALES


def es_activo(estado: Any) -> bool:
    """Una convocatoria está activa mientras su estado no sea terminal."""
    return not es_estado_terminal(estado)


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin acentos ni signos; ``anul·lació`` -> ``anullacio``."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("·", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def es_bolsa(tipo_proceso: str | None) -> bool:
    n = normalizar(tipo_proceso)
    return "bolsa" in n or "borsa" in n


class EventoCierre(NamedTuple):
    estado: str   # FINALIZADO | ANULADO | DESISTIDO
    regla: str    # identificador de la regla que lo detectó (trazabilidad)


# ---------------------------------------------------------------- patrones --
_ART = r"(?:(?:la|el|las|los|les|els|l)\s+)?"
_PRES = r"(?:presente\s+)?"

# Anulación / desistimiento: el OBJETO debe ser el proceso, no un acto menor.
_OBJ_ES = (r"(?:convocatoria|proceso selectivo|proceso de seleccion|bases|bolsa|"
           r"oposicion|concurso oposicion|oferta de empleo)")
_OBJ_VA = (r"(?:convocatoria|proces selectiu|proces de seleccio|bases|borsa|"
           r"oposicio|concurs oposicio)")
_OBJ = rf"(?:{_OBJ_ES}|{_OBJ_VA})"

_RE_ANULADO = re.compile(
    rf"\banul\w*\s+(?:de\s+|del\s+|d\s+)?{_ART}{_PRES}{_OBJ}\b"
    rf"|\bsin efecto\s+{_ART}{_PRES}(?:convocatoria|proceso selectivo|proces selectiu)\b"
    rf"|\bsense efecte\s+{_ART}{_PRES}(?:convocatoria|proces selectiu)\b"
)
_RE_DESISTIDO = re.compile(
    rf"\bdesist\w*\s+(?:de\s+|del\s+|d\s+)?{_ART}{_PRES}"
    rf"(?:convocatoria|proceso selectivo|proceso de seleccion|proces selectiu|"
    rf"proces de seleccio|bolsa|borsa)\b"
)

# Finalización explícita del proceso.
_RE_FINALIZACION = re.compile(
    r"\b(?:finalizacion|finalitzacio)\s+del?\s+proc(?:eso|es)\b"
    r"(?!\s+de\s+(?:alegacion|reclamacion|inscripcion|admision|solicitud|presentacion))"
)

# Proceso declarado desierto (cierra sin adjudicar). Orden estricto para no
# confundirlo con "desierta una plaza de la convocatoria".
_RE_DESIERTO = re.compile(
    rf"\bdeclar\w*\s+desiert[oa]s?\s+{_ART}{_PRES}(?:proceso selectivo|convocatoria)\b"
    rf"|\bdeclar\w*\s+desert[a]?\s+{_ART}{_PRES}(?:proces selectiu|convocatoria)\b"
)

# Nombramiento / toma de posesión / adjudicación definitiva (acto final).
_RE_NOMBRAMIENTO = re.compile(
    r"\bnombramiento\s+(?:como|de)\s+funcionari[oa]s?\b"
    r"|\bnomenament\s+(?:com a|de)\s+funcionari[s]?\b"
    r"|\bnombramiento\s+mediante\s+concurso\b|\bnomenament\s+mitjancant\s+concurs\b"
    r"|\btoma de posesion\b|\bpresa de possessio\b"
    r"|\badjudicacion\s+definitiva\b|\badjudicacio\s+definitiva\b"
    r"|\badjudicacion\s+de\s+destinos\b|\badjudicacio\s+de\s+destinacions\b"
)
_RE_INTERINO = re.compile(r"\binterin\w*|\binteri\b|\binterins\b")

# Resultado FINAL del proceso selectivo.
_APROBADOS = (r"(?:aprobad[oa]s?|aprovad[oa]s?|aprovat[s]?|superado|superat|superan|"
              r"superen|seleccionad[oa]s?|seleccionat[s]?)")
_LISTA = r"(?:relacion|lista|listado|llista|relacio)"
_RE_RESULTADO = (
    re.compile(rf"\b{_LISTA}\b.{{0,90}}?\b{_APROBADOS}\b"),
    re.compile(rf"\bpropuest\w*\b.{{0,70}}?\b{_APROBADOS}\b"),
    re.compile(rf"\bproposta\b.{{0,70}}?\b{_APROBADOS}\b"),
    re.compile(r"\bpropuesta\s+de\s+nombramiento\b|\bproposta\s+de\s+nomenament\b"),
    re.compile(rf"\b{_APROBADOS}\s+(?:en|del?|d)\s+{_ART}proc(?:eso|es)\s+selecti[vu]\w*\b"),
    re.compile(r"\bresultado final\b|\bresultat final\b"
               r"|\b(?:calificacion|puntuacion|calificacio|puntuacio) final del proc(?:eso|es)\b"),
)

# Lo que convierte un "resultado" en PARCIAL (no cierra) o en PROVISIONAL.
_RE_PROVISIONAL = re.compile(r"\bprovisional\w*\b|\bprovisoria\w*\b")
# "Se anula la lista de aprobados", "deja sin efecto...", "suspensión...": no cierran.
_RE_REVOCA = re.compile(r"\banul\w*|\bsin efecto\b|\bsense efecte\b|\brevoca\w*|\bsuspen\w*")
_RE_PARCIAL = re.compile(
    r"\bejercicios?\b(?!\s+(?:de\s+|del\s+)?\d{4})|\bexercicis?\b"
    r"|\bfases?\b"
    r"|\b(?:primer|segund|tercer|cuart|quint|sext)[oa]?\s+(?:prueba|prova|parte)\b"
    r"|\bprueba\s+(?:de|practica|fisica|psicotecnica|medica)\b|\bprova\s+(?:de|practica)\b"
    r"|\bpregunta\w*\b|\bexamen\w*\b|\bentrevista\b|\btest\b"
)


def _es_resultado_final(n: str) -> str | None:
    if _RE_PROVISIONAL.search(n) or _RE_PARCIAL.search(n) or _RE_REVOCA.search(n):
        return None
    for i, patron in enumerate(_RE_RESULTADO, start=1):
        if patron.search(n):
            return f"RESULTADO_FINAL_{i}"
    return None


def clasificar_evento(tipo_proceso: str | None, titulo: str | None) -> EventoCierre | None:
    """Devuelve el evento de cierre acreditado por un título oficial, o None.

    Prioridad: anulación > desistimiento > resultado/finalización.
    """
    n = normalizar(titulo)
    if not n:
        return None
    if _RE_ANULADO.search(n):
        return EventoCierre(ESTADO_ANULADO, "ANULACION_CONVOCATORIA")
    if _RE_DESISTIDO.search(n):
        return EventoCierre(ESTADO_DESISTIDO, "DESISTIMIENTO_CONVOCATORIA")

    bolsa = es_bolsa(tipo_proceso)
    if _RE_FINALIZACION.search(n):
        return EventoCierre(ESTADO_FINALIZADO, "FINALIZACION_PROCESO")
    if _RE_DESIERTO.search(n):
        return EventoCierre(ESTADO_FINALIZADO, "PROCESO_DESIERTO")
    if _RE_NOMBRAMIENTO.search(n) and not _RE_INTERINO.search(n):
        return EventoCierre(ESTADO_FINALIZADO, "NOMBRAMIENTO_O_ADJUDICACION")
    # Una bolsa no "se resuelve" con una relación de aprobados: sigue vigente.
    if not bolsa and not _RE_INTERINO.search(n):
        regla = _es_resultado_final(n)
        if regla:
            return EventoCierre(ESTADO_FINALIZADO, regla)
    return None


_ETIQUETAS = (
    (re.compile(r"^(?:proceso |proces )?(?:anulad[oa]|anullat|anullada|anul lat|anul lada)$"), ESTADO_ANULADO),
    (re.compile(r"^(?:proceso |proces )?(?:desistid[oa]|desistit|desistida)$"), ESTADO_DESISTIDO),
    (re.compile(r"^(?:proceso |proces )?(?:finalizad[oa]|finalitzat|finalitzada|resuelt[oa]|resolt|resolta|desiert[oa]|desert|deserta)$"), ESTADO_FINALIZADO),
)


def clasificar_etiqueta_estado(etiqueta: str | None) -> str | None:
    """Para CAMPOS DE ESTADO estructurados de una sede ("Anulada", "Finalizado"),
    no para títulos de publicaciones: exige que la etiqueta sea solo el estado."""
    n = normalizar(etiqueta)
    if not n:
        return None
    for patron, estado in _ETIQUETAS:
        if patron.match(n):
            return estado
    return None


def clasificar_evento_terminal(tipo_proceso: str | None, titulo: str | None) -> str | None:
    """Compatibilidad: devuelve solo el estado terminal (o None)."""
    evento = clasificar_evento(tipo_proceso, titulo)
    return evento.estado if evento else None


def elegir_evidencia(publicaciones: Iterable[dict[str, Any]], tipo_proceso: str | None) -> dict[str, Any] | None:
    """Elige la publicación que mejor acredita el cierre de un proceso.

    Cada publicación es un dict con ``id``, ``titulo`` y ``fecha_publicacion``
    (date, str ISO o None). Prioriza anulación/desistimiento sobre resultado y,
    a igualdad, la más reciente.
    """
    prioridad = {ESTADO_ANULADO: 0, ESTADO_DESISTIDO: 1, ESTADO_FINALIZADO: 2}
    candidatas: list[tuple[int, str, int, dict[str, Any], EventoCierre]] = []
    for pub in publicaciones:
        evento = clasificar_evento(tipo_proceso, pub.get("titulo"))
        if evento is None:
            continue
        fecha = str(pub.get("fecha_publicacion") or "")[:10]
        candidatas.append((prioridad[evento.estado], fecha, int(pub.get("id") or 0), pub, evento))
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (c[0], _invertir(c[1]), -c[2]))
    _, _, _, pub, evento = candidatas[0]
    return {
        "estado": evento.estado,
        "regla": evento.regla,
        "publicacion_id": pub.get("id"),
        "titulo": pub.get("titulo"),
        "fecha_publicacion": str(pub.get("fecha_publicacion") or "")[:10] or None,
    }


def _invertir(fecha_iso: str) -> str:
    """Clave de orden descendente para fechas ISO (cadena vacía al final)."""
    if not fecha_iso:
        return "~"
    return "".join(chr(ord("9") - int(c)) if c.isdigit() else c for c in fecha_iso)
