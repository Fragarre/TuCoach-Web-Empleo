from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote
import re

import httpx
from bs4 import BeautifulSoup

from . import bop_valencia as _bop
from .database import get_connection
from .ambito_administrativo import clasificar_ambito_administrativo

BOP_PORTAL_URL = _bop.BOP_PORTAL_URL


def _extraer_anuncios_por_texto(html: str) -> list[dict[str, Any]]:
    """Extrae anuncios desde texto plano, usando cada registro como delimitador estable."""
    texto = _bop._norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    registro_patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for mr in registro_patron.finditer(texto):
        numero = mr.group(1)
        inicio = texto.rfind("Anunci", 0, mr.start())
        if inicio < 0:
            continue
        titulo = _bop._norm(texto[inicio:mr.start()]).rstrip(".") + "."
        n = _bop._sin(titulo)
        organismo = "diputacion provincial de valencia" in n or "diputacio provincial de valencia" in n
        incluido = _bop._incluido(titulo)
        if not organismo or not incluido or numero in vistos:
            continue
        vistos.add(numero)
        contexto = texto[inicio:mr.end() + 150]
        fm = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", contexto, re.I)
        fecha = _bop._fecha(fm.group(1)) if fm else None
        resultados.append({
            "titulo": titulo,
            "url": f"{_bop.DOWNLOAD_URL}?anuncioNumReg={quote(numero)}&lang=es",
            "registro": numero,
            "fecha_publicacion": fecha,
        })
    return resultados


def _extraer_anuncios_pagina(html: str) -> list[dict[str, Any]]:
    return _extraer_anuncios_por_texto(html)


def _ajax_html(response_text: str) -> str:
    if "<partial-response" not in response_text:
        return response_text
    bloques = re.findall(r"<!\[CDATA\[(.*?)\]\]>", response_text, flags=re.S)
    if bloques:
        return "\n".join(bloques)
    soup = BeautifulSoup(response_text, "html.parser")
    return "\n".join(u.decode_contents() for u in soup.find_all("update"))


def _view_state(response_text: str) -> str | None:
    m = re.search(r'<update id="javax\.faces\.ViewState"><!\[CDATA\[(.*?)\]\]></update>', response_text, re.S)
    return m.group(1) if m else None


def _obtener_pagina(client: httpx.Client, fecha: date) -> tuple[date, str | None, str | None]:
    """Obtiene todos los registros publicados en una fecha, recorriendo el DataGrid PrimeFaces completo."""
    try:
        r0 = client.get(BOP_PORTAL_URL)
        r0.raise_for_status()
        soup = BeautifulSoup(r0.text, "html.parser")
        forms = soup.find_all("form")
        form = soup.find("form", id="j_idt132")
        if form is None:
            for candidato in forms:
                nombres = {i.get("name") for i in candidato.find_all("input") if i.get("name")}
                if "filtroCalendarioIni_input" in nombres and "filtroCalendarioFin_input" in nombres:
                    form = candidato
                    break
        if form is None:
            return fecha, None, f"No se encontró formulario de búsqueda; url={r0.url}; status={r0.status_code}; forms={[f.get('id') for f in forms]}; html={len(r0.text)}"

        data: dict[str, str] = {}
        for element in form.find_all("input"):
            name = element.get("name")
            if not name:
                continue
            typ = (element.get("type") or "").lower()
            if typ in {"submit", "button", "image", "file", "reset"}:
                continue
            if typ in {"checkbox", "radio"} and not element.has_attr("checked"):
                continue
            data[name] = element.get("value") or "on"

        fecha_txt = fecha.strftime("%d/%m/%Y")
        data["filtroCalendarioIni_input"] = fecha_txt
        data["filtroCalendarioFin_input"] = fecha_txt
        action = form.get("action") or "/bop/xhtml/portal.xhtml"
        if action.startswith("/"):
            url = str(r0.url).split("/bop/", 1)[0] + action
        else:
            url = str(r0.url).rsplit("/", 1)[0] + "/" + action

        ajax_headers = {
            "Referer": str(r0.url),
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/xml, text/xml, */*; q=0.01",
        }
        filtro = dict(data)
        filtro.update({
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "buscarBtn",
            "javax.faces.partial.execute": "buscarBtn filtroCalendarioIni filtroCalendarioFin",
            "javax.faces.partial.render": "messages boletines3 edictos",
            "buscarBtn": "buscarBtn",
        })
        r = client.post(url, data=filtro, headers=ajax_headers)
        r.raise_for_status()
        html_primera = _ajax_html(r.text)
        paginas = [html_primera]

        vs = _view_state(r.text)
        if vs:
            data["javax.faces.ViewState"] = vs

        sm = re.search(
            r'PrimeFaces\.cw\("DataGrid","list",\{id:"list",paginator:\{id:\[[^\]]+\],rows:(\d+),rowCount:(\d+),page:(\d+)',
            html_primera,
        )
        if sm:
            rows = int(sm.group(1))
            row_count = int(sm.group(2))
            for first in range(rows, row_count, rows):
                pagina = dict(data)
                pagina.update({
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": "list",
                    "javax.faces.behavior.event": "page",
                    "javax.faces.partial.event": "page",
                    "javax.faces.partial.execute": "list",
                    "javax.faces.partial.render": "list",
                    "list": "list",
                    "list_pagination": "true",
                    "list_first": str(first),
                    "list_rows": str(rows),
                    "list_skipChildren": "true",
                    "list_encodeFeature": "true",
                })
                rp = client.post(url, data=pagina, headers=ajax_headers)
                rp.raise_for_status()
                paginas.append(_ajax_html(rp.text))
                vs = _view_state(rp.text)
                if vs:
                    data["javax.faces.ViewState"] = vs

        return fecha, "\n".join(paginas), None
    except Exception as exc:
        return fecha, None, str(exc)


def descubrir_anuncios(client: httpx.Client, historico: bool = False, dias: int = 1) -> list[dict[str, Any]]:
    if not historico:
        r = client.get(_bop.BOP_URL)
        r.raise_for_status()
        return _extraer_anuncios_pagina(r.text)
    hoy = date.today()
    desde = hoy - timedelta(days=max(0, dias - 1))
    fechas = [desde + timedelta(days=i) for i in range((hoy - desde).days + 1)]
    resultados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def obtener(fecha: date) -> tuple[date, str | None, str | None]:
        headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
            return _obtener_pagina(c, fecha)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(obtener, fecha) for fecha in fechas]
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



def _plazas_corregida(s: str) -> int | None:
    plazas_libres = _bop._plazas_turno_libre(s)
    if plazas_libres is not None:
        return plazas_libres
    n = _bop._sin(s)
    patrones = [
        r"(?:seleccion|seleccio)\s+d['’](una|un)\s+(?:plazas?|places?|placa)",
        r"(?:seleccion|seleccio)\s+de\s+(una|un)\s+(?:plazas?|places?|placa)",
        r"(?:seleccion|seleccio)\s+de\s+(\d+)\s+(?:plazas?|places?|placa)",
        r"(?:seleccion|seleccio)\s+de\s+(una|dos|tres|cuatro|cinc|sis|set|siete|vuit|ocho|nou|nueve|deu|diez)\s+(?:plazas?|places?|placa)",
    ]
    palabras = {"una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinc": 5, "sis": 6, "set": 7, "siete": 7, "vuit": 8, "ocho": 8, "nou": 9, "nueve": 9, "deu": 10, "diez": 10}
    for patron in patrones:
        m = re.search(patron, n, re.I)
        if m:
            valor = m.group(1)
            return int(valor) if valor.isdigit() else palabras.get(valor.lower())
    return None


def _grupo_subgrupo_corregida(s: str) -> tuple[str | None, str | None]:
    n = _bop._sin(s)
    m = re.search(r"(?:subgrupo|grupo)\s*:?\s*([abc]\d(?:/\d)?)", n, re.I)
    if not m:
        return None, None
    subgrupo = m.group(1).upper()
    return subgrupo[0], subgrupo


_bop._plazas = _plazas_corregida
_bop._grupo_subgrupo = _grupo_subgrupo_corregida


def _postprocesar_ambito_y_cambios() -> tuple[int, int]:
    clasificados = 0
    cambios_tecnicos = 0
    with get_connection() as connection, connection.cursor() as cursor:
        organismo_id, _ = _bop._resolver_identidad_bop_valencia(cursor)
        cursor.execute(
            """
            SELECT id, denominacion, cuerpo_escala, grupo
            FROM procesos
            WHERE organismo_id=%s AND ambito_administrativo='REVISION'
            """,
            (organismo_id,),
        )
        for proceso_id, denominacion, cuerpo_escala, grupo in cursor.fetchall():
            ambito = clasificar_ambito_administrativo({
                "denominacion": denominacion,
                "cuerpo_escala": cuerpo_escala,
                "grupo": grupo,
            })
            if ambito == "REVISION":
                continue
            cursor.execute(
                "UPDATE procesos SET ambito_administrativo=%s, updated_at=NOW() WHERE id=%s AND ambito_administrativo='REVISION'",
                (ambito, proceso_id),
            )
            clasificados += cursor.rowcount

        cursor.execute(
            """
            UPDATE cambios c
            SET significativo=FALSE
            FROM procesos p
            WHERE p.id=c.proceso_id
              AND p.organismo_id=%s
              AND c.significativo=TRUE
              AND c.valor_anterior IS NULL
              AND COALESCE(c.campo,'') <> 'publicacion'
            """,
            (organismo_id,),
        )
        cambios_tecnicos = cursor.rowcount
        connection.commit()
    return clasificados, cambios_tecnicos


def importar_bop_valencia(historico: bool = False, dias: int = 1) -> dict[str, Any]:
    _bop.descubrir_anuncios = descubrir_anuncios
    stats = _bop.importar_bop_valencia(historico=historico, dias=dias)
    clasificados, cambios_tecnicos = _postprocesar_ambito_y_cambios()
    stats["ambito_administrativo_actualizados"] = clasificados
    stats["cambios_tecnicos_desactivados"] = cambios_tecnicos
    return stats


def _extraer_pdf_diagnostico(r: httpx.Response, *, origen: str, referencia: str) -> dict[str, Any]:
    content_type = r.headers.get("content-type") or ""
    es_pdf = r.content.startswith(b"%PDF") or "pdf" in content_type.lower()
    resultado: dict[str, Any] = {
        "origen": origen,
        "referencia": referencia,
        "url": str(r.url),
        "status": r.status_code,
        "content_type": content_type,
        "bytes": len(r.content),
        "es_pdf": es_pdf,
    }
    if not es_pdf:
        resultado["respuesta_inicio"] = r.text[:2000]
        return resultado
    try:
        import fitz
        documento = fitz.open(stream=r.content, filetype="pdf")
        paginas = [pagina.get_text("text") for pagina in documento]
        texto = "\n".join(paginas)
        normalizado = _bop._norm(texto)
        terminos = (
            "publicar", "publicará", "publicaran", "publicarán", "publicació", "publicación",
            "tauler", "tablón", "sede electrónica", "seu electrònica", "web municipal", "pàgina web",
            "anuncios sucesivos", "successius anuncis", "restantes anuncios", "següents anuncis",
            "lista provisional", "lista definitiva", "admitidos", "excluidos", "tribunal", "ejercicio",
            "calificaciones", "aprobados", "nombramiento",
        )
        fragmentos: list[str] = []
        texto_busqueda = normalizado.lower()
        posiciones: list[int] = []
        for termino in terminos:
            inicio = 0
            termino_n = _bop._norm(termino).lower()
            while True:
                pos = texto_busqueda.find(termino_n, inicio)
                if pos < 0:
                    break
                posiciones.append(pos)
                inicio = pos + len(termino_n)
        for pos in sorted(set(posiciones)):
            frag = normalizado[max(0, pos - 450): min(len(normalizado), pos + 900)].strip()
            if frag and all(frag not in existente and existente not in frag for existente in fragmentos):
                fragmentos.append(frag)

        secciones: dict[str, str] = {}
        if origen == "DOGV" and referencia == "2020/188":
            objetivos = (
                "Base séptima", "Base setena", "Base octava", "Base huitena",
                "Base novena", "Base desena", "Base décima", "Base undécima",
                "Base duodécima", "Base decimotercera",
            )
            busqueda_lower = normalizado.lower()
            for objetivo in objetivos:
                objetivo_n = _bop._norm(objetivo)
                pos = busqueda_lower.find(objetivo_n.lower())
                if pos >= 0 and objetivo not in secciones:
                    secciones[objetivo] = normalizado[pos:min(len(normalizado), pos + 7000)]

            terminos_dirigidos = (
                "calificaciones", "calificación", "resultado", "resultados", "ejercicio",
                "ejercicios", "tablón", "web municipal", "página web", "publicará", "publicarán",
                "anuncios", "convocatoria del siguiente ejercicio", "llamamiento",
            )
            fragmentos_dirigidos: list[str] = []
            inicio_busqueda = 0
            marcas_base7 = [busqueda_lower.find(_bop._norm(x).lower()) for x in ("Base séptima", "Base setena")]
            marcas_base7 = [x for x in marcas_base7 if x >= 0]
            if marcas_base7:
                inicio_busqueda = min(marcas_base7)
            for termino in terminos_dirigidos:
                tn = _bop._norm(termino).lower()
                inicio = inicio_busqueda
                while True:
                    pos = busqueda_lower.find(tn, inicio)
                    if pos < 0:
                        break
                    frag = normalizado[max(inicio_busqueda, pos - 700):min(len(normalizado), pos + 1400)].strip()
                    if frag and all(frag not in existente and existente not in frag for existente in fragmentos_dirigidos):
                        fragmentos_dirigidos.append(frag)
                    inicio = pos + len(tn)
            resultado["secciones_bases"] = secciones
            resultado["fragmentos_seguimiento"] = fragmentos_dirigidos[:40]

        resultado.update({
            "paginas": len(paginas),
            "texto_len": len(normalizado),
            "texto_inicio": normalizado[:4000],
            "fragmentos_publicacion": fragmentos[:30],
        })
        return resultado
    except Exception as exc:
        resultado["error_extraccion"] = f"{type(exc).__name__}: {exc}"
        return resultado


def diagnosticar_bop(client: httpx.Client, fecha: str | None = None) -> dict[str, Any]:
    if fecha and fecha.strip().lower() == "dogv:2020/188":
        url = "https://dogv.gva.es/datos/2020/01/30/pdf/2020_188.pdf"
        r = client.get(url)
        r.raise_for_status()
        return _extraer_pdf_diagnostico(r, origen="DOGV", referencia="2020/188")

    if fecha and re.fullmatch(r"\d{4}/\d+", fecha.strip()):
        registro = fecha.strip()
        url = f"{_bop.DOWNLOAD_URL}?anuncioNumReg={quote(registro)}&lang=es"
        r = client.get(url)
        r.raise_for_status()
        return _extraer_pdf_diagnostico(r, origen="BOP_VALENCIA", referencia=registro)

    if fecha:
        try:
            fecha_obj = date.fromisoformat(fecha)
        except ValueError:
            fecha_obj = date.today()
        r0 = client.get(BOP_PORTAL_URL)
        r0.raise_for_status()
        soup = BeautifulSoup(r0.text, "html.parser")
        forms = soup.find_all("form")
        _, html, error = _obtener_pagina(client, fecha_obj)
        texto = _bop._norm(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True))
        matches = list(re.finditer(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", texto, re.I))
        bloques = []
        for m in matches:
            inicio = texto.rfind("Anunci", 0, m.start())
            titulo = _bop._norm(texto[inicio:m.start()]) if inicio >= 0 else ""
            n = _bop._sin(titulo)
            bloques.append({"registro": m.group(1), "titulo": titulo[:500], "organismo": "diputacion provincial de valencia" in n or "diputacio provincial de valencia" in n, "incluido": _bop._incluido(titulo)})
        anuncios = _extraer_anuncios_pagina(html or "")
        return {"fecha_solicitada": fecha, "error": error, "get_url": str(r0.url), "get_status": r0.status_code, "get_html": len(r0.text), "form_ids": [f.get("id") for f in forms], "post_html": len(html or ""), "anuncios_parser": len(anuncios), "registro_matches": len(matches), "bloques_anunci_registro": len(bloques), "candidatos_organismo": sum(1 for b in bloques if b["organismo"]), "candidatos_incluidos": sum(1 for b in bloques if b["organismo"] and b["incluido"]), "muestra_bloques": [b for b in bloques if b["registro"] in {"2026/10873", "2026/10875", "2026/10878", "2026/10879", "2026/10881"}], "primeros_registros": re.findall(r"\b2026/\d+\b", texto)[:20], "contiene_10873": "2026/10873" in (html or "")}
    r = client.get(_bop.BOP_URL)
    r.raise_for_status()
    texto = _bop._norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
    return {"url": str(r.url), "status": r.status_code, "ancho_html": len(r.text), "anuncios_parser": len(_extraer_anuncios_pagina(r.text)), "primeros_registros": re.findall(r"\b2026/\d+\b", texto)[:20]}
