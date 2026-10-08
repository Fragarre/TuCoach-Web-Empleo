from __future__ import annotations

import math
import re
import time
import unicodedata
from datetime import date, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from .gva_http import nuevo_cliente_gva

BASE = "https://administracion.gob.es"
RESULTADOS = f"{BASE}/empleopublico/resultadosEmpleo"
DETALLE = f"{BASE}/empleopublico/resultadosEmpleo/detalle-empleo"
UA = "NetReto-Empleo/1.0 (https://netexamenes.com)"
TAM_PAGINA = 10

VIAS_INCLUIDAS = {"INGRESO_LIBRE", "INTERINIDAD", "CONTRATACION_FIJA"}
CODIGOS_ADMIN = ("A1-01", "A2-01", "C1-01", "C2-01")
PATRONES_ADMIN = (
    r"\bsuperior de administracion\b",
    r"\bcuerpo administrativo\b",
    r"\bcuerpo auxiliar\b",
    r"\badministrativ[oa]\b",
    r"\bauxiliar administrativ[oa]\b",
)
PATRONES_ORGANISMO_GVA = (
    "generalitat valenciana",
    "presidencia de la generalitat",
    "vicepresidencia",
    "conselleria",
    "labora",
    "agencia valenciana",
    "agencia tributaria valenciana",
    "institut valencia",
    "instituto valenciano",
)
PATRONES_ORGANISMO_NO_GVA = (
    "universitat",
    "universidad",
    "ayuntamiento",
    "ajuntament",
    "diputacion",
    "diputacio",
)


def _sin(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").lower()


def _limpio(texto: str) -> str:
    return " ".join((texto or "").replace("\xa0", " ").split())


def _get(client: httpx.Client, url: str, *, params: dict | None = None) -> httpx.Response:
    ultimo: Exception | None = None
    for intento in range(1, 5):
        try:
            respuesta = client.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            if intento < 4:
                time.sleep(2 * intento)
    assert ultimo is not None
    raise ultimo


def _parse_total(soup: BeautifulSoup) -> int | None:
    texto = _limpio(soup.get_text(" ", strip=True))
    m = re.search(r"Total(?: de)? resultados:?\s*([\d.]+)", texto, re.I)
    return int(m.group(1).replace(".", "")) if m else None


def _parse_tarjetas(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    salida: list[dict] = []
    vistos: set[int] = set()
    for enlace in soup.find_all("a", href=True):
        href = str(enlace.get("href") or "")
        m = re.search(r"selectorget=(\d+)", href)
        if not m:
            continue
        referencia = int(m.group(1))
        if referencia in vistos:
            continue
        vistos.add(referencia)
        contenedor = enlace
        for _ in range(6):
            padre = contenedor.parent
            if padre is None:
                break
            texto_padre = _limpio(padre.get_text(" ", strip=True))
            if len(texto_padre) >= 40:
                contenedor = padre
                break
            contenedor = padre
        texto = _limpio(contenedor.get_text(" ", strip=True))
        m_ubic = re.search(r"Ubicaci[oó]n:?\s*(.+?)(?=\s+[ÓO]rgano convocante|\s+Plazas?:|$)", texto, re.I)
        m_org = re.search(r"[ÓO]rgano convocante:?\s*(.+?)(?=\s+Plazas?:|\s+Titulaci[oó]n|$)", texto, re.I)
        salida.append({"referencia": referencia, "titulo": _limpio(enlace.get_text(" ", strip=True)),
                       "ubicacion": _limpio(m_ubic.group(1)) if m_ubic else None,
                       "organo": _limpio(m_org.group(1)) if m_org else None,
                       "url": urljoin(BASE, href)})
    return salida


def _params_intervalo(desde: date, hasta: date, offset: int = 1) -> dict[str, str]:
    return {
        "buscar": "true", "tipoBusqueda": "BOLSA_EMPLEO", "tipoConvocatoria": "2",
        "tipoFechas": "default", "fechaPublicacionDesde": desde.strftime("%d/%m/%Y"),
        "fechaPublicacionHasta": hasta.strftime("%d/%m/%Y"), "orders": "id", "sort": "desc",
        "desde": str(offset), "viaAcceso": "2",
    }


def descubrir_referencias(client: httpx.Client, desde: date, hasta: date) -> list[dict]:
    todas: dict[int, dict] = {}
    for pagina in range(1, 101):
        offset = 1 if pagina == 1 else (pagina - 1) * TAM_PAGINA + 1
        r = _get(client, RESULTADOS, params=_params_intervalo(desde, hasta, offset))
        tarjetas = _parse_tarjetas(r.text)
        if not tarjetas:
            break
        for item in tarjetas:
            if "AUTONÓMICO - COMUNITAT VALENCIANA" in (item.get("ubicacion") or "").upper():
                todas[item["referencia"]] = item
        total = _parse_total(BeautifulSoup(r.text, "html.parser"))
        if total is not None and len(todas) >= total:
            break
        if len(tarjetas) < TAM_PAGINA:
            break
    return [todas[k] for k in sorted(todas)]


def _extraer_via(texto: str) -> str | None:
    m = re.search(
        r"Tipo de v[ií]a\s+(.+?)(?=\s+(?:[ÓO]rgano convocante|Plazas|Titulaci[oó]n|Requisitos|Observaciones|M[aá]s informaci[oó]n|Plazo de presentaci[oó]n)\b)",
        texto,
        re.I,
    )
    n = _sin(m.group(1) if m else "")
    if "promocion interna" in n:
        return "PROMOCION_INTERNA"
    if "ingreso libre" in n or "acceso libre" in n:
        return "INGRESO_LIBRE"
    if "interinidad" in n:
        return "INTERINIDAD"
    if "contratacion fija" in n:
        return "CONTRATACION_FIJA"
    return None


def _es_admin(titulo: str, texto: str) -> tuple[bool, list[str]]:
    nt = _sin(titulo)
    nd = _sin(texto)
    codigos_detectados = sorted({
        codigo.upper()
        for codigo in re.findall(r"\b[a-c]\d-\d{2}(?:-[a-z0-9]+)*\b", nd, re.I)
    })
    codigos = [codigo for codigo in codigos_detectados if codigo in CODIGOS_ADMIN]
    # En GVA un código explícito prevalece sobre una denominación genérica:
    # especialidades distintas de A1-01/A2-01/C1-01/C2-01 quedan fuera.
    if codigos_detectados:
        return bool(codigos), codigos
    return bool(any(re.search(p, nt) for p in PATRONES_ADMIN)), codigos


def _clasificar_organismo(texto: str | None) -> str:
    n = _sin(texto or "")
    if any(p in n for p in PATRONES_ORGANISMO_NO_GVA):
        return "NO"
    if any(p in n for p in PATRONES_ORGANISMO_GVA):
        return "SI"
    return "REVISION"


def _extraer_publicacion_dogv(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Devuelve el primer PDF DOGV del detalle y su fecha inferida de la URL.

    En las fichas estatales, las disposiciones oficiales aparecen antes que los
    seguimientos. El primer enlace DOGV es por tanto la publicación primaria de
    la convocatoria. Si no puede identificarse de forma inequívoca, no se inventa.
    """
    for enlace in soup.find_all("a", href=True):
        href = str(enlace.get("href") or "")
        if "dogv.gva.es/" not in href.lower() or ".pdf" not in href.lower():
            continue
        url = urljoin(BASE, href)
        m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/pdf/", url, re.I)
        fecha = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        return url, fecha
    return None, None


def parsear_detalle(referencia: int, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    texto = _limpio(soup.get_text(" ", strip=True))
    titulo_tag = soup.find("h1") or soup.find("h2")
    titulo = _limpio(titulo_tag.get_text(" ", strip=True)) if titulo_tag else ""
    m_org = re.search(r"[ÓO]rgano convocante\s+(.+?)(?=\s+Requisitos\b|\s+Observaciones\b|\s+Más información\b|\s+Plazo de presentación\b)", texto, re.I)
    m_personal = re.search(r"Tipo de personal\s+(.+?)(?=\s+Tipo de vía\b)", texto, re.I)
    m_inicio = re.search(r"Desde el\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    m_fin = re.search(r"Hasta el\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    dogv_url, dogv_fecha = _extraer_publicacion_dogv(soup)
    return {
        "referencia": referencia,
        "texto": texto,
        "titulo_detalle": titulo,
        "organo_detalle": _limpio(m_org.group(1)) if m_org else None,
        "tipo_personal": _limpio(m_personal.group(1)) if m_personal else None,
        "via": _extraer_via(texto),
        "fecha_apertura": m_inicio.group(1) if m_inicio else None,
        "fecha_cierre": m_fin.group(1) if m_fin else None,
        "publicacion_oficial_url": dogv_url,
        "publicacion_oficial_fecha": dogv_fecha,
    }


def obtener_detalle(client: httpx.Client, referencia: int) -> dict:
    r = _get(client, DETALLE, params={"selectorServicio": "bolsa_empleo", "selectorget": referencia})
    return parsear_detalle(referencia, r.text)


def clasificar_oportunidad(tarjeta: dict, detalle: dict) -> dict:
    es_admin, codigos = _es_admin(tarjeta.get("titulo") or "", detalle.get("texto") or "")
    via = detalle.get("via")
    organismo_estado = _clasificar_organismo(tarjeta.get("organo") or detalle.get("organo_detalle"))
    incluida = bool(es_admin and organismo_estado == "SI" and via in VIAS_INCLUIDAS)
    return {
        **tarjeta,
        **{k: v for k, v in detalle.items() if k != "texto"},
        "codigos_administrativos": codigos,
        "ambito_administrativo": "SI" if es_admin else "NO",
        "organismo_gva": organismo_estado,
        "es_oportunidad": incluida,
        "motivo": "incluida" if incluida else (
            "promocion_interna" if via == "PROMOCION_INTERNA" else
            "fuera_ambito_administrativo" if not es_admin else
            "organismo_excluido" if organismo_estado == "NO" else
            "organismo_revision" if organismo_estado == "REVISION" else
            "via_no_incluida"
        ),
    }


def nuevo_cliente():
    """Cliente GVA directo con fallback selectivo al proxy español."""
    return nuevo_cliente_gva(
        timeout=httpx.Timeout(45.0, connect=15.0),
        headers={
            "User-Agent": UA,
            "Accept-Language": "es-ES,es;q=0.9",
        },
    )
