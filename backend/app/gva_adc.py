from __future__ import annotations

"""Inventario de solo lectura de anuncios de difícil cobertura (ADC) GVA.

No escribe en base de datos ni genera notificaciones. Descubre ADC de los
cuerpos administrativos generales A1-01, A2-01, C1-01 y C2-01 y conserva los
datos necesarios para una posterior persistencia y relación documental con
bolsas.
"""

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from . import gva_clean
from .gva_estatal_service import _get_gva_con_reintentos
from .gva_estatal_source import nuevo_cliente


CODIGOS_ADMIN_ESTRICTOS = ("A1-01", "A2-01", "C1-01", "C2-01")

ESPECIALIDADES_EXCLUIDAS = (
    (r"\bAPT[- ]", "especialidad_apt"),
    (r"\bC1-07\b", "especialidad_c1_07"),
    (r"\bC2-01-02\b", "especialidad_c2_01_02"),
    (r"\bC2-01-EDU\b|\bC1-01-EDU\b", "sector_educacion"),
    (r"protocolo", "especialidad_protocolo"),
    (r"orientador(?:a|es)?(?:\s+laboral(?:es)?)?", "especialidad_orientacion_laboral"),
    (r"auxiliar(?:es)? de servicios", "especialidad_auxiliar_servicios"),
    (r"comunicacion y relaciones informativas", "especialidad_comunicacion"),
    (r"fondos europeos", "especialidad_fondos_europeos"),
    (r"agentes tributarios", "especialidad_agentes_tributarios"),
    (r"asistencia al contribuyente", "especialidad_tributaria"),
    (r"discapacidad intelectual", "turno_discapacidad_intelectual"),
)


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn").lower()


def _id_emp(href: str) -> int | None:
    valores = parse_qs(urlparse(href).query).get("id_emp")
    if not valores:
        return None
    try:
        return int(valores[0])
    except (TypeError, ValueError):
        return None


def _es_tarjeta_adc(enlace) -> bool:
    tarjeta = enlace.find_parent(class_=lambda valor: valor and "card" in str(valor).split())
    texto = " ".join((tarjeta or enlace).get_text(" ", strip=True).split())
    return "anuncio dificil cobertura" in _sin_acentos(texto)


def _descubrir_por_codigo(client, codigo: str) -> dict[int, str]:
    respuesta = _get_gva_con_reintentos(
        client,
        gva_clean.GVA_SEARCH_URL,
        params={
            "convocatoria": "1",
            "descripcion": codigo,
            "pagina": "0",
            "tamanyoPagina": "100",
            "elementosPaginacion": "100",
        },
        intentos=1,
    )
    soup = BeautifulSoup(respuesta.text, "html.parser")
    encontrados: dict[int, str] = {}
    for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
        href = str(enlace.get("href") or "")
        identificador = _id_emp(href)
        if identificador is None or not _es_tarjeta_adc(enlace):
            continue
        encontrados[identificador] = urljoin(gva_clean.GVA_BASE_URL, href)
    return encontrados


def _fecha(texto: str, etiqueta: str) -> str | None:
    m = re.search(rf"{re.escape(etiqueta)}\s*:?[ \t]*(\d{{2}}[-/]\d{{2}}[-/]\d{{4}})", texto, re.I)
    return m.group(1).replace("/", "-") if m else None


def _plazas(texto: str) -> int | None:
    m = re.search(r"(?:Numero|Número) de plazas totales\s*:?[ \t]*(\d+)", texto, re.I)
    if not m:
        m = re.search(r"\bPlazas\s*:?[ \t]*(\d+)\b", texto, re.I)
    return int(m.group(1)) if m else None


def _numero_adc(denominacion: str) -> str | None:
    m = re.search(r"\bADC\s+([^\s.]+)", denominacion or "", re.I)
    return m.group(1) if m else None


def _bolsas_explicitas(denominacion: str) -> list[str]:
    """Extrae solo referencias explícitas e inmediatas en la denominación."""
    halladas: set[str] = set()
    normalizado = _sin_acentos(denominacion)
    patron = r"\b(?:bolsa|borsa)\s*(?:n(?:um(?:ero)?)?[.ºo]?\s*)?([0-9]{2,4}(?:/[0-9]{2,4})?(?:[- ]?[bl])?)\b"
    for m in re.finditer(patron, normalizado, re.I):
        halladas.add(m.group(1).upper().replace(" ", ""))
    return sorted(halladas)

def _datos_etapas(soup: BeautifulSoup, texto: str) -> dict[str, Any]:
    norm = _sin_acentos(texto)
    m_etapa = re.search(r"Etapa actual\s*:\s*(.+?)\s+(?:Data publicaci|Fecha publicaci|Anunci|Anuncio|Termini|Plazo)", texto, re.I)
    etapa = " ".join(m_etapa.group(1).split()) if m_etapa else None
    publicaciones = []
    for m in re.finditer(r"(?:Data|Fecha) publicaci[^:]*:\s*(\d{2}-\d{2}-\d{4})", texto, re.I):
        publicaciones.append(m.group(1))
    estado_plazo = "CERRADO" if ("termini tancat" in norm or "plazo cerrado" in norm) else (
        "ABIERTO" if ("termini obert" in norm or "plazo abierto" in norm) else None
    )
    documentos = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if ".pdf" not in href.lower():
            continue
        documentos.append({
            "texto": " ".join(a.get_text(" ", strip=True).split()),
            "url": urljoin(gva_clean.GVA_BASE_URL, href),
        })
    return {
        "etapa_actual": etapa,
        "estado_plazo": estado_plazo,
        "fechas_publicacion": list(dict.fromkeys(publicaciones)),
        "documentos_pdf": documentos,
    }


def _clasificar(id_emp: int, url: str, html: str) -> dict[str, Any]:
    proceso = gva_clean.parsear_detalle(url, html, id_emp)
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    norm = _sin_acentos(texto)
    denominacion = str(proceso.get("denominacion") or "")

    codigos = [c for c in CODIGOS_ADMIN_ESTRICTOS if c.lower() in norm]
    especialidad = next(
        (motivo for patron, motivo in ESPECIALIDADES_EXCLUIDAS if re.search(patron, _sin_acentos(denominacion), re.I)),
        None,
    )
    es_adc = (
        proceso.get("tipo_proceso") == "Anuncio difícil cobertura (ADC)"
        or "anuncio dificil cobertura" in norm
        or denominacion.upper().startswith("ADC ")
    )

    apertura = _fecha(texto, "Apertura plazo")
    cierre = _fecha(texto, "Cierre plazo")
    bolsas = _bolsas_explicitas(denominacion)
    etapas = _datos_etapas(soup, texto)

    return {
        "id_emp": id_emp,
        "identificador_estable": f"GVA:ADC:{id_emp}",
        "numero_adc": _numero_adc(denominacion),
        "denominacion": denominacion,
        "cuerpo_escala": codigos[0] if len(codigos) == 1 else None,
        "grupo": proceso.get("grupo"),
        "plazas": proceso.get("plazas") or _plazas(texto),
        "fecha_apertura": proceso.get("fecha_apertura") or apertura,
        "fecha_cierre": proceso.get("fecha_cierre") or cierre,
        "etapa_actual_gva": etapas["etapa_actual"],
        "estado_plazo": etapas["estado_plazo"],
        "fechas_publicacion": etapas["fechas_publicacion"],
        "documentos_pdf": etapas["documentos_pdf"],
        "url": url,
        "bolsas_relacionadas": bolsas,
        "evidencia_relacion": "TEXTO_FICHA" if bolsas else None,
        "valido_empleo": bool(es_adc and len(codigos) == 1 and especialidad is None),
        "motivo_exclusion": especialidad if especialidad else (
            None if es_adc and len(codigos) == 1 else "fuera_filtro_administrativo"
        ),
    }


def inventariar_adc_gva() -> dict[str, Any]:
    """Descubre y clasifica ADC sin consultar ni modificar la base de datos."""
    descubiertos: dict[int, str] = {}
    diagnostico: list[dict[str, Any]] = []

    with nuevo_cliente() as client:
        for codigo in CODIGOS_ADMIN_ESTRICTOS:
            try:
                encontrados = _descubrir_por_codigo(client, codigo)
                descubiertos.update(encontrados)
                diagnostico.append({"codigo": codigo, "estado": "OK", "adc": len(encontrados)})
            except Exception as exc:
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        validos: list[dict[str, Any]] = []
        excluidos: list[dict[str, Any]] = []
        for id_emp, url in sorted(descubiertos.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url, intentos=1)
                item = _clasificar(id_emp, url, respuesta.text)
            except Exception as exc:
                excluidos.append({
                    "id_emp": id_emp,
                    "url": url,
                    "motivo_exclusion": "error_detalle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            (validos if item["valido_empleo"] else excluidos).append(item)

    return {
        "modo": "INVENTARIO_SIN_BD",
        "escrituras_bd": False,
        "notificaciones": False,
        "descubiertos": len(descubiertos),
        "validos_empleo": len(validos),
        "con_bolsas_relacionadas": sum(bool(x["bolsas_relacionadas"]) for x in validos),
        "diagnostico": diagnostico,
        "adc_validos": validos,
        "excluidos": excluidos,
    }
