from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection
from .estado_proceso import clasificar_evento_terminal
from .organismos import resolver_fuente, resolver_organismo

BASE_URL = "https://sede.diputacionalicante.es/"
LISTADO_URL = urljoin(BASE_URL, "empleo-otras-oposiciones/")
RSS_URL = urljoin(BASE_URL, "rssoposicotras/")


def _norm(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _fecha(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_rss(xml: str | bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        titulo = _norm(item.findtext("title"))
        url = _norm(item.findtext("link"))
        if not url:
            continue
        referencia = _norm(item.findtext("guid")) or url
        items.append(
            {
                "referencia": referencia,
                "titulo": titulo,
                "url": urljoin(BASE_URL, url),
                "fecha_publicacion": _fecha(item.findtext("pubDate")),
            }
        )
    return items


def _fecha_es(value: str | None) -> date | None:
    value = _norm(value)
    if not value:
        return None
    for formato in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, formato).date()
        except ValueError:
            pass
    return None


def _parse_listado(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    filas: list[dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        celdas = tr.find_all(["th", "td"])
        textos = [_norm(c.get_text(" ", strip=True)) for c in celdas]
        if len(textos) < 2:
            continue
        unidos = " | ".join(textos).lower()
        if "plaza" in unidos and "entidad" in unidos:
            continue
        plaza = textos[0]
        entidad = textos[1] if len(textos) > 1 else None
        if not plaza or not entidad:
            continue
        vacantes = textos[2] if len(textos) > 2 else None
        fecha_bases = _fecha_es(textos[3] if len(textos) > 3 else None)
        fecha_inicio = _fecha_es(textos[4] if len(textos) > 4 else None)
        fecha_fin = _fecha_es(textos[5] if len(textos) > 5 else None)
        observaciones = textos[6] if len(textos) > 6 else None
        enlaces = [urljoin(BASE_URL, a.get("href")) for a in tr.find_all("a", href=True)]
        enlace = enlaces[0] if enlaces else None
        clave = f"{entidad}|{plaza}|{enlace or ''}"
        estado_terminal = clasificar_evento_terminal(None, observaciones or "")
        estado_revision = "TERMINAL" if estado_terminal else "CANDIDATO_ACTIVO"

        filas.append(
            {
                "referencia": f"DALIOTRAS:{hashlib.sha256(clave.encode('utf-8')).hexdigest()[:20]}",
                "denominacion": plaza,
                "entidad": entidad,
                "vacantes": vacantes,
                "fecha_bases": fecha_bases,
                "fecha_inicio_presentacion": fecha_inicio,
                "fecha_fin_presentacion": fecha_fin,
                "observaciones": observaciones,
                "estado_revision": estado_revision,
                "estado_terminal": estado_terminal,
                "url": enlace or LISTADO_URL,
                "enlaces": enlaces,
                "ambito_administrativo": clasificar_ambito_administrativo(
                    {"denominacion": plaza, "cuerpo_escala": None, "grupo": None}
                ),
            }
        )
    return filas


def diagnosticar_otras_entidades_alicante(*, max_items: int = 200) -> dict[str, Any]:
    """SOLO_REVISION: descubre y normaliza sin acceder a la base de datos."""
    headers = {"User-Agent": "TuCoach-Empleo/1.0", "Accept-Language": "es-ES,es;q=0.9"}
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Diputación Alicante / otras entidades locales",
        "rss_url": RSS_URL,
        "listado_url": LISTADO_URL,
        "descubiertos": 0,
        "administrativos": 0,
        "candidatos_activos": 0,
        "terminales": 0,
        "errores": [],
        "detalle": [],
    }
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        try:
            rss = client.get(RSS_URL)
            rss.raise_for_status()
            items = _parse_rss(rss.content)
            resultado["rss_status"] = rss.status_code
            resultado["rss_items"] = len(items)
        except Exception as exc:
            resultado["errores"].append({"origen": "RSS", "error": f"{type(exc).__name__}: {exc}"})
            items = []

        try:
            pagina = client.get(LISTADO_URL)
            pagina.raise_for_status()
            filas = _parse_listado(pagina.text)
            resultado["listado_status"] = pagina.status_code
            resultado["listado_items"] = len(filas)
        except Exception as exc:
            resultado["errores"].append({"origen": "LISTADO", "error": f"{type(exc).__name__}: {exc}"})
            filas = []

    # El listado estructurado es la base normalizada. El RSS queda como canal de descubrimiento
    # y diagnóstico; no se enlazan elementos entre ambos por heurística débil.
    detalle = filas[:max_items]
    resultado["descubiertos"] = len(detalle)
    administrativos = [x for x in detalle if x["ambito_administrativo"] == "SI"]
    resultado["administrativos"] = len(administrativos)
    resultado["candidatos_activos"] = sum(1 for x in administrativos if x["estado_revision"] == "CANDIDATO_ACTIVO")
    resultado["terminales"] = sum(1 for x in administrativos if x["estado_revision"] == "TERMINAL")
    resultado["detalle"] = detalle
    return resultado


def _int_o_none(value: str | None) -> int | None:
    value = _norm(value)
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def preparar_bootstrap_otras_entidades_alicante(*, max_items: int = 200) -> dict[str, Any]:
    """Previsualiza el bootstrap municipal de Alicante sin escribir en BD."""
    revision = diagnosticar_otras_entidades_alicante(max_items=max_items)
    candidatas = [
        x for x in revision["detalle"]
        if x["ambito_administrativo"] == "SI"
        and x["estado_revision"] == "CANDIDATO_ACTIVO"
        and _norm(x.get("entidad")).lower().startswith("ayuntamiento de ")
    ]
    return {
        "modo": "SOLO_REVISION",
        "fuente": revision["fuente"],
        "errores": revision["errores"],
        "candidatas": len(candidatas),
        "plazas": sum(_int_o_none(x.get("vacantes")) or 0 for x in candidatas),
        "detalle": candidatas,
    }


def bootstrap_otras_entidades_alicante(*, max_items: int = 200, aplicar: bool = False) -> dict[str, Any]:
    """Carga inicial idempotente de oportunidades municipales activas de Alicante."""
    revision = preparar_bootstrap_otras_entidades_alicante(max_items=max_items)
    if not aplicar or revision["errores"]:
        return revision

    resultado: dict[str, Any] = {
        "modo": "APLICAR",
        "fuente": revision["fuente"],
        "nuevas": 0,
        "existentes": 0,
        "organismos_creados": 0,
        "publicaciones": 0,
        "plazas": 0,
        "errores": [],
        "detalle": [],
    }
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        fuente = resolver_fuente(
            cursor,
            nombre="Diputación Alicante - Otras oposiciones",
            tipo="SEDE",
        )
        fuente_id = fuente["id"]

        for item in revision["detalle"]:
            entidad = _norm(item["entidad"])
            municipio = entidad[len("Ayuntamiento de "):].strip()
            organismo = resolver_organismo(
                cursor,
                tipo="AYUNTAMIENTO",
                provincia="Alicante",
                municipio=municipio,
            )
            if organismo is None:
                cursor.execute(
                    """
                    INSERT INTO organismos
                        (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                    VALUES (%s,'AYUNTAMIENTO',%s,'Alicante',TRUE,NOW(),NOW())
                    RETURNING id
                    """,
                    (entidad, municipio),
                )
                organismo_id = cursor.fetchone()["id"]
                resultado["organismos_creados"] += 1
            else:
                organismo_id = organismo["id"]

            cursor.execute(
                "SELECT id FROM procesos WHERE identificador_estable=%s",
                (item["referencia"],),
            )
            existente = cursor.fetchone()
            plazas = _int_o_none(item.get("vacantes"))
            if existente:
                proceso_id = existente["id"]
                resultado["existentes"] += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO procesos
                        (organismo_id,identificador_estable,denominacion,estado,
                         plazas,fecha_convocatoria,fecha_apertura,fecha_cierre,
                         fuente_principal_id,es_oportunidad,ambito_administrativo,
                         datos_json,updated_at)
                    VALUES (%s,%s,%s,'EN_CURSO',%s,%s,%s,%s,%s,TRUE,'SI',%s,NOW())
                    RETURNING id
                    """,
                    (
                        organismo_id,
                        item["referencia"],
                        item["denominacion"],
                        plazas,
                        item.get("fecha_bases"),
                        item.get("fecha_inicio_presentacion"),
                        item.get("fecha_fin_presentacion"),
                        fuente_id,
                        Jsonb({
                            "origen": "DIPUTACION_ALICANTE_OTRAS",
                            "url_oficial": item["url"],
                            "observaciones": item.get("observaciones"),
                        }),
                    ),
                )
                proceso_id = cursor.fetchone()["id"]
                resultado["nuevas"] += 1
                resultado["plazas"] += plazas or 0

            cursor.execute(
                """
                INSERT INTO publicaciones
                    (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json)
                VALUES (%s,%s,%s,'BASES',%s,%s,%s,%s)
                ON CONFLICT (fuente_id,referencia,url) DO NOTHING
                RETURNING id
                """,
                (
                    proceso_id,
                    fuente_id,
                    item["referencia"],
                    item["denominacion"],
                    item.get("fecha_bases"),
                    item["url"],
                    Jsonb({"entidad": entidad, "origen": "DIPUTACION_ALICANTE_OTRAS"}),
                ),
            )
            if cursor.fetchone():
                resultado["publicaciones"] += 1
            resultado["detalle"].append({
                "proceso_id": proceso_id,
                "referencia": item["referencia"],
                "entidad": entidad,
                "denominacion": item["denominacion"],
                "plazas": plazas,
            })
        connection.commit()
    return resultado
