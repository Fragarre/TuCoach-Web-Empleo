"""Catálogo oficial de municipios de la Comunitat Valenciana.

La identidad estable es el código INE. Los dos nombres publicados por la
Generalitat se usan como alias para que una fuente en castellano o valenciano
se asocie al mismo ayuntamiento.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import httpx


MAPETS_CSV_URL = (
    "https://dadesobertes.gva.es/dataset/93721b0e-3a83-4108-a7b5-040058c5d6c6/"
    "resource/29864181-c7b1-470c-a4e6-e5958ead7c4a/download/mapets.csv"
)
_PROVINCIAS = {"03": "Alicante", "12": "Castellón", "46": "Valencia"}


def _normalizar(texto: str | None) -> str:
    base = " ".join((texto or "").lower().strip().split())
    base = "".join(
        caracter for caracter in unicodedata.normalize("NFD", base)
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", base)


def _claves(nombre: str | None) -> set[str]:
    normalizado = _normalizar(nombre)
    if not normalizado:
        return set()
    claves = {normalizado}
    for articulo in ("la", "el", "les", "los"):
        if normalizado.startswith(articulo) and len(normalizado) > len(articulo):
            claves.add(normalizado[len(articulo):])
        elif normalizado.endswith(articulo) and len(normalizado) > len(articulo):
            claves.add(normalizado[:-len(articulo)])
    return claves


@dataclass(frozen=True)
class MunicipioCV:
    codigo_ine: str
    provincia: str
    nombre: str
    nombre_val: str

    @property
    def etiqueta(self) -> str:
        if _normalizar(self.nombre) == _normalizar(self.nombre_val):
            return f"{self.nombre_val} · {self.provincia}"
        return f"{self.nombre_val} ({self.nombre}) · {self.provincia}"

    @property
    def aliases(self) -> set[str]:
        return _claves(self.nombre) | _claves(self.nombre_val)


@lru_cache(maxsize=1)
def catalogo_municipios_cv() -> tuple[MunicipioCV, ...]:
    """Descarga el CSV oficial y conserva una única copia válida en memoria."""
    respuesta = httpx.get(MAPETS_CSV_URL, timeout=20, follow_redirects=True)
    respuesta.raise_for_status()
    municipios: list[MunicipioCV] = []
    for fila in csv.DictReader(io.StringIO(respuesta.text), delimiter=";"):
        codigo = str(fila.get("codigo_ine") or "").strip().zfill(5)
        provincia = _PROVINCIAS.get(codigo[:2])
        nombre = str(fila.get("nombre") or "").strip()
        nombre_val = str(fila.get("nombre_val") or "").strip()
        if provincia and nombre and nombre_val:
            municipios.append(MunicipioCV(codigo, provincia, nombre, nombre_val))
    if len(municipios) < 500:
        raise RuntimeError("El catálogo oficial de municipios recibido no es válido")
    return tuple(sorted(municipios, key=lambda item: (item.provincia, item.nombre_val)))


def municipio_por_ine(codigo_ine: str | None) -> MunicipioCV | None:
    codigo = str(codigo_ine or "").strip().zfill(5)
    return next((item for item in catalogo_municipios_cv() if item.codigo_ine == codigo), None)


def municipio_equivalente(provincia: str | None, primero: str | None, segundo: str | None) -> bool:
    """Indica si dos grafías pertenecen al mismo municipio oficial."""
    claves_primero = _claves(primero)
    claves_segundo = _claves(segundo)
    if not claves_primero or not claves_segundo:
        return False
    try:
        catalogo = catalogo_municipios_cv()
    except (httpx.HTTPError, RuntimeError):
        # La actualización periódica no se detiene si el portal de datos abiertos
        # está temporalmente indisponible; conserva su comparación previa.
        return False
    for municipio in catalogo:
        if municipio.provincia == provincia and municipio.aliases & claves_primero and municipio.aliases & claves_segundo:
            return True
    return False
