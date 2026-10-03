from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from psycopg.types.json import Jsonb

from .database import get_connection
from .organismos import resolver_fuente, resolver_organismo
from .estado_proceso import clasificar_evento_terminal

BOP_URL = "https://bop.dival.es/bop/"
BOP_PORTAL_URL = "https://bop.dival.es/bop/xhtml/portal.xhtml"
DOWNLOAD_URL = "https://bop.dival.es/bop/downloads"

INCLUIDOS = (
    "convocatoria", "proceso selectivo", "selección", "seleccion",
    "oposición", "oposicion", "bolsa de trabajo", "bolsa de empleo",
)
EXCLUIDOS = (
    "provisión del puesto", "provision del puesto", "provisión de puestos",
    "provision de puestos", "provisión del lugar", "provision del lloc",
    "provisió del lloc", "libre designación", "libre designacion",
    "lliure designació", "lliure designacio", "nomenament", "nombramiento",
)

NUMEROS = {
    "una": 1, "uno": 1, "un": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "quatre": 4, "cinco": 5, "cinc": 5, "seis": 6,
    "sis": 6, "siete": 7, "set": 7, "ocho": 8, "vuit": 8,
    "nueve": 9, "nou": 9, "diez": 10, "deu": 10, "once": 11,
    "onze": 11, "doce": 12, "dotze": 12, "trece": 13, "tretze": 13,
    "catorce": 14, "catorze": 14, "quince": 15, "quinze": 15,
    "dieciseis": 16, "setze": 16, "dieciséis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "vint": 20,
}


def _norm(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())


def _sin(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _fecha(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _convocatoria(s: str) -> str | None:
    n = _sin(s)
    m = re.search(r"convocatoria\s+([a-z]?\s*\d{1,3}/\d{2,4}[a-z]?)\b", n, re.I)
    return re.sub(r"\s+", "", m.group(1)).upper() if m else None


def _anio_convocatoria(s: str) -> int | None:
    c = _convocatoria(s)
    if not c:
        return None
    m = re.search(r"/(\d{2,4})(?:[A-Z])?$", c, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return 2000 + n if n < 100 else n


def _plazas(s: str) -> int | None:
    n = _sin(s)
    patrones = (
        r"(?:seleccion|seleccio)\s+de\s+(\d+)\s+(?:plazas?|places?)",
        r"(?:seleccion|seleccio)\s+de\s+(una|uno|un|dos|tres|cuatro|quatre|cinco|cinc|seis|sis|siete|set|ocho|vuit|nueve|nou|diez|deu|once|onze|doce|dotze|trece|tretze|catorce|catorze|quince|quinze|dieciseis|dieciséis|diecisiete|dieciocho|diecinueve|veinte|vint)\s+(?:plazas?|places?)",
        r"convocatoria\s+de\s+(\d+)\s+(?:plazas?|places?)",
        r"convocatoria\s+de\s+(una|uno|un|dos|tres|cuatro|quatre|cinco|cinc|seis|sis|siete|set|ocho|vuit|nueve|nou|diez|deu)\s+(?:plazas?|places?)",
    )
    for p in patrones:
        m = re.search(p, n, re.I)
        if m:
            valor = m.group(1).lower()
            return int(valor) if valor.isdigit() else NUMEROS.get(valor)
    return None


def _grupo_subgrupo(s: str) -> tuple[str | None, str | None]:
    n = _sin(s)
    m = re.search(r"(?:grupo\s*/\s*subgrupo|subgrupo|grupo)\s*[:.-]?\s*([a-z]\d(?:/\d)?)", n, re.I)
    if not m:
        return None, None
    subgrupo = m.group(1).upper()
    return subgrupo.split("/")[0][0], subgrupo


def _fecha_convocatoria(s: str) -> date | None:
    n = _sin(s)
    patrones = (
        r"convocatoria.{0,120}?(?:de fecha|fecha de|dat(?:a|a) de)\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
        r"(?:fecha de convocatoria|data de la convocatoria)\s*:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
    )
    for p in patrones:
        m = re.search(p, n, re.I | re.S)
        if m:
            return _fecha(m.group(1))
    return None


def _turno(s: str) -> str | None:
    n = _sin(s)
    if "estabilizacion" in n:
        return "ESTABILIZACION"
    if "promocion interna" in n:
        return "PROMOCION_INTERNA"
    if "turno libre" in n or "oposicion lliure" in n or "oposicion libre" in n:
        return "TURNO_LIBRE"
    return None


def _tipo(s: str) -> str:
    n = _sin(s)
    if any(x in n for x in ("bolsa de trabajo", "bolsa de empleo", "borsa de treball")):
        return "Bolsa de trabajo"
    if any(x in n for x in ("concurso-oposicion", "concurso oposicion", "concurs-oposicio", "concurs oposicio")):
        return "Concurso-oposición"
    if "oposicion" in n or "oposicio" in n:
        return "Oposición"
    return "Proceso selectivo"


def _incluido(titulo: str) -> bool:
    n = _sin(titulo)
    if any(_sin(x) in n for x in EXCLUIDOS):
        return False
    return any(_sin(x) in n for x in INCLUIDOS)


def _es_convocatoria_base(titulo: str, texto: str) -> bool:
    n = _sin(titulo + " " + texto)
    if any(x in n for x in (
        "designacion de miembros", "designacion del organo", "designacion del tribunal",
        "composicion del organo", "relacion provisional", "relacion definitiva",
        "lista provisional", "lista definitiva", "fecha de examen", "calificaciones",
        "resultado", "nombramiento",
    )):
        return False
    return any(x in n for x in (
        "aprobacion de las bases", "aprobacion de bases", "bases que han de regir",
        "bases especificas", "convocatoria para la seleccion", "convocatoria del concurso",
        "convocatoria de la oposicion", "convocatoria del proceso selectivo",
    ))


def _tipo_publicacion(titulo: str, texto: str) -> str:
    return "CONVOCATORIA" if _es_convocatoria_base(titulo, texto) else "BOP"


def _extraer_metadatos(a: Any) -> tuple[str | None, date | None]:
    cont = a
    for _ in range(20):
        cont = cont.parent
        if cont is None:
            break
        texto = _norm(cont.get_text(" ", strip=True))
        if re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)", texto, re.I):
            m = re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d{5})", texto, re.I)
            fm = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", texto, re.I)
            return (m.group(1) if m else None, _fecha(fm.group(1)) if fm else _fecha(texto))
    return None, None


def _diagnostico_candidato(a: Any) -> dict[str, Any]:
    chain = []
    cont = a
    for level in range(1, 13):
        cont = cont.parent
        if cont is None:
            break
        texto = _norm(cont.get_text(" ", strip=True))
        chain.append({"nivel": level, "tag": getattr(cont, "name", None), "id": cont.get("id") if hasattr(cont, "get") else None, "class": cont.get("class") if hasattr(cont, "get") else None, "texto": texto[:1000]})
        if re.search(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)", texto, re.I):
            break
    return {"titulo": _norm(a.get_text(" ", strip=True)), "id": a.get("id"), "class": a.get("class"), "onclick": a.get("onclick"), "chain": chain}


def diagnosticar_bop(client: httpx.Client) -> dict[str, Any]:
    r = client.get(BOP_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    anchors = soup.find_all("a")
    commandlinks = [a for a in anchors if "ui-commandlink" in " ".join(a.get("class", []))]
    candidatos = []
    for a in anchors:
        titulo = _norm(a.get_text(" ", strip=True))
        if not titulo or "anunci" not in _sin(titulo):
            continue
        candidatos.append(_diagnostico_candidato(a))
        if len(candidatos) >= 20:
            break
    return {"url": str(r.url), "status": r.status_code, "ancho_html": len(r.text), "total_anchors": len(anchors), "commandlinks": len(commandlinks), "anuncios_detectados": len(candidatos), "candidatos": candidatos}


def _pagina_bop_url(fecha: date) -> str:
    return f"{BOP_PORTAL_URL}?fecha={quote(fecha.strftime('%d/%m/%Y'))}"


def _extraer_anuncios_pagina(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for a in soup.find_all("a"):
        titulo = _norm(a.get_text(" ", strip=True))
        if not titulo or not _incluido(titulo):
            continue
        n = _sin(titulo)
        if "diputacion provincial de valencia" not in n and "diputacio provincial de valencia" not in n:
            continue
        if "ui-commandlink" not in " ".join(a.get("class", [])):
            continue
        registro, fecha = _extraer_metadatos(a)
        if not registro or registro in vistos:
            continue
        vistos.add(registro)
        resultados.append({"titulo": titulo, "url": f"{DOWNLOAD_URL}?anuncioNumReg={quote(registro)}&lang=es", "registro": registro, "fecha_publicacion": fecha})
    return resultados


def _obtener_pagina(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    try:
        r = client.get(_pagina_bop_url(fecha))
        r.raise_for_status()
        return fecha, r.text, None
    except Exception as exc:
        return fecha, None, str(exc)


def descubrir_anuncios(client: httpx.Client, historico: bool = False, dias: int = 1) -> list[dict[str, Any]]:
    if not historico:
        r = client.get(BOP_URL)
        r.raise_for_status()
        return _extraer_anuncios_pagina(r.text)
    hoy = date.today()
    desde = hoy - timedelta(days=max(0, dias - 1))
    fechas = [desde + timedelta(days=i) for i in range((hoy - desde).days + 1)]
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_obtener_pagina, client, fecha) for fecha in fechas]
        for future in as_completed(futures):
            _, html, _ = future.result()
            if not html:
                continue
            for anuncio in _extraer_anuncios_pagina(html):
                if anuncio["registro"] not in vistos:
                    vistos.add(anuncio["registro"])
                    resultados.append(anuncio)
    resultados.sort(key=lambda x: (x["fecha_publicacion"] or date.min, x["registro"]))
    return resultados


def _obtener_texto(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", "").lower() or r.content.startswith(b"%PDF"):
        reader = PdfReader(BytesIO(r.content))
        return _norm(" ".join(page.extract_text() or "" for page in reader.pages))
    return _norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))


def _identificador_estable(titulo: str, texto: str) -> str:
    convocatoria = _convocatoria(titulo + " " + texto)
    if convocatoria:
        return f"DVAL:{convocatoria}"
    return "DVAL:T:" + hashlib.sha256(_sin(titulo).encode("utf-8")).hexdigest()[:24]


def _resolver_identidad_bop_valencia(cursor) -> tuple[int, int]:
    organismo = resolver_organismo(
        cursor,
        tipo="DIPUTACION",
        provincia="Valencia",
        nombre="Diputación Provincial de Valencia",
    )
    if organismo is None:
        raise RuntimeError("No existe el organismo Diputación Provincial de Valencia")
    fuente = resolver_fuente(
        cursor,
        nombre="Boletín Oficial de la Provincia de Valencia",
        tipo="BOP",
        organismo_id=organismo["id"],
    )
    return organismo["id"], fuente["id"]


def _cambios_base_existente(existente: tuple[Any, ...], nuevos: tuple[Any, ...]) -> list[tuple[str, Any, Any]]:
    """Compara metadatos de bases sin permitir que su relectura altere el estado."""
    campos = (
        ("denominacion", 1, 0),
        ("grupo", 2, 1),
        ("subgrupo", 3, 2),
        ("tipo_proceso", 4, 3),
        ("turno", 5, 4),
        ("plazas", 6, 5),
        ("anio_convocatoria", 8, 6),
        ("fecha_convocatoria", 9, 7),
    )
    cambios: list[tuple[str, Any, Any]] = []
    for campo, indice_existente, indice_nuevo in campos:
        valor_nuevo = nuevos[indice_nuevo]
        valor_anterior = existente[indice_existente]
        if valor_anterior != valor_nuevo and valor_nuevo is not None:
            cambios.append((campo, valor_anterior, valor_nuevo))
    return cambios


def importar_bop_valencia(historico: bool = False, dias: int = 1) -> dict[str, Any]:
    stats: dict[str, Any] = {"descubiertos": 0, "procesos": 0, "publicaciones": 0, "cambios": 0, "anuncios": []}
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        anuncios = descubrir_anuncios(client, historico=historico, dias=dias)
        stats["descubiertos"] = len(anuncios)
        with get_connection() as connection:
            with connection.cursor() as cursor:
                organismo_id, fuente_id = _resolver_identidad_bop_valencia(cursor)
                for anuncio in anuncios:
                    fecha = anuncio["fecha_publicacion"]
                    titulo = anuncio["titulo"]
                    registro = anuncio["registro"]
                    texto = _obtener_texto(client, anuncio["url"])
                    contenido = titulo + " " + texto
                    estable = _identificador_estable(titulo, texto)
                    tipo_publicacion = _tipo_publicacion(titulo, texto)
                    es_base = tipo_publicacion == "CONVOCATORIA"
                    anio = _anio_convocatoria(contenido)
                    fecha_convocatoria = _fecha_convocatoria(contenido)
                    grupo, subgrupo = _grupo_subgrupo(contenido)
                    plazas = _plazas(contenido)
                    ultima = datetime.combine(fecha, datetime.min.time(), tzinfo=timezone.utc) if fecha else None
                    cursor.execute("SELECT id, denominacion, grupo, subgrupo, tipo_proceso, turno, plazas, estado, anio_convocatoria, fecha_convocatoria, datos_json FROM procesos WHERE identificador_estable=%s", (estable,))
                    existente = cursor.fetchone()
                    if existente:
                        proceso_id = existente[0]
                        if es_base:
                            # Releer las bases puede refrescar metadatos, pero no debe
                            # reabrir un proceso que una publicación posterior cerró.
                            nuevos = (titulo, grupo, subgrupo, _tipo(contenido), _turno(contenido), plazas, anio, fecha_convocatoria)
                            for campo, valor_anterior, valor_nuevo in _cambios_base_existente(existente, nuevos):
                                cursor.execute("INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo) VALUES (%s,%s,%s,%s,%s,%s,TRUE)", (proceso_id, "ACTUALIZACION", campo, str(valor_anterior) if valor_anterior is not None else None, str(valor_nuevo), f"Actualización de la convocatoria: {campo}"))
                                stats["cambios"] += 1
                            cursor.execute("UPDATE procesos SET denominacion=%s,grupo=COALESCE(%s,grupo),subgrupo=COALESCE(%s,subgrupo),tipo_proceso=%s,turno=COALESCE(%s,turno),plazas=COALESCE(%s,plazas),anio_convocatoria=COALESCE(%s,anio_convocatoria),fecha_convocatoria=COALESCE(%s,fecha_convocatoria),ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,es_oportunidad=TRUE,datos_json=%s,updated_at=NOW() WHERE id=%s", (nuevos[0], nuevos[1], nuevos[2], nuevos[3], nuevos[4], nuevos[5], nuevos[6], nuevos[7], ultima, fuente_id, Jsonb({**(existente[10] or {}), "url_convocatoria": anuncio["url"], "registro_convocatoria": registro}), proceso_id))
                        else:
                            estado_terminal = clasificar_evento_terminal(
                                existente[4],
                                titulo,
                            )
                            if estado_terminal:
                                cursor.execute(
                                    "UPDATE procesos SET estado=%s,ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,es_oportunidad=TRUE,updated_at=NOW() WHERE id=%s",
                                    (estado_terminal, ultima, fuente_id, proceso_id),
                                )
                            else:
                                cursor.execute("UPDATE procesos SET ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),fuente_principal_id=%s,es_oportunidad=TRUE,updated_at=NOW() WHERE id=%s", (ultima, fuente_id, proceso_id))
                    else:
                        cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,grupo,subgrupo,tipo_proceso,turno,plazas,estado,anio_convocatoria,fecha_convocatoria,ultima_publicacion_at,fuente_principal_id,es_oportunidad,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s) RETURNING id", (organismo_id, registro, estable, titulo, grupo, subgrupo, _tipo(contenido), _turno(contenido), plazas, "EN_CURSO", anio, fecha_convocatoria, ultima, fuente_id, Jsonb({"registro": registro, "url_ultima_publicacion": anuncio["url"], "convocatoria_identificada": _convocatoria(contenido)})))
                        proceso_id = cursor.fetchone()[0]
                        stats["procesos"] += 1
                    contenido_hash = hashlib.sha256(texto.encode("utf-8")).hexdigest()
                    cursor.execute("SELECT id FROM publicaciones WHERE proceso_id=%s AND referencia=%s LIMIT 1", (proceso_id, registro))
                    publicacion = cursor.fetchone()
                    if publicacion is None:
                        cursor.execute("INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,contenido_hash,contenido_texto,datos_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (proceso_id, fuente_id, registro, tipo_publicacion, titulo, fecha, anuncio["url"], contenido_hash, texto, Jsonb({"registro": registro, "url": anuncio["url"], "es_convocatoria_base": es_base})))
                        publicacion_id = cursor.fetchone()[0]
                        stats["publicaciones"] += 1
                        if not es_base:
                            cursor.execute("INSERT INTO cambios (proceso_id,publicacion_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo) VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)", (proceso_id, publicacion_id, "PUBLICACION", "publicacion", None, registro, f"Nueva publicación oficial: {titulo}"))
                            stats["cambios"] += 1
                    stats["anuncios"].append({"registro": registro, "titulo": titulo, "fecha_publicacion": fecha.isoformat() if fecha else None, "proceso_id": proceso_id, "identificador_estable": estable, "tipo_publicacion": tipo_publicacion})
            connection.commit()
    return stats
