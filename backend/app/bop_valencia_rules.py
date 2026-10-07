from __future__ import annotations

from . import bop_valencia as _bop


_TERMINOS_NO_EMPLEO = (
    "subvencion", "subvencio", "subvencions", "subvenciones",
    "ayuda", "ayudas", "ajuda", "ajudes",
    "premio", "premios", "premi", "premis",
    "bdns", "concessio de subvencions", "concesion de subvenciones",
    "justificacio de la convocatoria", "justificacion de la convocatoria",
    "concurs de merits per a cobrir el lloc", "concurs de mèrits per a cobrir el lloc",
    "concurso de meritos para cubrir el puesto", "concurso de méritos para cubrir el puesto",
    "concurs de merits per a la provisio", "concurs de mèrits per a la provisió",
    "concurso de meritos para la provision", "concurso de méritos para la provisión",
    "concurs de merits", "concurs de mèrits",
    "concurso de meritos", "concurso de méritos",
    "lliure designacio", "lliure designació", "libre designacion", "libre designación",
    "nomenament, per concurs de merits", "nomenament, per concurs de mèrits",
    "nomenament per concurs de merits", "nomenament per concurs de mèrits",
    "nombramiento, por concurso de meritos", "nombramiento, por concurso de méritos",
    "nombramiento por concurso de meritos", "nombramiento por concurso de méritos",
)

_TERMINOS_EMPLEO = (
    "proceso selectivo", "proces selectiu",
    "seleccion de", "seleccio de", "selección de",
    "oposicion", "oposicio", "oposición",
    "concurso oposicion", "concurs oposicio",
    "concurso-oposicion", "concurs-oposicio",
    "bolsa de trabajo", "borsa de treball", "bolsa de empleo",
    "plaza de", "plazas de", "placa de", "places de",
    "personas admitidas", "persones admeses",
    "personas excluidas", "persones excloses",
    "tribunal calificador", "tribunal qualificador",
    "organo tecnico de seleccion", "organ tecnic de seleccio",
    "data d'examen", "fecha de examen", "primer ejercicio", "primer exercici",
    "calificaciones", "qualificacions", "resultados", "resultats",
    "lista de aprobados", "llista d'aprovats", "personas aprobadas", "persones aprovades",
    "constitucio de borsa", "constitucion de bolsa",
    "nombramiento", "nomenament", "toma de posesion", "presa de possessio",
)


def incluido_empleo_bop(titulo: str) -> bool:
    """Acepta anuncios inequívocamente vinculados a empleo público.

    Los nombramientos de procesos selectivos pueden ser hitos finales y no se
    descartan por el mero término 'nombramiento'. Las provisiones internas sí
    se excluyen de forma expresa.
    """
    n = _bop._sin(titulo)
    # Una provisión de puesto o concurso de méritos no pertenece al catálogo
    # de oposiciones aunque su anuncio sea terminal (por ejemplo, un nombramiento).
    # Se excluye antes de conservar los hitos terminales de procesos selectivos.
    if any(_bop._sin(x) in n for x in _TERMINOS_NO_EMPLEO):
        return False
    if _bop._es_evento_terminal_titulo(titulo):
        return True
    return any(_bop._sin(x) in n for x in _TERMINOS_EMPLEO)


def aplicar_reglas_bop() -> None:
    _bop._incluido = incluido_empleo_bop
