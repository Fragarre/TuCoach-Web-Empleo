"""Extracción conservadora de la clasificación declarada en fuentes oficiales.

No deduce el grupo por la denominación del puesto: solo acepta valores próximos
a una etiqueta explícita de grupo, subgrupo o escala/cuerpo.
"""
from __future__ import annotations

import re
import unicodedata
from typing import TypedDict


class ClasificacionPuesto(TypedDict):
    grupo: str | None
    subgrupo: str | None
    cuerpo_escala: str | None


def _sin_acentos(texto: str) -> str:
    return "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(caracter) != "Mn"
    )


def _limpiar(valor: str | None) -> str | None:
    if not valor:
        return None
    valor = " ".join(valor.replace("\xa0", " ").split()).strip(" .;:-")
    return valor or None


def extraer_clasificacion_declarada(texto: str) -> ClasificacionPuesto:
    """Devuelve exclusivamente clasificación etiquetada en español o valenciano."""
    normal = _sin_acentos(texto)
    grupo = subgrupo = cuerpo_escala = None

    combinado = re.search(
        r"\b(?:grupo|grup)\s*/\s*(?:subgrupo|subgrup)\s*[:.\-]?\s*([A-E])\s*/\s*([A-E][1-3])\b",
        normal,
        re.IGNORECASE,
    )
    if combinado:
        grupo, subgrupo = combinado.group(1).upper(), combinado.group(2).upper()
    else:
        encontrado_subgrupo = re.search(
            r"\b(?:subgrupo|subgrup)\s*[:.\-]?\s*([A-E][1-3])\b",
            normal,
            re.IGNORECASE,
        )
        encontrado_grupo = re.search(
            r"\b(?:grupo|grup)\s*[:.\-]?\s*([A-E](?:[1-3])?)\b",
            normal,
            re.IGNORECASE,
        )
        if encontrado_subgrupo:
            subgrupo = encontrado_subgrupo.group(1).upper()
            grupo = subgrupo[0]
        if encontrado_grupo:
            grupo_declarado = encontrado_grupo.group(1).upper()
            grupo = grupo or grupo_declarado[0]
            if len(grupo_declarado) == 2:
                subgrupo = subgrupo or grupo_declarado

    # Se conserva el literal oficial. El límite evita capturar el resto de la
    # publicación cuando el PDF no mantiene saltos de línea.
    escala = re.search(
        r"\b(?:cuerpo\s*(?:/|y)?\s*escala|cos\s*(?:/|i)?\s*escala|escala)\s*[:.\-]\s*"
        r"(Administraci(?:[oó]n|ó)\s+(?:General|Especial))\b",
        texto,
        re.IGNORECASE,
    )
    if escala:
        valor = _limpiar(escala.group(1))
        if valor:
            # Homogeneiza solo la grafía, no el significado declarado.
            sin = _sin_acentos(valor).lower()
            if "general" in sin:
                cuerpo_escala = "Administración General"
            elif "especial" in sin:
                cuerpo_escala = "Administración Especial"
            else:
                cuerpo_escala = valor

    return {"grupo": grupo, "subgrupo": subgrupo, "cuerpo_escala": cuerpo_escala}
