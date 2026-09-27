from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import bop_valencia as _bop
from . import bop_valencia_patch as _bop_patch
from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection
from .estado_proceso import clasificar_evento_terminal
from .organismos import resolver_fuente, resolver_organismo


def _sin(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _reparar_mojibake_utf8(texto: str) -> str:
    if not texto or not any(m in texto for m in ("Ã", "Â", "â€", "â€™", "â€œ", "â€")):
        return texto

    def reparar_fragmento(fragmento: str) -> str:
        if not any(m in fragmento for m in ("Ã", "Â", "â€", "â€™", "â€œ", "â€")):
            return fragmento
        for encoding in ("latin-1", "cp1252"):
            try:
                reparado = fragmento.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if reparado.count("Ã") + reparado.count("Â") + reparado.count("â") < fragmento.count("Ã") + fragmento.count("Â") + fragmento.count("â"):
                return reparado
        return fragmento

    reparado = reparar_fragmento(texto)
    if reparado != texto:
        return reparado
    return re.sub(r"\S+", lambda m: reparar_fragmento(m.group(0)), texto)


def _es_emisor_ayuntamiento(titulo: str) -> bool:
    n = _sin(titulo).strip()
    return bool(re.match(r"^(?:anunci|anuncio)\s+(?:(?:de|del)\s+)?(?:l['’]\s*)?(?:ayuntamiento|ajuntament)\s+(?:de|del|d['’])\s+", n, re.I))


def _municipio_desde_titulo(titulo: str) -> str | None:
    if not _es_emisor_ayuntamiento(titulo):
        return None
    n = _sin(titulo)
    fin = r"(?=\s+sobre\b|\s+per\s+a\b|\s+para\b|[.,;:]|$)"
    for patron in (rf"(?:ayuntamiento|ajuntament)\s+de\s+(.+?){fin}", rf"(?:ayuntamiento|ajuntament)\s+del\s+(.+?){fin}", rf"(?:ayuntamiento|ajuntament)\s+d['’]\s*(.+?){fin}"):
        m = re.search(patron, n, re.I)
        if m:
            return " ".join(m.group(1).split()).strip()[:120]
    return None


def _municipio_visible_desde_titulo(titulo: str) -> str | None:
    if not _es_emisor_ayuntamiento(titulo):
        return None
    t = _reparar_mojibake_utf8(titulo)
    fin = r"(?=\s+sobre\b|\s+per\s+a\b|\s+para\b|[.,;:]|$)"
    for patron in (rf"(?:Ayuntamiento|Ajuntament)\s+de\s+(.+?){fin}", rf"(?:Ayuntamiento|Ajuntament)\s+del\s+(.+?){fin}", rf"(?:Ayuntamiento|Ajuntament)\s+d['’]\s*(.+?){fin}"):
        m = re.search(patron, t, re.I)
        if m:
            return " ".join(m.group(1).split()).strip()[:120]
    return None


def _es_perfil_administrativo(titulo: str) -> bool:
    return clasificar_ambito_administrativo({"denominacion": titulo, "cuerpo_escala": None, "grupo": None}) == "SI"


def _clasificar_anuncio(titulo: str) -> str:
    n = _sin(titulo)
    internos = ("libre designacion", "lliure designacio", "comision de servicios", "comissio de serveis", "concurso de traslados", "concurs de trasllats", "concurso especifico de meritos", "concurs especific de merits", "provision de puesto", "provisio de lloc", "provision del puesto", "provisio del lloc", "abierto a otras administraciones publicas", "obert a altres administracions publiques", "promocion interna", "promocio interna", "cesion de la bolsa", "cessio de la borsa", "cessio de les borses", "cesion de bolsas", "conveni de collaboracio", "conveni de col·laboracio", "convenio de colaboracion")
    if any(x in n for x in internos):
        return "EXCLUIDO_INTERNO"
    ruido = ("subvencion", "subvencio", "premio", "premi", "ayuda", "ajuda", "ordenanza fiscal", "ordenanca fiscal", "tasa", "taxa", "gestion tributaria", "gestio tributaria", "recaptacio", "recaudacion")
    if any(x in n for x in ruido) and not any(x in n for x in ("administratiu", "administrativo", "auxiliar administratiu", "auxiliar administrativo", "tecnic d'administracio", "tecnico de administracion")):
        return "RUIDO"
    bolsas = ("borsa d'ocupacio", "borsa de treball", "bolsa de empleo", "bolsa de trabajo", "funcionari interi", "funcionario interino", "funcionaria interina", "nomenament interi")
    if any(x in n for x in bolsas):
        return "BOLSA_TEMPORAL"
    seguimiento = ("relacio provisional", "relacion provisional", "relacio definitiva", "relacion definitiva", "admeses", "admesos", "admitidos", "admitidas", "exclosos", "excloses", "excluidos", "excluidas", "tribunal", "organ tecnic de seleccio", "organo tecnico de seleccion", "primer exercici", "primer ejercicio", "data de l'exercici", "fecha del ejercicio", "nomenament", "nombramiento", "persona aprovada", "persones aprovades", "resultats", "resultados", "proposta de nomenament", "propuesta de nombramiento", "correccio d'errors", "correccion de errores", "modificacio", "modificacion", "resolucio de recursos", "resolucion de recursos", "acumulacio de places", "acumulacion de plazas")
    if any(x in n for x in seguimiento):
        return "SEGUIMIENTO"
    bases = ("aprovacio de les bases", "aprobacion de las bases", "bases de la convocatoria", "bases i la convocatoria", "bases y la convocatoria", "bases reguladores del procediment selectiu", "bases reguladoras del procedimiento selectivo")
    return "NUEVA_CONVOCATORIA" if any(x in n for x in bases) else "SEGUIMIENTO"


def _es_empleo_administrativo(titulo: str) -> bool:
    return _es_emisor_ayuntamiento(titulo) and _es_perfil_administrativo(titulo)


def _extraer_anuncios_municipales(html: str) -> list[dict[str, Any]]:
    html = _reparar_mojibake_utf8(html)
    texto = _bop._norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    texto = _reparar_mojibake_utf8(texto)
    patron = re.compile(r"N[uú]m\.\s*(?:de\s*)?(?:registre|registro)\s*:?\s*(\d{4}/\d+)", re.I)
    resultados, vistos = [], set()
    for mr in patron.finditer(texto):
        numero = mr.group(1)
        inicio = max(texto.rfind("Anunci", 0, mr.start()), texto.rfind("Anuncio", 0, mr.start()))
        if inicio < 0:
            continue
        titulo = _reparar_mojibake_utf8(_bop._norm(texto[inicio:mr.start()]).rstrip(".") + ".")
        municipio = _municipio_desde_titulo(titulo)
        if not municipio or numero in vistos:
            continue
        vistos.add(numero)
        contexto = texto[inicio:mr.end() + 150]
        fm = re.search(r"(?:Data publicaci[oó]|Fecha publicaci[oó]n)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", contexto, re.I)
        fecha = _bop._fecha(fm.group(1)) if fm else None
        resultados.append({"titulo": titulo, "url": f"{_bop.DOWNLOAD_URL}?anuncioNumReg={quote(numero)}&lang=es", "registro": numero, "fecha_publicacion": fecha, "municipio": municipio, "municipio_visible": _municipio_visible_desde_titulo(titulo)})
    return resultados


def descubrir_municipales_bop(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    headers = {"User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)", "Accept-Language": "es-ES,es;q=0.9"}
    hallazgos, errores, vistos = [], [], set()
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        fecha = desde
        while fecha <= hasta:
            try:
                _, html, _ = _bop_patch._obtener_pagina(client, fecha)
            except Exception as exc:
                errores.append({"fecha": fecha.isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                fecha += timedelta(days=1)
                continue
            if html:
                for a in _extraer_anuncios_municipales(html):
                    if not _es_empleo_administrativo(a["titulo"]) or a["registro"] in vistos:
                        continue
                    vistos.add(a["registro"])
                    hallazgos.append({"registro": a["registro"], "fecha_publicacion": a["fecha_publicacion"].isoformat() if a["fecha_publicacion"] else None, "municipio_detectado": a["municipio"], "municipio_visible": a["municipio_visible"], "clase": _clasificar_anuncio(a["titulo"]), "titulo": a["titulo"], "url": a["url"]})
            fecha += timedelta(days=1)
    hallazgos.sort(key=lambda x: (x["fecha_publicacion"] or "", x["registro"]))
    conteo = Counter(h["clase"] for h in hallazgos)
    candidatas = [h for h in hallazgos if h["clase"] == "NUEVA_CONVOCATORIA"]
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "descubiertos": len(hallazgos), "resumen_clases": dict(sorted(conteo.items())), "candidatas_nuevas": len(candidatas), "dias_con_error": len(errores), "errores": errores, "hallazgos": hallazgos}


def _nombre_municipio(slug: str) -> str:
    return " ".join(p.capitalize() for p in slug.split())


def _extraer_plazas(titulo: str) -> int | None:
    n = _sin(titulo)
    palabras = {"una": 1, "un": 1, "dues": 2, "dos": 2, "tres": 3, "quatre": 4, "cuatro": 4, "cinc": 5, "cinco": 5, "sis": 6, "seis": 6, "set": 7, "siete": 7, "huit": 8, "vuit": 8, "ocho": 8, "nou": 9, "nueve": 9, "deu": 10, "diez": 10}
    m = re.search(r"\b(\d+|una|un|dues|dos|tres|quatre|cuatro|cinc|cinco|sis|seis|set|siete|huit|vuit|ocho|nou|nueve|deu|diez)\s+(?:placa|places|plaza|plazas|lloc|llocs|puesto|puestos)\b", n)
    if m:
        valor = m.group(1)
        return int(valor) if valor.isdigit() else palabras.get(valor)
    if re.search(r"\bla\s+(?:placa|plaza)\b", n) or re.search(r"\b(?:el|l['’])\s*(?:lloc|puesto)\b", n):
        return 1
    return None


def _familia_perfil(titulo: str) -> str | None:
    n = _sin(titulo)
    if "auxiliar administr" in n:
        return "AUXILIAR_ADMINISTRATIVO"
    if any(x in n for x in ("tecnic d'administracio general", "tecnico de administracion general", "tecnica de administracion general", "tecnico/a de administracion general")):
        return "TAG"
    if "administratiu" in n or "administrativo" in n or "administrativa" in n:
        return "ADMINISTRATIVO"
    return None


def _extraer_codigo_proceso(titulo: str) -> str | None:
    n = _sin(titulo)
    patrones = (
        r"\b(?:expedient|expediente)\s*(?:num\.?|n[uú]m\.?|numero)?\s*[:.-]?\s*([a-z0-9][a-z0-9._/-]{3,})",
        r"\b(?:codi|codigo)\s+(?:de\s+)?(?:convocatoria|convocatoria)\s*[:.-]?\s*([a-z0-9][a-z0-9._/-]{3,})",
    )
    for patron in patrones:
        m = re.search(patron, n, re.I)
        if m:
            return m.group(1).rstrip(".,;:").upper()
    return None


def _es_seguimiento_selectivo_claro(titulo: str) -> bool:
    n = _sin(titulo)
    ajeno = ("cessio de la borsa", "cessio de les borses", "cesion de la bolsa", "cesion de bolsas", "conveni de collaboracio", "conveni de col·laboracio", "convenio de colaboracion")
    if any(x in n for x in ajeno):
        return False
    hitos = ("relacio provisional", "relacion provisional", "relacio definitiva", "relacion definitiva", "admeses", "admesos", "admitidos", "admitidas", "exclosos", "excloses", "excluidos", "excluidas", "tribunal qualificador", "tribunal calificador", "organ tecnic de seleccio", "organo tecnico de seleccion", "primer exercici", "primer ejercicio", "data de l'exercici", "fecha del ejercicio", "persona aprovada", "persones aprovades", "resultats", "resultados", "proposta de nomenament", "propuesta de nombramiento", "correccio d'errors", "correccion de errores", "acumulacio de places", "acumulacion de plazas", "nomenament com a funcionari", "nombramiento como funcionario", "nombramiento como funcionaria")
    return any(x in n for x in hitos)


def _es_finalizacion(titulo: str) -> bool:
    n = _sin(titulo)
    finales = ("nomenament com a funcionari", "nombramiento como funcionario", "nombramiento como funcionaria", "presa de possessio", "toma de posesion", "finalitzacio del proces selectiu", "finalizacion del proceso selectivo")
    return any(x in n for x in finales)


def _buscar_proceso_seguimiento(cursor, hallazgo: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    familia = _familia_perfil(hallazgo["titulo"])
    if not familia:
        return None, "SIN_FAMILIA"
    if not _es_seguimiento_selectivo_claro(hallazgo["titulo"]):
        return None, "NO_ES_CONTINUIDAD_SELECTIVA"
    codigo_seguimiento = _extraer_codigo_proceso(hallazgo["titulo"])
    cursor.execute(
        """
        SELECT p.id,p.denominacion,p.plazas,p.estado,p.tipo_proceso,p.fecha_convocatoria,o.municipio
        FROM procesos p
        JOIN organismos o ON o.id=p.organismo_id
        WHERE o.tipo='AYUNTAMIENTO'
          AND p.ambito_administrativo='SI'
          AND p.estado='EN_CURSO'
          AND p.fecha_convocatoria IS NOT NULL
          AND p.fecha_convocatoria <= %s
        ORDER BY p.fecha_convocatoria DESC,p.id DESC
        """,
        (hallazgo["fecha_publicacion"],),
    )
    candidatos = []
    for p in cursor.fetchall():
        if _sin(p.get("municipio") or "") != _sin(hallazgo["municipio_detectado"]):
            continue
        if _familia_perfil(p.get("denominacion") or "") != familia:
            continue
        candidatos.append(p)
    if not candidatos:
        return None, "SIN_COINCIDENCIA"
    if codigo_seguimiento:
        por_codigo = [p for p in candidatos if _extraer_codigo_proceso(p.get("denominacion") or "") == codigo_seguimiento]
        if len(por_codigo) == 1:
            return por_codigo[0], "CODIGO_EXACTO"
        if len(por_codigo) > 1:
            return None, "CODIGO_AMBIGUO"
        return None, "CODIGO_SIN_COINCIDENCIA"
    if len(candidatos) == 1:
        return candidatos[0], "UNICO_HITO_SELECTIVO"
    return None, "AMBIGUO_SIN_CODIGO"


def importar_municipales_bop(*, hasta: date, dias: int = 30, aplicar: bool = False) -> dict[str, Any]:
    diagnostico = descubrir_municipales_bop(hasta=hasta, dias=dias)
    hallazgos = [h for h in diagnostico["hallazgos"] if h["clase"] in ("NUEVA_CONVOCATORIA", "SEGUIMIENTO")]
    candidatas = [h for h in hallazgos if h["clase"] == "NUEVA_CONVOCATORIA"]
    seguimientos = [h for h in hallazgos if h["clase"] == "SEGUIMIENTO"]
    resultado = {"modo": "APLICAR" if aplicar else "SOLO_REVISION", "desde": diagnostico["desde"], "hasta": diagnostico["hasta"], "candidatas": len(candidatas), "seguimientos": len(seguimientos), "dias_con_error": diagnostico["dias_con_error"], "nuevos": 0, "existentes": 0, "seguimientos_vinculados": 0, "seguimientos_revision": 0, "seguimientos_sin_cambios": 0, "organismos_creados": 0, "detalle": []}

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        fuente = resolver_fuente(
            cursor,
            nombre="Boletín Oficial de la Provincia de Valencia",
            tipo="BOP",
        )
        fuente_id = fuente["id"]

        for h in hallazgos:
            if h["clase"] == "SEGUIMIENTO":
                proceso, motivo = _buscar_proceso_seguimiento(cursor, h)
                item = {"registro": h["registro"], "clase": "SEGUIMIENTO", "titulo": h["titulo"], "municipio": h["municipio_detectado"], "vinculacion": motivo, "proceso_id": proceso["id"] if proceso else None}
                if not proceso:
                    resultado["seguimientos_revision"] += 1
                    resultado["detalle"].append(item)
                    continue
                resultado["seguimientos_vinculados"] += 1
                plazas_nuevas = _extraer_plazas(h["titulo"])
                cambia_plazas = plazas_nuevas is not None and plazas_nuevas != proceso.get("plazas")
                estado_terminal = clasificar_evento_terminal(proceso.get("tipo_proceso"), h["titulo"])
                finaliza = bool(estado_terminal) and str(proceso.get("estado") or "").upper() not in {"FINALIZADO", "DESISTIDO", "ANULADO", "CANCELADO"}
                if cambia_plazas:
                    item["plazas_anterior"] = proceso.get("plazas")
                    item["plazas_nueva"] = plazas_nuevas
                if finaliza:
                    item["estado_nuevo"] = estado_terminal
                if aplicar:
                    cursor.execute("SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s LIMIT 1", (fuente_id, h["registro"]))
                    pub = cursor.fetchone()
                    publicacion_nueva = pub is None
                    if publicacion_nueva:
                        cursor.execute("INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json,detectada_at) VALUES (%s,%s,%s,'BOP',%s,%s,%s,%s,NOW()) RETURNING id", (proceso["id"], fuente_id, h["registro"], h["titulo"], h["fecha_publicacion"], h["url"], Jsonb({"origen": "BOP_VALENCIA_MUNICIPAL", "clase": "SEGUIMIENTO"})))
                        item["publicacion_id"] = cursor.fetchone()["id"]
                    else:
                        item["publicacion_id"] = pub["id"]

                    updates, params = [], []
                    if cambia_plazas:
                        updates.append("plazas=%s")
                        params.append(plazas_nuevas)
                    if finaliza:
                        updates.append("estado=%s")
                        params.append(estado_terminal)
                    if publicacion_nueva:
                        updates.append("ultima_publicacion_at=%s")
                        params.append(h["fecha_publicacion"])
                    if updates:
                        updates.append("updated_at=NOW()")
                        params.append(proceso["id"])
                        cursor.execute(f"UPDATE procesos SET {','.join(updates)} WHERE id=%s", tuple(params))
                    else:
                        resultado["seguimientos_sin_cambios"] += 1
                        item["estado_importacion"] = "SIN_CAMBIOS"
                resultado["detalle"].append(item)
                continue

            estable = f"BOPMUN:{h['registro']}"
            cursor.execute("SELECT id FROM procesos WHERE identificador_estable=%s", (estable,))
            ex = cursor.fetchone()
            if ex:
                resultado["existentes"] += 1
                resultado["detalle"].append({"registro": h["registro"], "clase": "NUEVA_CONVOCATORIA", "estado": "EXISTENTE", "proceso_id": ex["id"]})
                continue
            if not aplicar:
                resultado["detalle"].append(h)
                continue

            municipio = h["municipio_detectado"]
            org = resolver_organismo(
                cursor,
                tipo="AYUNTAMIENTO",
                provincia="Valencia",
                municipio=municipio,
            )
            visible = h.get("municipio_visible") or _nombre_municipio(municipio)
            if org:
                organismo_id = org["id"]
                nombre_esperado = f"Ayuntamiento de {visible}"
                if org.get("nombre") != nombre_esperado:
                    cursor.execute("UPDATE organismos SET nombre=%s,activo=TRUE,updated_at=NOW() WHERE id=%s", (nombre_esperado, organismo_id))
            else:
                nombre = f"Ayuntamiento de {visible}"
                cursor.execute("INSERT INTO organismos (nombre,tipo,municipio,provincia,activo,created_at,updated_at) VALUES (%s,'AYUNTAMIENTO',%s,'Valencia',TRUE,NOW(),NOW()) RETURNING id", (nombre, municipio))
                organismo_id = cursor.fetchone()["id"]
                resultado["organismos_creados"] += 1
            cursor.execute("INSERT INTO procesos (organismo_id,codigo_externo,identificador_estable,denominacion,plazas,estado,fecha_convocatoria,fuente_principal_id,es_oportunidad,ambito_administrativo,datos_json,updated_at) VALUES (%s,%s,%s,%s,%s,'EN_CURSO',%s,%s,TRUE,'SI',%s,NOW()) RETURNING id", (organismo_id, h["registro"], estable, h["titulo"], _extraer_plazas(h["titulo"]), h["fecha_publicacion"], fuente_id, Jsonb({"url_oficial": h["url"], "bop_registro": h["registro"], "origen": "BOP_VALENCIA_MUNICIPAL"})))
            pid = cursor.fetchone()["id"]
            resultado["nuevos"] += 1
            resultado["detalle"].append({"registro": h["registro"], "clase": "NUEVA_CONVOCATORIA", "estado": "NUEVO", "proceso_id": pid, "municipio": municipio})

        if aplicar:
            connection.commit()
        else:
            connection.rollback()
    return resultado


def listar_municipios_detectados() -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,nombre,municipio,activo FROM organismos WHERE tipo='AYUNTAMIENTO' ORDER BY municipio NULLS LAST,nombre")
        rows = cursor.fetchall()
    return [{"id": r[0], "nombre": r[1], "municipio": r[2], "activo": r[3]} for r in rows]