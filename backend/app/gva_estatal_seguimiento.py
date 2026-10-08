from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from psycopg.rows import dict_row

from .database import get_connection
from .gva_estatal_source import DETALLE, _get, _limpio, _sin
from .organismos import resolver_organismo


ESTADOS_TERMINALES = {
    "finalizado", "finalitzado", "finalitzat",
    "cancelado", "cancel·lado", "cancel·lat",
    "desistido", "desistit", "anulado", "anul·lat",
}


def _tokens_identidad(texto: str) -> set[str]:
    """Extrae identificadores fuertes de una convocatoria."""
    n = _sin(texto).upper()
    tokens: set[str] = set()
    for codigo in re.findall(r"\b[A-C]\d-\d{2}(?:-\d{2})?\b", n):
        tokens.add(f"CODIGO:{codigo}")
    for numero in re.findall(r"\bORDEN\s+(\d{1,3}/\d{4})\b", n):
        tokens.add(f"ORDEN:{numero}")
    for numero in re.findall(r"\bCONVOCATORIA\s+(\d{1,3}/\d{2,4})\b", n):
        tokens.add(f"CONVOCATORIA:{numero}")
    for numero in re.findall(r"\bBOLSA\s+([0-9]{2,5}-?[A-Z])\b", n):
        tokens.add(f"BOLSA:{numero}")
    return tokens


def _fila_seccion(soup: BeautifulSoup, nombre: str):
    objetivo = _sin(nombre).strip()
    for fila in soup.select("div.detalle-content-row"):
        texto = _sin(_limpio(fila.get_text(" ", strip=True)))
        if texto.startswith(objetivo):
            return fila
    return None


def _signatura_dogv(url: str) -> str | None:
    m = re.search(r"/pdf/(\d{4})_(\d+)_", url, re.I)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    query = parse_qs(urlparse(url).query)
    valor = (query.get("signatura") or [None])[0]
    if valor:
        m = re.fullmatch(r"(\d{4})/(\d+)", valor.strip())
        if m:
            return f"{m.group(1)}_{m.group(2)}"
    return None


def _fecha_seguimiento(texto: str, url: str) -> str | None:
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", texto)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/", url, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _tipo_seguimiento(texto: str) -> str:
    n = _sin(texto)
    if "correccion" in n or "modificacion" in n:
        return "MODIFICACION"
    if "admitid" in n or "excluid" in n:
        return "ADMITIDOS"
    if "tribunal" in n:
        return "TRIBUNAL"
    if "examen" in n or "prueba" in n and "fecha" in n:
        return "EXAMEN"
    if "resultado" in n or "calificacion" in n or "meritos" in n:
        return "RESULTADO"
    if "nombramiento" in n:
        return "NOMBRAMIENTO"
    if "adjudic" in n:
        return "ADJUDICACION"
    if "lista" in n or "bolsa" in n:
        return "LISTA"
    return "SEGUIMIENTO_OFICIAL"


def extraer_seguimientos_validos(html: str) -> dict[str, Any]:
    """Extractor legado usado solo como pista durante la migración a DOGV directo."""
    soup = BeautifulSoup(html, "html.parser")
    disposiciones = _fila_seccion(soup, "Disposiciones")
    seguimiento = _fila_seccion(soup, "Seguimiento")
    texto_primario = _limpio(disposiciones.get_text(" ", strip=True)) if disposiciones else ""
    identidad = _tokens_identidad(texto_primario)

    validos: list[dict[str, Any]] = []
    rechazados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    if seguimiento is None:
        return {"identidad": sorted(identidad), "validos": [], "rechazados": []}

    for enlace in seguimiento.find_all("a", href=True):
        url = str(enlace.get("href") or "").strip()
        if "dogv.gva.es" not in url.lower():
            continue
        signatura = _signatura_dogv(url)
        if not signatura or signatura in vistos:
            continue
        vistos.add(signatura)
        dd = enlace.find_parent("dd")
        texto = _limpio(dd.get_text(" ", strip=True) if dd else enlace.get_text(" ", strip=True))
        tokens = _tokens_identidad(texto)
        comunes = sorted(identidad & tokens)
        item = {
            "signatura": signatura,
            "url": url,
            "fecha_publicacion": _fecha_seguimiento(texto, url),
            "tipo": _tipo_seguimiento(texto),
            "titulo": texto.replace(url, "").strip(),
            "tokens": sorted(tokens),
            "coincidencias_identidad": comunes,
        }
        if identidad and comunes:
            validos.append(item)
        else:
            item["motivo"] = "identidad_no_coincidente" if identidad else "identidad_primaria_insuficiente"
            rechazados.append(item)

    return {"identidad": sorted(identidad), "validos": validos, "rechazados": rechazados}


def _referencia_estatal(datos_json: dict[str, Any] | None) -> int | None:
    datos = datos_json or {}
    fuente = datos.get("fuente_estatal") if isinstance(datos.get("fuente_estatal"), dict) else {}
    valor = fuente.get("referencia_estatal") or datos.get("referencia_estatal")
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _cargar_procesos_activos() -> list[dict[str, Any]]:
    """Compatibilidad para la auditoría diagnóstica existente."""
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo = resolver_organismo(
            cursor,
            tipo="ADMINISTRACION_AUTONOMICA",
            provincia=None,
            nombre="Generalitat Valenciana",
        )
        if organismo is None:
            return []
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, estado, datos_json
            FROM procesos
            WHERE organismo_id=%s
              AND es_oportunidad=TRUE
              AND ambito_administrativo='SI'
            ORDER BY id
            """,
            (organismo["id"],),
        )
        filas = list(cursor.fetchall())
    salida: list[dict[str, Any]] = []
    for fila in filas:
        if str(fila.get("estado") or "").strip().lower() in ESTADOS_TERMINALES:
            continue
        ref = _referencia_estatal(fila.get("datos_json"))
        if ref is None:
            continue
        item = dict(fila)
        item["referencia_estatal"] = ref
        salida.append(item)
    return salida


def _obtener_html(client, referencia: int) -> str:
    respuesta = _get(client, DETALLE, params={"idConvocatoria": referencia, "idioma": "es"})
    return respuesta.text


def actualizar_seguimientos_gva(*, aplicar: bool = False, solo_oportunidades: bool = False) -> dict[str, Any]:
    """Punto de entrada estable; desde v2 delega en DOGV estructurado directo."""
    from .gva_dogv_seguimiento import actualizar_seguimientos_gva_dogv

    return actualizar_seguimientos_gva_dogv(aplicar=aplicar, solo_oportunidades=solo_oportunidades)
