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
    """Extrae el plazo de la etapa inicial sin confundir publicaciones posteriores."""
    texto_lineas = "\n".join(soup.stripped_strings)
    patrones = (
        r"(?:Plazo|Termini)(?: de presentaci[oó]n| de presentacio| de la etapa actual)?\s*:?[\s\n]*(?:Desde|Des de)?\s*(?:el\s+)?(\d{2}[-/]\d{2}[-/]\d{4})\s*(?:hasta|fins(?:\s+al)?|a)\s*(?:el\s+)?(\d{2}[-/]\d{2}[-/]\d{4})",
        r"(?:Desde|Des de)\s*(?:el\s+)?(\d{2}[-/]\d{2}[-/]\d{4})\s*(?:hasta|fins(?:\s+al)?|a)\s*(?:el\s+)?(\d{2}[-/]\d{2}[-/]\d{4})",
    )
    for patron in patrones:
        m = re.search(patron, texto_lineas, re.I)
        if m:
            return {"apertura": m.group(1).replace("/", "-"), "cierre": m.group(2).replace("/", "-")}
    return {"apertura": None, "cierre": None}

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
    patron_lista = re.compile(r"\b(?:bolsas|borses)\s*[:.]?\s*([^\n]{1,240})", re.I)
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
                SELECT id, identificador_estable, tipo_proceso, es_oportunidad, datos_json
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
        acciones.append({
            "accion": "ACTUALIZAR" if existente else "NUEVA",
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
            "accionables": sum(bool(x["accionable"]) for x in acciones),
        },
        "acciones": acciones,
        "excluidos": inventario["excluidos"],
    }

def persistir_adc_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Persiste ADC como oportunidades; no genera notificaciones."""
    plan = planificar_adc_gva()
    if not aplicar:
        return plan

    insertados = 0
    actualizados = 0
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
            }
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
                    SET plazas=%s,
                        fecha_apertura=COALESCE(%s,fecha_apertura),
                        fecha_cierre=COALESCE(%s,fecha_cierre),
                        datos_json=COALESCE(datos_json,'{}'::jsonb) || %s,
                        updated_at=NOW()
                    WHERE id=%s AND identificador_estable=%s
                    """,
                    (
                        adc["plazas"],adc["fecha_apertura"],adc["fecha_cierre"],Jsonb(datos),
                        accion["proceso_id"],accion["identificador_existente"],
                    ),
                )
                actualizados += cursor.rowcount
        connection.commit()
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "notificaciones": False,
        "insertados": insertados,
        "actualizados": actualizados,
    }

                        THEN (datos_json->>'id_emp')::bigint = ANY(%s)
                        ELSE FALSE
                      END
                   OR CASE
                        WHEN identificador_estable ~ '^GVA:[0-9]+        acciones.append({
            "accion": "ACTUALIZAR" if existente else "NUEVA",
            "proceso_id": int(existente["id"]) if existente else None,
            "identificador_estable": adc["identificador_estable"],
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
            "accionables": sum(bool(x["accionable"]) for x in acciones),
        },
        "acciones": acciones,
        "excluidos": inventario["excluidos"],
    }


def persistir_adc_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Persiste ADC como oportunidades; no genera notificaciones."""
    plan = planificar_adc_gva()
    if not aplicar:
        return plan

    insertados = 0
    actualizados = 0
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
            }
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
                    SET plazas=%s,
                        fecha_apertura=COALESCE(%s,fecha_apertura),
                        fecha_cierre=COALESCE(%s,fecha_cierre),
                        datos_json=COALESCE(datos_json,'{}'::jsonb) || %s,
                        updated_at=NOW()
                    WHERE id=%s AND identificador_estable=%s
                    """,
                    (
                        adc["plazas"],adc["fecha_apertura"],adc["fecha_cierre"],Jsonb(datos),
                        accion["proceso_id"],adc["identificador_estable"],
                    ),
                )
                actualizados += cursor.rowcount
        connection.commit()
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "notificaciones": False,
        "insertados": insertados,
        "actualizados": actualizados,
    }

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
                    if numero in ids_emp:
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
        acciones.append({
            "accion": "ACTUALIZAR" if existente else "NUEVA",
            "proceso_id": int(existente["id"]) if existente else None,
            "identificador_estable": adc["identificador_estable"],
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
            "accionables": sum(bool(x["accionable"]) for x in acciones),
        },
        "acciones": acciones,
        "excluidos": inventario["excluidos"],
    }


def persistir_adc_gva(*, aplicar: bool = False) -> dict[str, Any]:
    """Persiste ADC como oportunidades; no genera notificaciones."""
    plan = planificar_adc_gva()
    if not aplicar:
        return plan

    insertados = 0
    actualizados = 0
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
            }
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
                    SET plazas=%s,
                        fecha_apertura=COALESCE(%s,fecha_apertura),
                        fecha_cierre=COALESCE(%s,fecha_cierre),
                        datos_json=COALESCE(datos_json,'{}'::jsonb) || %s,
                        updated_at=NOW()
                    WHERE id=%s AND identificador_estable=%s
                    """,
                    (
                        adc["plazas"],adc["fecha_apertura"],adc["fecha_cierre"],Jsonb(datos),
                        accion["proceso_id"],adc["identificador_estable"],
                    ),
                )
                actualizados += cursor.rowcount
        connection.commit()
    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "notificaciones": False,
        "insertados": insertados,
        "actualizados": actualizados,
    }
