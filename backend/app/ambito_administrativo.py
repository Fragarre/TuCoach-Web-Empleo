from __future__ import annotations

import re
import unicodedata
from typing import Any

AMBITOS = ("SI", "NO", "REVISION")


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip().lower()


def clasificar_ambito_administrativo(proceso: dict[str, Any], *, aplicar_codigos_gva: bool = False) -> str:
    """Clasificación automática conservadora para el catálogo de Tu Coach.

    Solo devuelve SI/NO cuando la denominación es suficientemente explícita.
    Los casos fronterizos permanecen en REVISION para decisión humana.
    Las exclusiones específicas prevalecen sobre menciones genéricas a la
    escala de administración general.

    ``contexto`` (opcional) es el texto oficial que rodea la plaza (párrafo del
    BOE, extracto del BOP...). Se usa igual que la escala, pero las exclusiones
    "solo de puesto" se evalúan únicamente sobre la denominación, para que una
    mención incidental en el párrafo no retire una plaza de TAG legítima.
    """
    texto = _normalizar(" ".join(str(proceso.get(k) or "") for k in ("denominacion", "cuerpo_escala", "grupo", "contexto")))
    puesto = _normalizar(str(proceso.get("denominacion") or ""))

    # En GVA los códigos de cuerpo son determinantes: solo se admiten los
    # cuatro cuerpos administrativos generales definidos para Tu Coach. Una
    # denominación genérica ("administrativo", "tributario", etc.) no puede
    # convertir en SI un código GVA explícitamente distinto.
    codigos_gva = re.findall(r"\b[ac][12]-\d{2}(?:-[a-z0-9]+)*\b", texto)
    if aplicar_codigos_gva and codigos_gva:
        return "SI" if any(c in {"a1-01", "a2-01", "c1-01", "c2-01"} for c in codigos_gva) else "NO"

    # Exclusiones claras. Se evalúan primero para evitar falsos positivos como
    # "conserje-notificador, escala de administración general, subescala subalterna".
    patrones_no = (
        r"investigacion cientifica",
        r"medicina(?: del trabajo)?",
        r"metge(?:/ssa)? del treball",
        r"enfermeria",
        r"psicologia",
        r"educacion especial",
        r"educacio especial",
        r"educacion infantil",
        r"educacio infantil",
        r"professor(?:/a)?",
        r"profesor(?:/a)?",
        r"veterinari",
        r"ingenier",
        r"enginyer",
        r"arquitect",
        r"laboratorio",
        r"laboratori",
        r"biblioteca",
        r"bibliotecari",
        r"servicios auxiliares de la investigacion",
        r"\bsubaltern(?:o|a|os|es)?\b",
        r"\bconser(?:je|ge)(?:s)?\b",
        r"\bnotificador(?:/a|a|es)?\b",
        r"ayudante de residencia",
        r"agricol",
        r"forestal",
        r"orientacion escolar",
        r"orientacio escolar",
        r"taller ocupacional",
        r"obras publicas",
        r"obres publiques",
        r"mecanic",
        r"conductor",
        r"restaurador",
        r"traductor",
        r"linguistic",
        r"delineant",
        r"comunicacio audiovisual",
        r"comercio",
        r"comerc",
        r"desarrollo local",
        r"desenvolupament local",
        r"gestion tributaria",
        r"gestio tributaria",
        r"recaudacion",
        r"recaptacio",
        r"agentes? tributarios?",
        r"agents? tributaris?",
        r"tecnico(?:/a)? tributario",
        r"tecnic(?:/a)? tributari",
        # Escala de Administración Especial: por definición no es un puesto
        # administrativo general (p. ej. "relaciones laborales, escala de
        # Administración Especial, subescala técnica").
        r"administracion especial",
        r"administracio especial",
        r"relaciones laborales",
        r"relacions laborals",
        r"\bfiestas?\b",
        r"\bfestejos\b",
        r"\bfestes\b",
    )
    if any(re.search(p, texto) for p in patrones_no):
        return "NO"

    # Perfiles específicos que solo se excluyen si forman parte del nombre del
    # puesto: una plaza de TAG "adscrita a Tesorería" sigue siendo TAG.
    patrones_no_puesto = (
        r"tesoreri",
        r"tresoreri",
    )
    if any(re.search(p, puesto) for p in patrones_no_puesto):
        return "NO"

    # Cuerpos y perfiles administrativos en sentido amplio, en castellano y valenciano.
    patrones_si = (
        r"\bc1-01\b",
        r"\bc2-01\b",
        r"\ba1-01\b",
        r"\ba2-01\b",
        r"\bcuerpo administrativo\b",
        r"\bcuerpo auxiliar\b",
        r"\bcuerpo superior de administracion\b",
        r"\bcuerpo superior de gestion\b",
        r"\badministracion general\b",
        r"\badministracio general\b",
        r"\btecnico(?:/a)? de administracion general\b",
        r"\btecnic(?:/a)? d['’]?administracio general\b",
        r"\bauxiliar(?:es)? administrativo(?:s|/a|/va)?\b",
        r"\bauxiliar(?:s)? administratiu(?:s|/va)?\b",
        r"\badministrativo(?:s|/a)?\b",
        r"\badministratiu(?:s|/va|/ves)?\b",
    )
    if any(re.search(p, texto) for p in patrones_si):
        return "SI"

    return "REVISION"
