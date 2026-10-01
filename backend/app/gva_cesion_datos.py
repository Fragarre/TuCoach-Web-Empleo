from __future__ import annotations

"""Cesiones de datos GVA como novedades de bolsas ya existentes.

No crea oportunidades. Solo publica una novedad en una bolsa administrativa
cuando el anuncio oficial identifica expresamente esa bolsa y el plazo de la
cesión está abierto. Las cesiones históricas cerradas se inventarían, pero
permanecen silenciosas.
"""

import io
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import gva_clean
from .database import get_connection
from .gva_estatal_service import _get_gva_con_reintentos
from .gva_estatal_source import nuevo_cliente
from .organismos import resolver_fuente, resolver_organismo


CODIGOS_BUSQUEDA = ("A1-01", "A2-01", "C1-01", "C2-01")


def _sin(texto: str) -> str:
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


def _es_cesion(enlace) -> bool:
    tarjeta = enlace.find_parent(class_=lambda valor: valor and "card" in str(valor).split())
    texto = _sin(" ".join((tarjeta or enlace).get_text(" ", strip=True).split()))
    return "cesion de datos" in texto or "cessio de dades" in texto


def _descubrir(client, codigo: str) -> dict[int, str]:
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
    salida: dict[int, str] = {}
    for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
        href = str(enlace.get("href") or "")
        identificador = _id_emp(href)
        if identificador is not None and _es_cesion(enlace):
            salida[identificador] = urljoin(gva_clean.GVA_BASE_URL, href)
    return salida


def _fecha_iso(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    texto = str(valor).strip()
    for formato in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _plazo(soup: BeautifulSoup, proceso: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    texto = "\n".join(soup.stripped_strings)
    apertura = _fecha_iso(proceso.get("fecha_apertura"))
    cierre = _fecha_iso(proceso.get("fecha_cierre"))
    if not (apertura and cierre):
        m = re.search(
            r"(?:Plazo|Termini).*?(?:Desde|Des de)\s+(\d{2}[-/]\d{2}[-/]\d{4})"
            r"\s+(?:hasta|a|fins(?:\s+al)?)\s+(\d{2}[-/]\d{2}[-/]\d{4})",
            texto,
            re.I | re.S,
        )
        if m:
            apertura = apertura or _fecha_iso(m.group(1))
            cierre = cierre or _fecha_iso(m.group(2))
    norm = _sin(texto)
    estado = "CERRADO" if ("plazo cerrado" in norm or "termini tancat" in norm) else (
        "ABIERTO" if ("plazo abierto" in norm or "termini obert" in norm) else None
    )
    return apertura, cierre, estado


def _accionable(apertura: str | None, cierre: str | None, estado: str | None) -> bool:
    hoy = date.today()
    a = date.fromisoformat(apertura) if apertura else None
    c = date.fromisoformat(cierre) if cierre else None
    if estado == "CERRADO" or (c is not None and c < hoy):
        return False
    if a is not None and a > hoy:
        return False
    return bool(estado == "ABIERTO" or (a is not None and c is not None and a <= hoy <= c))


def _documentos_pdf(soup: BeautifulSoup) -> list[dict[str, str]]:
    salida = []
    vistos: set[str] = set()
    for enlace in soup.find_all("a", href=True):
        href = str(enlace.get("href") or "")
        if ".pdf" not in href.lower():
            continue
        url = urljoin(gva_clean.GVA_BASE_URL, href)
        if url in vistos:
            continue
        vistos.add(url)
        salida.append({"texto": " ".join(enlace.get_text(" ", strip=True).split()), "url": url})
    return salida


def _bolsas_pdf(client, documentos: list[dict[str, str]]) -> list[str]:
    """Extrae controles solo dentro de la lista oficial de bolsas habilitadas."""
    halladas: set[str] = set()
    patron_ref = re.compile(r"\b\d{2,4}-[BL]\b", re.I)
    inicio = re.compile(
        r"(?:personas|persones)\s+integrantes?\s+de\s+las?\s+(?:siguientes|seguents)\s+"
        r"(?:bolsas|borses)\s*:?",
        re.I,
    )
    fin = re.compile(
        r"\b(?:caracteristicas?\s+del\s+puesto|caracteristiques?\s+del\s+lloc|"
        r"plazo\s+(?:de\s+)?presentacion|termini\s+(?:de\s+)?presentacio|"
        r"el\s+orden\s+vendra|l'ordre\s+vindra)\b",
        re.I,
    )
    for documento in documentos:
        try:
            respuesta = _get_gva_con_reintentos(client, documento["url"], intentos=1)
            lector = PdfReader(io.BytesIO(respuesta.content))
            texto = "\n".join((pagina.extract_text() or "") for pagina in lector.pages)
        except Exception:
            continue
        normalizado = _sin(texto)
        m_inicio = inicio.search(normalizado)
        if not m_inicio:
            continue
        resto = normalizado[m_inicio.end():]
        m_fin = fin.search(resto)
        bloque = resto[:m_fin.start()] if m_fin else resto[:12000]
        halladas.update(x.upper() for x in patron_ref.findall(bloque))
    return sorted(halladas)


def _fecha_publicacion(soup: BeautifulSoup) -> str | None:
    texto = "\n".join(soup.stripped_strings)
    fechas = re.findall(
        r"(?:Fecha|Data)\s+publicaci[^:]*:\s*(\d{2}[-/]\d{2}[-/]\d{4})",
        texto,
        re.I,
    )
    if not fechas:
        fechas = re.findall(r"(?:Web\s+de|Web\s+de)\s+(\d{2}[-/]\d{2}[-/]\d{4})", texto, re.I)
    return _fecha_iso(fechas[0]) if fechas else None


def _clasificar(client, id_emp: int, url: str, html: str) -> dict[str, Any]:
    proceso = gva_clean.parsear_detalle(url, html, id_emp)
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    norm = _sin(texto)
    denominacion = str(proceso.get("denominacion") or "")
    es_cesion = (
        "cesion de datos" in _sin(denominacion)
        or "cessio de dades" in _sin(denominacion)
        or "anuncio de cesion de datos a otras administraciones publicas" in norm
        or "anunci de cessio de dades a altres administracions publiques" in norm
    )
    apertura, cierre, estado = _plazo(soup, proceso)
    documentos = _documentos_pdf(soup)
    bolsas = _bolsas_pdf(client, documentos)
    return {
        "id_emp": id_emp,
        "denominacion": denominacion,
        "url": url,
        "fecha_publicacion": _fecha_publicacion(soup),
        "fecha_apertura": apertura,
        "fecha_cierre": cierre,
        "estado_plazo": estado,
        "accionable": _accionable(apertura, cierre, estado),
        "bolsas_relacionadas": bolsas,
        "documentos_pdf": documentos,
        "valida": bool(es_cesion),
    }


def inventariar_cesiones_gva() -> dict[str, Any]:
    descubiertas: dict[int, str] = {}
    diagnostico = []
    with nuevo_cliente() as client:
        for codigo in CODIGOS_BUSQUEDA:
            try:
                encontradas = _descubrir(client, codigo)
                descubiertas.update(encontradas)
                diagnostico.append({"codigo": codigo, "estado": "OK", "cesiones": len(encontradas)})
            except Exception as exc:
                diagnostico.append({"codigo": codigo, "estado": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
        cesiones = []
        errores = []
        for id_emp, url in sorted(descubiertas.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url, intentos=1)
                item = _clasificar(client, id_emp, url, respuesta.text)
                if item["valida"]:
                    cesiones.append(item)
            except Exception as exc:
                errores.append({"id_emp": id_emp, "url": url, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "modo": "INVENTARIO_SIN_BD",
        "escrituras_bd": False,
        "notificaciones": False,
        "descubiertas": len(descubiertas),
        "validas": len(cesiones),
        "accionables": sum(bool(x["accionable"]) for x in cesiones),
        "con_bolsas_explicitas": sum(bool(x["bolsas_relacionadas"]) for x in cesiones),
        "diagnostico": diagnostico,
        "cesiones": cesiones,
        "errores": errores,
    }


def _resolver_identidad(cursor) -> tuple[int, int]:
    organismo = resolver_organismo(
        cursor, tipo="ADMINISTRACION_AUTONOMICA", provincia=None, nombre="Generalitat Valenciana"
    )
    if organismo is None:
        raise RuntimeError("Cesiones GVA: Generalitat Valenciana no localizada")
    fuente = resolver_fuente(
        cursor,
        nombre="Diari Oficial de la Generalitat Valenciana",
        tipo="DOGV",
        organismo_id=organismo["id"],
    )
    return int(organismo["id"]), int(fuente["id"])


def _resolver_bolsas(cursor, referencias: list[str]) -> dict[str, Any]:
    resueltas: dict[str, int] = {}
    ambiguas: dict[str, list[int]] = {}
    no_resueltas: list[str] = []
    for ref in referencias:
        m = re.fullmatch(r"(\d{2,4})-([BL])", str(ref or "").upper().strip())
        if not m:
            no_resueltas.append(str(ref))
            continue
        numero, sufijo = m.groups()
        patron = rf"(^|[^0-9]){re.escape(numero)}-{sufijo}([^0-9]|$)"
        cursor.execute(
            """
            SELECT id
            FROM procesos
            WHERE es_oportunidad=TRUE
              AND ambito_administrativo='SI'
              AND tipo_proceso='Bolsa de trabajo'
              AND denominacion ~* %s
            ORDER BY id
            """,
            (patron,),
        )
        filas = list(cursor.fetchall())
        if len(filas) == 1:
            resueltas[ref] = int(filas[0]["id"])
        elif len(filas) > 1:
            ambiguas[ref] = [int(x["id"]) for x in filas]
        else:
            no_resueltas.append(ref)
    return {"resueltas": resueltas, "ambiguas": ambiguas, "no_resueltas": sorted(set(no_resueltas))}


def planificar_cesiones_gva() -> dict[str, Any]:
    inventario = inventariar_cesiones_gva()
    relaciones: dict[str, Any] = {}
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        for cesion in inventario["cesiones"]:
            relaciones[str(cesion["id_emp"])] = _resolver_bolsas(cursor, cesion["bolsas_relacionadas"])
    return {**inventario, "modo": "SOLO_REVISION", "relaciones_previstas": relaciones}


def _publicar(cursor, *, bolsa_id: int, fuente_id: int, cesion: dict[str, Any]) -> bool:
    referencia = f"GVA_CESION_DATOS:{cesion['id_emp']}:{cesion.get('fecha_publicacion') or ''}"
    cursor.execute(
        "SELECT id FROM publicaciones WHERE proceso_id=%s AND fuente_id=%s AND referencia=%s LIMIT 1",
        (bolsa_id, fuente_id, referencia),
    )
    if cursor.fetchone() is not None:
        return False
    titulo = cesion["denominacion"] or f"Cesión de datos GVA {cesion['id_emp']}"
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,
            datos_json,detectada_at
        ) VALUES (%s,%s,%s,'CESION_DATOS',%s,%s,%s,%s,NOW())
        """,
        (
            bolsa_id,
            fuente_id,
            referencia,
            titulo,
            cesion.get("fecha_publicacion"),
            cesion["url"],
            Jsonb({
                "categoria_gva": "CESION_DATOS",
                "id_emp": cesion["id_emp"],
                "fecha_apertura": cesion.get("fecha_apertura"),
                "fecha_cierre": cesion.get("fecha_cierre"),
                "estado_plazo": cesion.get("estado_plazo"),
                "bolsas_relacionadas": cesion.get("bolsas_relacionadas") or [],
            }),
        ),
    )
    return True


def persistir_cesiones_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Publica únicamente cesiones accionables en las bolsas explícitamente relacionadas."""
    plan = planificar_cesiones_gva()
    if not aplicar:
        return plan

    publicaciones = 0
    accionables = 0
    ambiguas: dict[str, list[int]] = {}
    no_resueltas: set[str] = set()
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        _, fuente_id = _resolver_identidad(cursor)
        for cesion in plan["cesiones"]:
            relaciones = plan["relaciones_previstas"].get(str(cesion["id_emp"]), {})
            ambiguas.update(relaciones.get("ambiguas") or {})
            no_resueltas.update(relaciones.get("no_resueltas") or [])
            if not cesion["accionable"]:
                continue
            accionables += 1
            for bolsa_id in (relaciones.get("resueltas") or {}).values():
                if _publicar(cursor, bolsa_id=bolsa_id, fuente_id=fuente_id, cesion=cesion):
                    publicaciones += 1
        connection.commit()
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "notificaciones_generales": False,
        "cesiones_validas": plan["validas"],
        "cesiones_accionables": accionables,
        "publicaciones_seguimiento": publicaciones,
        "relaciones_ambiguas": ambiguas,
        "relaciones_no_resueltas": sorted(no_resueltas),
    }
