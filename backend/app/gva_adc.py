from __future__ import annotations

"""Inventario de solo lectura de anuncios de difícil cobertura (ADC) GVA.

No escribe en base de datos ni genera notificaciones. Descubre ADC de los
cuerpos administrativos generales A1-01, A2-01, C1-01 y C2-01 y conserva los
datos necesarios para una posterior persistencia y relación documental con
bolsas.
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

from .database import get_connection
from .organismos import resolver_fuente, resolver_organismo

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


def _plazo_adc(soup: BeautifulSoup, texto: str) -> dict[str, str | None]:
    """Extrae únicamente intervalos asociados explícitamente a Plazo/Termini."""
    texto_lineas = "\n".join(soup.stripped_strings)
    patron = (
        r"(?:Plazo|Termini)(?:\s+de\s+(?:presentaci[oó]n|presentacio|la etapa actual))?"
        r"\s*:?\s*(?:Desde|Des de)?\s*(?:el\s+)?"
        r"(\d{2}[-/]\d{2}[-/]\d{4})\s*"
        r"(?:hasta|fins(?:\s+al)?|a)\s*(?:el\s+)?"
        r"(\d{2}[-/]\d{2}[-/]\d{4})"
    )
    m = re.search(patron, texto_lineas, re.I)
    if not m:
        return {"apertura": None, "cierre": None}
    return {
        "apertura": m.group(1).replace("/", "-"),
        "cierre": m.group(2).replace("/", "-"),
    }

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

def _bolsas_documento_pdf(client, documentos: list[dict[str, str]]) -> list[str]:
    """Extrae bolsas solo cuando el PDF oficial contiene una lista explícita."""
    halladas: set[str] = set()
    # La lista oficial puede continuar en líneas siguientes del PDF. El bloque
    # admite únicamente referencias y separadores; se detiene al empezar texto.
    patron_lista = re.compile(
        r"\b(?:bolsas|borses)\s*[:.]?\s*"
        r"((?:\d{2,4}(?:/\d{2,4})?(?:[- ]?[bl])?\s*[,.;]?\s*)+)",
        re.I,
    )
    patron_ref = re.compile(r"\b\d{2,4}(?:/\d{2,4})?(?:[- ]?[bl])?\b", re.I)
    for documento in documentos:
        url = documento.get("url")
        if not url:
            continue
        try:
            respuesta = _get_gva_con_reintentos(client, url, intentos=1)
            lector = PdfReader(io.BytesIO(respuesta.content))
            texto_pdf = "\n".join((pagina.extract_text() or "") for pagina in lector.pages)
        except Exception:
            continue
        normalizado = _sin_acentos(texto_pdf)
        for m in patron_lista.finditer(normalizado):
            for ref in patron_ref.findall(m.group(1)):
                halladas.add(ref.upper().replace(" ", ""))
    return sorted(halladas)

def _datos_etapas(soup: BeautifulSoup, texto: str) -> dict[str, Any]:
    norm = _sin_acentos(texto)
    texto_lineas = "\n".join(soup.stripped_strings)
    # La etapa termina en una etiqueta estructural; no usamos "Anuncio", porque
    # puede formar parte del propio nombre de la etapa.
    m_etapa = re.search(
        r"Etapa actual\s*:\s*(.+?)"
        r"(?=\s*(?:Data publicaci|Fecha publicaci|Termini|Plazo|Documents?|Documentos?|"
        r"C[oó]digo SIA|Codi SIA|Convocatoria)\b)",
        texto_lineas,
        re.I | re.S,
    )
    etapa = " ".join(m_etapa.group(1).split()) if m_etapa else None
    publicaciones = []
    for m in re.finditer(
        r"(?:Data|Fecha)\s+publicaci[^:]*:\s*(\d{2}[-/]\d{2}[-/]\d{4})",
        texto_lineas,
        re.I,
    ):
        publicaciones.append(m.group(1).replace("/", "-"))
    estado_plazo = "CERRADO" if ("termini tancat" in norm or "plazo cerrado" in norm) else (
        "ABIERTO" if ("termini obert" in norm or "plazo abierto" in norm) else None
    )
    documentos = []
    vistos: set[str] = set()
    for enlace in soup.find_all("a", href=True):
        href = str(enlace.get("href") or "")
        if ".pdf" not in href.lower():
            continue
        url_pdf = urljoin(gva_clean.GVA_BASE_URL, href)
        if url_pdf in vistos:
            continue
        vistos.add(url_pdf)
        documentos.append({
            "texto": " ".join(enlace.get_text(" ", strip=True).split()),
            "url": url_pdf,
        })
    return {
        "etapa_actual": etapa,
        "estado_plazo": estado_plazo,
        "fechas_publicacion": list(dict.fromkeys(publicaciones)),
        "documentos_pdf": documentos,
    }

def _estado_accionable(fecha_apertura: str | None, fecha_cierre: str | None, estado_plazo: str | None) -> dict[str, Any]:
    """Determina si el ADC admite actuación del usuario en la fecha de consulta."""
    hoy = date.today()
    def convertir(valor: str | None) -> date | None:
        if not valor:
            return None
        try:
            return datetime.strptime(valor, "%d-%m-%Y").date()
        except ValueError:
            return None

    apertura = convertir(fecha_apertura)
    cierre = convertir(fecha_cierre)
    if estado_plazo == "CERRADO" or (cierre is not None and cierre < hoy):
        return {"accionable": False, "motivo_accionabilidad": "PLAZO_CERRADO"}
    if apertura is not None and apertura > hoy:
        return {"accionable": False, "motivo_accionabilidad": "PLAZO_PENDIENTE"}
    if estado_plazo == "ABIERTO" or (apertura is not None and cierre is not None and apertura <= hoy <= cierre):
        return {"accionable": True, "motivo_accionabilidad": "PLAZO_ABIERTO"}
    return {"accionable": False, "motivo_accionabilidad": "PLAZO_NO_ACREDITADO"}


def _clasificar(client, id_emp: int, url: str, html: str) -> dict[str, Any]:
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

    plazo = _plazo_adc(soup, texto)
    apertura = _fecha(texto, "Apertura plazo") or plazo["apertura"]
    cierre = _fecha(texto, "Cierre plazo") or plazo["cierre"]
    bolsas_ficha = _bolsas_explicitas(denominacion)
    etapas = _datos_etapas(soup, texto)
    bolsas_pdf = _bolsas_documento_pdf(client, etapas["documentos_pdf"])
    bolsas = sorted(set(bolsas_ficha) | set(bolsas_pdf))
    fecha_apertura = proceso.get("fecha_apertura") or apertura
    fecha_cierre = proceso.get("fecha_cierre") or cierre
    accion = _estado_accionable(fecha_apertura, fecha_cierre, etapas["estado_plazo"])

    return {
        "id_emp": id_emp,
        "identificador_estable": f"GVA:ADC:{id_emp}",
        "numero_adc": _numero_adc(denominacion),
        "denominacion": denominacion,
        "cuerpo_escala": codigos[0] if len(codigos) == 1 else None,
        "grupo": proceso.get("grupo"),
        "plazas": proceso.get("plazas") or _plazas(texto),
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "accionable": accion["accionable"],
        "motivo_accionabilidad": accion["motivo_accionabilidad"],
        "etapa_actual_gva": etapas["etapa_actual"],
        "estado_plazo": etapas["estado_plazo"],
        "fechas_publicacion": etapas["fechas_publicacion"],
        "documentos_pdf": etapas["documentos_pdf"],
        "url": url,
        "bolsas_relacionadas": bolsas,
        "evidencia_relacion": ("PDF_OFICIAL" if bolsas_pdf else ("TEXTO_FICHA" if bolsas_ficha else None)),
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
                item = _clasificar(client, id_emp, url, respuesta.text)
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


def _resolver_identidad_gva_adc(cursor) -> tuple[int, int]:
    organismo = resolver_organismo(
        cursor,
        tipo="ADMINISTRACION_AUTONOMICA",
        provincia=None,
        nombre="Generalitat Valenciana",
    )
    if organismo is None:
        raise RuntimeError("Persistencia ADC bloqueada: Generalitat Valenciana no localizada")
    fuente = resolver_fuente(
        cursor,
        nombre="Diari Oficial de la Generalitat Valenciana",
        tipo="DOGV",
        organismo_id=organismo["id"],
    )
    return int(organismo["id"]), int(fuente["id"])


def planificar_adc_gva() -> dict[str, Any]:
    """Planifica altas/actualizaciones ADC sin escribir en BD."""
    inventario = inventariar_adc_gva()
    validos = inventario["adc_validos"]
    identificadores = [x["identificador_estable"] for x in validos]
    ids_emp = [int(x["id_emp"]) for x in validos]
    existentes_por_identificador: dict[str, dict[str, Any]] = {}
    existentes_por_id_emp: dict[int, dict[str, Any]] = {}

    if identificadores:
        with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, identificador_estable, denominacion, cuerpo_escala, grupo, plazas, fecha_apertura, fecha_cierre, tipo_proceso, es_oportunidad, datos_json
                FROM procesos
                WHERE identificador_estable = ANY(%s)
                   OR CASE
                        WHEN COALESCE(datos_json->>'id_emp','') ~ '^[0-9]+$'
                        THEN (datos_json->>'id_emp')::bigint = ANY(%s)
                        ELSE FALSE
                      END
                   OR CASE
                        WHEN identificador_estable ~ '^GVA:[0-9]+$'
                        THEN substring(identificador_estable from 5)::bigint = ANY(%s)
                        ELSE FALSE
                      END
                """,
                (identificadores, ids_emp, ids_emp),
            )
            for fila in cursor.fetchall():
                item = dict(fila)
                existentes_por_identificador[str(item["identificador_estable"])] = item
                datos = item.get("datos_json") or {}
                candidatos_id = [datos.get("id_emp")]
                m = re.fullmatch(r"GVA:(\d+)", str(item.get("identificador_estable") or ""))
                if m:
                    candidatos_id.append(m.group(1))
                for valor in candidatos_id:
                    try:
                        numero = int(valor)
                    except (TypeError, ValueError):
                        continue
                    if numero not in ids_emp:
                        continue
                    previo = existentes_por_id_emp.get(numero)
                    if previo is not None and int(previo["id"]) != int(item["id"]):
                        raise RuntimeError(
                            f"ADC {numero}: más de un proceso existente coincide con el mismo id_emp"
                        )
                    existentes_por_id_emp[numero] = item

    acciones = []
    for adc in validos:
        id_emp = int(adc["id_emp"])
        existente = (
            existentes_por_identificador.get(adc["identificador_estable"])
            or existentes_por_id_emp.get(id_emp)
        )
        accion = "NUEVA"
        baseline_adc = False
        if existente:
            datos_previos = existente.get("datos_json") or {}
            baseline_adc = datos_previos.get("categoria_gva") != "ADC"
            huella_previa = datos_previos.get("huella_novedad_adc")
            huella_actual = _huella_novedad_adc(adc)
            campos_cambian = any((
                existente.get("denominacion") != adc["denominacion"],
                existente.get("cuerpo_escala") != adc["cuerpo_escala"],
                existente.get("grupo") != adc["grupo"],
                existente.get("plazas") != adc["plazas"],
                adc["fecha_apertura"] is not None and existente.get("fecha_apertura") != adc["fecha_apertura"],
                adc["fecha_cierre"] is not None and existente.get("fecha_cierre") != adc["fecha_cierre"],
                datos_previos.get("estado_plazo") != adc["estado_plazo"],
                datos_previos.get("accionable") != adc["accionable"],
                datos_previos.get("bolsas_relacionadas") != adc["bolsas_relacionadas"],
                datos_previos.get("evidencia_relacion") != adc["evidencia_relacion"],
                huella_previa != huella_actual,
            ))
            accion = "ACTUALIZAR" if campos_cambian else "SIN_CAMBIOS"
        acciones.append({
            "accion": accion,
            "baseline_adc": baseline_adc,
            "proceso_id": int(existente["id"]) if existente else None,
            "identificador_estable": adc["identificador_estable"],
            "identificador_existente": existente["identificador_estable"] if existente else None,
            "numero_adc": adc["numero_adc"],
            "denominacion": adc["denominacion"],
            "cuerpo_escala": adc["cuerpo_escala"],
            "plazas": adc["plazas"],
            "fecha_apertura": adc["fecha_apertura"],
            "fecha_cierre": adc["fecha_cierre"],
            "accionable": adc["accionable"],
            "motivo_accionabilidad": adc["motivo_accionabilidad"],
            "bolsas_relacionadas": adc["bolsas_relacionadas"],
            "registro": adc,
        })

    return {
        "modo": "SOLO_REVISION",
        "escrituras_bd": False,
        "notificaciones": False,
        "resumen": {
            "validos": len(validos),
            "nuevos": sum(x["accion"] == "NUEVA" for x in acciones),
            "actualizar": sum(x["accion"] == "ACTUALIZAR" for x in acciones),
            "sin_cambios": sum(x["accion"] == "SIN_CAMBIOS" for x in acciones),
            "accionables": sum(bool(x["accionable"]) for x in acciones),
        },
        "acciones": acciones,
        "excluidos": inventario["excluidos"],
    }


def _normalizar_ref_bolsa(ref: str) -> str:
    return str(ref or "").upper().replace(" ", "").strip()


def _resolver_bolsas_relacionadas(cursor, referencias: list[str]) -> dict[str, Any]:
    """Resuelve solo referencias documentales inequívocas contra bolsas GVA existentes."""
    resueltas: dict[str, int] = {}
    ambiguas: dict[str, list[int]] = {}
    no_resueltas: list[str] = []

    for referencia in referencias:
        ref = _normalizar_ref_bolsa(referencia)
        if not ref:
            continue
        # Las referencias históricas tipo 500/22 no se convierten en un número
        # de bolsa administrativa: se conservan como evidencia, pero no se enlazan.
        if "/" in ref:
            no_resueltas.append(ref)
            continue
        m = re.fullmatch(r"(\d{2,4})(?:-?([BL]))?", ref)
        if not m:
            no_resueltas.append(ref)
            continue
        numero, sufijo = m.groups()
        patron = rf"(^|[^0-9]){re.escape(numero)}(?:-?{sufijo})?([^0-9]|$)" if sufijo else rf"(^|[^0-9]){re.escape(numero)}(?:-?[BL])?([^0-9]|$)"
        cursor.execute(
            """
            SELECT id, denominacion
            FROM procesos
            WHERE es_oportunidad=TRUE
              AND ambito_administrativo='SI'
              AND tipo_proceso='Bolsa de trabajo'
              AND denominacion ~* %s
            ORDER BY id
            """,
            (patron,),
        )
        candidatos = list(cursor.fetchall())
        if len(candidatos) == 1:
            resueltas[ref] = int(candidatos[0]["id"])
        elif len(candidatos) > 1:
            ambiguas[ref] = [int(x["id"]) for x in candidatos]
        else:
            no_resueltas.append(ref)

    return {
        "resueltas": resueltas,
        "ambiguas": ambiguas,
        "no_resueltas": sorted(set(no_resueltas)),
    }


def _huella_novedad_adc(adc: dict[str, Any]) -> str:
    """Identifica de forma estable la etapa/documento oficial actualmente visible."""
    fechas = adc.get("fechas_publicacion") or []
    documentos = adc.get("documentos_pdf") or []
    fecha = fechas[0] if fechas else ""
    documento = documentos[0].get("url", "") if documentos else ""
    etapa = adc.get("etapa_actual_gva") or ""
    return f"{fecha}|{etapa}|{documento}"


def _publicacion_adc_en_bolsa(
    cursor,
    *,
    bolsa_id: int,
    fuente_id: int,
    adc: dict[str, Any],
    huella: str,
) -> bool:
    """Publica una etapa ADC nueva en una bolsa con relación documental explícita."""
    import hashlib

    digest = hashlib.sha256(huella.encode("utf-8")).hexdigest()[:16]
    referencia = f"ADC:{adc['id_emp']}:{digest}"
    cursor.execute(
        """
        SELECT id
        FROM publicaciones
        WHERE proceso_id=%s AND fuente_id=%s AND referencia=%s
        LIMIT 1
        """,
        (bolsa_id, fuente_id, referencia),
    )
    if cursor.fetchone() is not None:
        return False
    titulo = (
        f"ADC {adc.get('numero_adc') or adc['id_emp']}: "
        f"{adc.get('etapa_actual_gva') or 'novedad relacionada'}"
    )
    fechas = adc.get("fechas_publicacion") or []
    fecha_publicacion = fechas[0] if fechas else None
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,
            datos_json,detectada_at
        ) VALUES (%s,%s,%s,'ADC_RELACIONADO',%s,%s,%s,%s,NOW())
        """,
        (
            bolsa_id,
            fuente_id,
            referencia,
            titulo,
            fecha_publicacion,
            adc["url"],
            Jsonb({
                "adc_id_emp": adc["id_emp"],
                "numero_adc": adc.get("numero_adc"),
                "etapa_actual_gva": adc.get("etapa_actual_gva"),
                "evidencia_relacion": adc.get("evidencia_relacion"),
                "bolsas_relacionadas": adc.get("bolsas_relacionadas") or [],
                "huella_novedad": huella,
            }),
        ),
    )
    return True


def _publicacion_etapa_adc(
    cursor,
    *,
    proceso_id: int,
    fuente_id: int,
    adc: dict[str, Any],
    huella: str,
) -> bool:
    """Registra una etapa nueva del propio ADC para su seguimiento."""
    import hashlib

    digest = hashlib.sha256(huella.encode("utf-8")).hexdigest()[:16]
    referencia = f"ADC_ETAPA:{adc['id_emp']}:{digest}"
    cursor.execute(
        """
        SELECT id
        FROM publicaciones
        WHERE proceso_id=%s AND fuente_id=%s AND referencia=%s
        LIMIT 1
        """,
        (proceso_id, fuente_id, referencia),
    )
    if cursor.fetchone() is not None:
        return False
    fechas = adc.get("fechas_publicacion") or []
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,
            datos_json,detectada_at
        ) VALUES (%s,%s,%s,'ADC_ETAPA',%s,%s,%s,%s,NOW())
        """,
        (
            proceso_id,
            fuente_id,
            referencia,
            f"ADC {adc.get('numero_adc') or adc['id_emp']}: "
            f"{adc.get('etapa_actual_gva') or 'novedad'}",
            fechas[0] if fechas else None,
            adc["url"],
            Jsonb({
                "adc_id_emp": adc["id_emp"],
                "numero_adc": adc.get("numero_adc"),
                "etapa_actual_gva": adc.get("etapa_actual_gva"),
                "huella_novedad": huella,
            }),
        ),
    )
    return True


def persistir_adc_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Persiste ADC como oportunidades; no genera notificaciones."""
    plan = planificar_adc_gva()
    if not aplicar:
        return plan

    insertados = 0
    actualizados = 0
    publicaciones_bolsas = 0
    publicaciones_adc = 0
    relaciones_ambiguas: dict[str, list[int]] = {}
    relaciones_no_resueltas: set[str] = set()
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo_id, fuente_id = _resolver_identidad_gva_adc(cursor)
        for accion in plan["acciones"]:
            adc = accion["registro"]
            datos = {
                "id_emp": adc["id_emp"],
                "numero_adc": adc["numero_adc"],
                "url_detalle": adc["url"],
                "categoria_gva": "ADC",
                "etapa_actual_gva": adc["etapa_actual_gva"],
                "estado_plazo": adc["estado_plazo"],
                "accionable": adc["accionable"],
                "motivo_accionabilidad": adc["motivo_accionabilidad"],
                "bolsas_relacionadas": adc["bolsas_relacionadas"],
                "evidencia_relacion": adc["evidencia_relacion"],
                "documentos_pdf": adc["documentos_pdf"],
                "fechas_publicacion": adc["fechas_publicacion"],
                "huella_novedad_adc": _huella_novedad_adc(adc),
            }
            if accion["accion"] == "SIN_CAMBIOS":
                continue
            if accion["accion"] == "NUEVA":
                cursor.execute(
                    """
                    INSERT INTO procesos (
                        organismo_id,codigo_externo,identificador_estable,denominacion,
                        cuerpo_escala,grupo,tipo_proceso,plazas,estado,
                        fecha_apertura,fecha_cierre,fuente_principal_id,datos_json,
                        es_oportunidad,origen_dato,revision_estado,ambito_administrativo,
                        created_at,updated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,'Anuncio difícil cobertura (ADC)',%s,'EN_CURSO',
                        %s,%s,%s,%s,TRUE,'AUTOMATICO','PUBLICADA','SI',NOW(),NOW()
                    )
                    ON CONFLICT (identificador_estable) DO NOTHING
                    """,
                    (
                        organismo_id,str(adc["id_emp"]),adc["identificador_estable"],
                        adc["denominacion"],adc["cuerpo_escala"],adc["grupo"],adc["plazas"],
                        adc["fecha_apertura"],adc["fecha_cierre"],fuente_id,Jsonb(datos),
                    ),
                )
                insertados += cursor.rowcount
            else:
                cursor.execute(
                    """
                    UPDATE procesos
                    SET denominacion=%s,
                        cuerpo_escala=%s,
                        grupo=%s,
                        tipo_proceso='Anuncio difícil cobertura (ADC)',
                        plazas=%s,
                        estado='EN_CURSO',
                        es_oportunidad=TRUE,
                        ambito_administrativo='SI',
                        fecha_apertura=COALESCE(%s,fecha_apertura),
                        fecha_cierre=COALESCE(%s,fecha_cierre),
                        datos_json=COALESCE(datos_json,'{}'::jsonb) || %s,
                        updated_at=NOW()
                    WHERE id=%s AND identificador_estable=%s
                    """,
                    (
                        adc["denominacion"],adc["cuerpo_escala"],adc["grupo"],adc["plazas"],
                        adc["fecha_apertura"],adc["fecha_cierre"],Jsonb(datos),
                        accion["proceso_id"],accion["identificador_existente"],
                    ),
                )
                actualizados += cursor.rowcount

            # Una etapa posterior de un ADC ya normalizado es una novedad del
            # propio proceso. El baseline (incluido legacy) permanece silencioso.
            if accion["accion"] == "ACTUALIZAR" and not accion.get("baseline_adc", False):
                if _publicacion_etapa_adc(
                    cursor,
                    proceso_id=int(accion["proceso_id"]),
                    fuente_id=fuente_id,
                    adc=adc,
                    huella=_huella_novedad_adc(adc),
                ):
                    publicaciones_adc += 1

            relaciones = _resolver_bolsas_relacionadas(cursor, adc["bolsas_relacionadas"])
            relaciones_ambiguas.update(relaciones["ambiguas"])
            relaciones_no_resueltas.update(relaciones["no_resueltas"])

            # Baseline histórico silencioso: una ADC que entra por primera vez y
            # ya no es accionable se registra, pero no genera novedades en bolsas.
            publicar_en_bolsas = (
                not accion.get("baseline_adc", False)
                and (accion["accion"] == "ACTUALIZAR" or bool(adc["accionable"]))
            )
            if publicar_en_bolsas:
                huella = _huella_novedad_adc(adc)
                for bolsa_id in relaciones["resueltas"].values():
                    if _publicacion_adc_en_bolsa(
                        cursor,
                        bolsa_id=bolsa_id,
                        fuente_id=fuente_id,
                        adc=adc,
                        huella=huella,
                    ):
                        publicaciones_bolsas += 1
        connection.commit()
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "notificaciones": False,
        "insertados": insertados,
        "actualizados": actualizados,
        "publicaciones_adc": publicaciones_adc,
        "publicaciones_bolsas": publicaciones_bolsas,
        "relaciones_ambiguas": relaciones_ambiguas,
        "relaciones_no_resueltas": sorted(relaciones_no_resueltas),
    }
