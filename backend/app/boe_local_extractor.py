from __future__ import annotations

from datetime import date, timedelta
from html import unescape
import re
from typing import Any

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo
from .boe_local_diagnostico import (
    BOE_SUMARIO,
    DEPARTAMENTO_LOCAL_CODIGO,
    PROVINCIAS,
    SECCION_OPOSICIONES_CODIGO,
    _iter_items_locales,
    _lista,
    _texto_url,
)

BOE_TEXTO = "https://www.boe.es/diario_boe/txt.php?id={ident}"

NUMEROS = {
    "una": 1, "un": 1, "uno": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20,
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _texto_documento(client: httpx.Client, ident: str) -> str:
    r = client.get(BOE_TEXTO.format(ident=ident))
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")
    texto = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    texto = re.sub(r"<style\b[^>]*>.*?</style>", " ", texto, flags=re.I | re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _numero_plazas(valor: str) -> int | None:
    limpio = valor.strip().lower()
    if limpio.isdigit():
        return int(limpio)
    return NUMEROS.get(limpio)


def _fecha_espanola(dia: str, mes: str, anio: str) -> str | None:
    numero_mes = MESES.get(mes.lower())
    if numero_mes is None:
        return None
    try:
        return date(int(anio), numero_mes, int(dia)).isoformat()
    except ValueError:
        return None


def _extraer_entidad(titulo: str) -> str | None:
    m = re.search(r",\s+(?:del|de la)\s+(.+?),\s+referente\b", titulo, flags=re.I)
    return m.group(1).strip() if m else None


def _extraer_provincia(titulo: str) -> str | None:
    bajo = titulo.lower()
    if "valencia" in bajo or "valència" in bajo:
        return "Valencia"
    if "alicante" in bajo or "alacant" in bajo:
        return "Alicante"
    if "castellón" in bajo or "castellon" in bajo or "castelló" in bajo:
        return "Castellón"
    return None


def _extraer_bases_bop(texto: str) -> dict[str, Any] | None:
    patron = re.compile(
        r"Bolet[ií]n Oficial de la Provincia de\s+([^»<]+?)\s*[»\"]?\s+n[uú]mero\s+(\d+),\s+de\s+(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})",
        flags=re.I,
    )
    m = patron.search(texto)
    if not m:
        return None
    return {
        "provincia": m.group(1).strip(),
        "numero": int(m.group(2)),
        "fecha": _fecha_espanola(m.group(3), m.group(4), m.group(5)),
    }


def _fragmentos_plazas(texto: str) -> list[str]:
    resultado: list[str] = []
    for frag in re.split(r"(?<=[.!?])\s+", texto):
        limpio = frag.strip()
        if re.search(r"\b(?:\d+|una|un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciseis|dieciséis|diecisiete|dieciocho|diecinueve|veinte)\s+plazas?\s+de\b", limpio, flags=re.I):
            resultado.append(limpio)
    return resultado


def _extraer_plaza(fragmento: str) -> dict[str, Any] | None:
    m = re.search(
        r"\b(\d+|una|un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciseis|dieciséis|diecisiete|dieciocho|diecinueve|veinte)\s+plazas?\s+de\s+([^,.;]+)",
        fragmento,
        flags=re.I,
    )
    if not m:
        return None
    plazas = _numero_plazas(m.group(1))
    denominacion = m.group(2).strip()
    ambito = clasificar_ambito_administrativo(
        {"denominacion": fragmento, "cuerpo_escala": None, "grupo": None}
    )
    sistema = None
    sm = re.search(r"por el sistema de\s+([^,.;]+)", fragmento, flags=re.I)
    if sm:
        sistema = sm.group(1).strip()
    turno = None
    tm = re.search(r"(?:en|por) turno\s+([^.;]+)", fragmento, flags=re.I)
    if tm:
        turno = tm.group(1).strip().rstrip(",")
    elif re.search(r"promoci[oó]n interna", fragmento, flags=re.I):
        turno = "promoción interna"
    return {
        "plazas": plazas,
        "denominacion": denominacion,
        "sistema_selectivo": sistema,
        "turno": turno,
        "ambito_administrativo": ambito,
        "texto_fuente": fragmento,
    }


def _tipo_documento_boe(titulo: str, texto: str) -> str:
    """Clasifica eventos BOE que pueden alterar una convocatoria ya conocida."""
    contexto = f"{titulo} {texto}".lower()
    if re.search(r"\b(deja|dejar)\s+sin\s+efecto\b|\banula(?:r|da|do|ción)?\b", contexto, flags=re.I):
        return "ANULACION"
    if re.search(r"\bcorrecci[oó]n\s+de\s+errores\b|\brectificaci[oó]n\b", contexto, flags=re.I):
        return "RECTIFICACION"
    return "CONVOCATORIA"


def _extraer_fecha_resolucion_documento(titulo: str, texto: str) -> str | None:
    """Extrae la fecha de la resolución que origina el anuncio BOE, si consta expresamente."""
    contexto = f"{titulo} {texto}"
    m = re.search(
        r"Resoluci[oó]n\s+de\s+(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+(\d{4})",
        contexto,
        flags=re.I,
    )
    if not m:
        return None
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    return f"{int(m.group(3)):04d}-{meses[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _extraer_resolucion_anterior(titulo: str, texto: str) -> dict[str, str] | None:
    """Extrae la fecha de la resolución anterior citada por una anulación/rectificación."""
    contexto = f"{titulo} {texto}"
    m = re.search(
        r"(?:deja\s+sin\s+efecto|correcci[oó]n\s+de\s+errores\s+de)\s+la\s+de\s+"
        r"(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+(\d{4})",
        contexto,
        flags=re.I,
    )
    if not m:
        return None
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    return {"fecha_resolucion": f"{int(m.group(3)):04d}-{meses[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"}


def _extraer_plazo_literal(texto: str) -> str | None:
    """Extrae fórmulas habituales del BOE sin interpretar todavía el cómputo."""
    patrones = (
        r"((?:El )?plazo de presentaci[oó]n de solicitudes[^.]{0,350}\.)",
        r"((?:El )?plazo para (?:la )?presentaci[oó]n de solicitudes[^.]{0,350}\.)",
        r"((?:El )?plazo para presentar solicitudes[^.]{0,350}\.)",
        r"(Las solicitudes se presentar[aá]n[^.]{0,350}\.)",
    )
    for patron in patrones:
        m = re.search(patron, texto, flags=re.I)
        if m:
            return m.group(1).strip()
    return None


def extraer_convocatorias_boe_local(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Convierte documentos BOE locales CV en convocatorias estructuradas."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    convocatorias: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    documentos_cv = 0
    documentos_con_ambito = 0

    headers = {
        "Accept": "application/json",
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
    }

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        fecha = desde
        while fecha <= hasta:
            try:
                r = client.get(BOE_SUMARIO.format(fecha=fecha.strftime("%Y%m%d")))
                if r.status_code == 404:
                    fecha += timedelta(days=1)
                    continue
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                errores.append({"fecha": fecha.isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                fecha += timedelta(days=1)
                continue

            for _, _, epigrafe, item in _iter_items_locales(data):
                ident = str(item.get("identificador") or "").strip()
                titulo = str(item.get("titulo") or "").strip()
                if not ident or not titulo:
                    continue
                contexto = f"{epigrafe} {titulo}".lower()
                if not any(p in contexto for p in PROVINCIAS):
                    continue
                documentos_cv += 1

                try:
                    texto = _texto_documento(client, ident)
                except Exception as exc:
                    errores.append({"fecha": fecha.isoformat(), "error": f"{ident}: {type(exc).__name__}: {str(exc)[:160]}"})
                    continue

                tipo_documento = _tipo_documento_boe(titulo, texto)
                bases_bop = _extraer_bases_bop(texto)
                entidad = _extraer_entidad(titulo)
                provincia = _extraer_provincia(titulo)
                plazo_literal = _extraer_plazo_literal(texto)
                fecha_resolucion = _extraer_fecha_resolucion_documento(titulo, texto)
                resolucion_anterior = _extraer_resolucion_anterior(titulo, texto)

                plazas = []
                for fragmento in _fragmentos_plazas(texto):
                    plaza = _extraer_plaza(fragmento)
                    if plaza and plaza["ambito_administrativo"] == "SI":
                        plazas.append(plaza)

                if not plazas:
                    if tipo_documento in ("RECTIFICACION", "ANULACION"):
                        convocatorias.append({
                            "codigo_externo": f"{ident}#evento",
                            "boe_id": ident,
                            "fecha_boe": fecha.isoformat(),
                            "entidad": entidad,
                            "provincia": provincia,
                            "denominacion": None,
                            "plazas": None,
                            "sistema_selectivo": None,
                            "turno": None,
                            "ambito_administrativo": None,
                            "bases_bop": bases_bop,
                            "plazo_solicitudes_literal": plazo_literal,
                            "tipo_documento": tipo_documento,\n                            "fecha_resolucion": fecha_resolucion,\n                            "resolucion_anterior": resolucion_anterior,
                            "titulo_boe": titulo,
                            "url_html": _texto_url(item.get("url_html")),
                            "url_xml": _texto_url(item.get("url_xml")),
                            "url_pdf": _texto_url(item.get("url_pdf")),
                            "texto_plaza": texto,
                        })
                    continue

                documentos_con_ambito += 1

                for indice, plaza in enumerate(plazas, start=1):
                    convocatorias.append({
                        "codigo_externo": f"{ident}#{indice}",
                        "boe_id": ident,
                        "fecha_boe": fecha.isoformat(),
                        "entidad": entidad,
                        "provincia": provincia,
                        "denominacion": plaza["denominacion"],
                        "plazas": plaza["plazas"],
                        "sistema_selectivo": plaza["sistema_selectivo"],
                        "turno": plaza["turno"],
                        "ambito_administrativo": "SI",
                        "bases_bop": bases_bop,
                        "plazo_solicitudes_literal": plazo_literal,
                        "url_html": _texto_url(item.get("url_html")),
                        "url_xml": _texto_url(item.get("url_xml")),
                        "url_pdf": _texto_url(item.get("url_pdf")),
                        "texto_plaza": plaza["texto_fuente"],\n                        "tipo_documento": tipo_documento,\n                        "titulo_boe": titulo,
                    })
            fecha += timedelta(days=1)

    return {
        "modo": "SOLO_LECTURA",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "documentos_cv": documentos_cv,
        "documentos_con_ambito": documentos_con_ambito,
        "convocatorias": len(convocatorias),
        "dias_con_error": len(errores),
        "errores": errores,
        "detalle": convocatorias,
    }
