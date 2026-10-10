from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .festivos import hoy_es
from .ambito_administrativo import clasificar_ambito_administrativo
from .bop_valencia_municipios import _clasificar_anuncio, _sin
from .bop_alicante import seleccionar_proceso_seguimiento
from .database import get_connection
from .bop_valencia import _grupo_subgrupo, _obtener_texto
from .organismos import resolver_fuente, resolver_organismo
from .boe_local_import import recuperar_boe_para_proceso_bop
from .estado_proceso import clasificar_evento_terminal



_NUMEROS_PLAZAS = {
    "UNA": 1, "UN": 1, "DOS": 2, "TRES": 3, "CUATRO": 4, "CINCO": 5,
    "SEIS": 6, "SIETE": 7, "OCHO": 8, "NUEVE": 9, "DIEZ": 10,
    "ONCE": 11, "DOCE": 12, "TRECE": 13, "CATORCE": 14, "QUINCE": 15,
    "DIECISEIS": 16, "DIECISIETE": 17, "DIECIOCHO": 18, "DIECINUEVE": 19,
    "VEINTE": 20,
}


def _extraer_plazas_titulo(titulo: str | None) -> int | None:
    """Extrae el número de plazas cuando el título del BOP lo declara explícitamente."""
    import re
    import unicodedata

    normal = unicodedata.normalize("NFKD", titulo or "").encode("ascii", "ignore").decode().upper()
    m = re.search(r"\b(\d+)\s+PLAZAS?\b", normal)
    if m:
        return int(m.group(1))
    palabras = "|".join(_NUMEROS_PLAZAS)
    m = re.search(rf"\b({palabras})\s+PLAZAS?\b", normal)
    return _NUMEROS_PLAZAS.get(m.group(1)) if m else None


PORTAL = "https://bop.dipcas.es/PortalBOP/"
DESCARGA = "https://bop.dipcas.es/PortalBOP/api/descargarAnuncio"


def _texto(elemento) -> str:
    return " ".join(elemento.get_text(" ", strip=True).split())


def _extraer_sumario(html: str) -> dict[str, Any]:
    """Extrae el sumario visible del BOP; no persiste ni consulta BD."""
    soup = BeautifulSoup(html, "html.parser")
    texto = _texto(soup)
    numero = None
    fecha = None
    import re
    m = re.search(r"Sumario\s+BOP.*?(\d+)\s*\|?\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
    if m:
        numero, fecha = m.group(1), m.group(2)

    anuncios: list[dict[str, Any]] = []
    organismo = ""
    for nodo in soup.find_all(["a", "div", "span", "td", "li"]):
        t = _texto(nodo)
        if not t:
            continue
        enlace = nodo if nodo.name == "a" else nodo.find("a", href=True)
        href = enlace.get("href") if enlace else None
        if href and "descargarAnuncio" in href and "idAnuncio=" in href:
            import re
            mid = re.search(r"idAnuncio=(\d+)", href)
            if not mid:
                continue
            # El enlace solo contiene "ui-button". En PrimeFaces aparecen
            # scripts auxiliares entre el botón y el texto visible del anuncio.
            titulo = ""
            siguiente = enlace.find_next()
            while siguiente is not None:
                if siguiente.name == "a" and siguiente.get("href") and "descargarAnuncio" in siguiente.get("href"):
                    break
                if siguiente.name in {"script", "style"}:
                    siguiente = siguiente.find_next()
                    continue
                candidato = _texto(siguiente)
                if (
                    candidato
                    and candidato != "ui-button"
                    and "PrimeFaces.cw(" not in candidato
                    and not candidato.startswith("$(function()")
                ):
                    titulo = candidato
                    break
                siguiente = siguiente.find_next()
            if not titulo:
                continue
            # El organismo válido es la cabecera estructural más próxima
            # al enlace dentro de su cadena de contenedores. Un div exterior
            # puede contener un titulo2 (Diputación) y varios titulo3
            # municipales, por lo que no debemos tomar la primera cabecera
            # de un ancestro amplio.
            organismo_anuncio = organismo
            if not organismo_anuncio:
                for contenedor in enlace.find_parents("div"):
                    cabeceras = contenedor.find_all(
                        "span",
                        class_=lambda clases: clases
                        and ("titulo2" in clases.split() or "titulo3" in clases.split()),
                        recursive=False,
                    )
                    if cabeceras:
                        organismo_anuncio = _texto(cabeceras[0])
                        break

            anuncios.append({
                "id_anuncio": mid.group(1),
                "referencia": f"BOPCS:{mid.group(1)}",
                "numero_bop": numero,
                "fecha_publicacion": fecha,
                "organismo": organismo_anuncio,
                "titulo": titulo,
                "url_documento": str(httpx.URL(DESCARGA).copy_add_param("idAnuncio", mid.group(1)).copy_add_param("idioma", "es")),
            })
            continue

        normal = t.upper()
        if (
            normal.startswith("AYUNTAMIENTO ")
            or normal.startswith("AJUNTAMENT ")
            or normal.startswith("DIPUTACIÓ PROVINCIAL")
            or normal.startswith("DIPUTACIÓN PROVINCIAL")
        ) and len(t) < 180:
            organismo = t

    # El DOM puede repetir nodos contenedores: identidad documental por idAnuncio.
    unicos: dict[str, dict[str, Any]] = {}
    for anuncio in anuncios:
        unicos.setdefault(anuncio["id_anuncio"], anuncio)
    return {"numero_bop": numero, "fecha_publicacion": fecha, "anuncios": list(unicos.values())}



def _boletines_disponibles(html: str) -> list[dict[str, Any]]:
    """Extrae fecha, número y componente JSF de «Boletines anteriores»."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }
    encontrados = {}
    patron = re.compile(r"N[º°]\s*(\d+).*?(\d{2})\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+(\d{4})")
    for nodo in soup.find_all(id=True):
        m = patron.search(_texto(nodo))
        if not m:
            continue
        mes = meses.get(m.group(3).lower())
        if not mes:
            continue
        item = {
            "numero": m.group(1),
            "fecha": date(int(m.group(4)), mes, int(m.group(2))),
            "source": nodo.get("id"),
        }
        clave = (item["numero"], item["fecha"])
        previo = encontrados.get(clave)
        if previo is None or item["source"].count(":") > previo["source"].count(":"):
            encontrados[clave] = item
    return sorted(encontrados.values(), key=lambda x: x["fecha"], reverse=True)


def _view_state(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    nodo = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    return nodo.get("value") if nodo else None


def _cargar_boletin_anterior(client: httpx.Client, html_inicial: str, source: str) -> str:
    """Reproduce la acción JSF/PrimeFaces observada en el navegador."""
    import xml.etree.ElementTree as ET
    view_state = _view_state(html_inicial)
    if not view_state:
        raise RuntimeError("El portal no expone javax.faces.ViewState")
    formulario = "busquedaBoletinesForm"
    datos = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": formulario,
        source: source,
        formulario: formulario,
        "javax.faces.ViewState": view_state,
    }
    respuesta = client.post(
        PORTAL,
        data=datos,
        headers={
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PORTAL,
        },
    )
    respuesta.raise_for_status()
    raiz = ET.fromstring(respuesta.text)
    for update in raiz.findall(".//update"):
        if update.get("id") == formulario and update.text:
            return update.text
    raise RuntimeError("La respuesta JSF no contiene la actualización del formulario")


def _clasificar_anuncio_castellon(titulo: str) -> str:
    """Clasificación conservadora adaptada a las fórmulas observadas en BOP Castellón."""
    clase = _clasificar_anuncio(titulo)
    if clase != "SEGUIMIENTO":
        return clase

    n = _sin(titulo)
    # No promover a convocatoria inicial anuncios que describen hitos,
    # impugnaciones o correcciones de unas bases ya publicadas.
    bloqueos = (
        "recurso", "rectificacion", "correccion", "lista provisional",
        "lista definitiva", "listado provisional", "listado definitivo",
        "admitidos", "admitidas", "excluidos", "excluidas", "tribunal",
        "nombramiento", "emplazamiento", "alegacion", "abstencion",
    )
    if any(x in n for x in bloqueos):
        return clase

    nuevas_castellon = (
        "bases rectoras especificas para la provision en propiedad",
        "aprobacion bases provision en propiedad",
        "bases concurso oposicion libre",
    )
    return "NUEVA_CONVOCATORIA" if any(x in n for x in nuevas_castellon) else clase


def consultar_bop_castellon(*, desde: date | None = None, hasta: date | None = None) -> dict[str, Any]:
    """Fase 4: SOLO_REVISION sobre una ventana real de boletines oficiales."""
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Castellón",
        "hasta": (hasta or hoy_es()).isoformat(),
        "fecha_boletin": None,
        "numero_bop": None,
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "resumen_clases": {},
        "errores": [],
        "detalle": [],
        "muestra_extraida": [],
        "sin_organismo": [],
    }
    limite_hasta = hasta or hoy_es()
    limite_desde = desde or limite_hasta
    resultado["desde"] = limite_desde.isoformat()
    resultado["boletines_revisados"] = 0
    if limite_desde > limite_hasta:
        resultado["errores"].append("La fecha desde no puede ser posterior a hasta")
        return resultado
    try:
        with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "TuCoach-Empleo/1.0"}) as client:
            respuesta = client.get(PORTAL)
            respuesta.raise_for_status()
            html_inicial = respuesta.text
            actual = _extraer_sumario(html_inicial)
            sumarios = []
            if actual["fecha_publicacion"]:
                fecha_actual = datetime.strptime(actual["fecha_publicacion"], "%d/%m/%Y").date()
                if limite_desde <= fecha_actual <= limite_hasta:
                    sumarios.append(actual)
            for boletin in _boletines_disponibles(html_inicial):
                if not (limite_desde <= boletin["fecha"] <= limite_hasta):
                    continue
                html_boletin = _cargar_boletin_anterior(client, html_inicial, boletin["source"])
                sumario = _extraer_sumario(html_boletin)
                fecha_esperada = boletin["fecha"].strftime("%d/%m/%Y")
                if sumario["fecha_publicacion"] != fecha_esperada:
                    raise RuntimeError(f"El portal devolvió {sumario['fecha_publicacion']!r} para BOP {boletin['numero']} ({fecha_esperada})")
                sumarios.append(sumario)
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado
    anuncios_unicos = {}
    for sumario in sumarios:
        for anuncio in sumario["anuncios"]:
            anuncios_unicos.setdefault(anuncio["id_anuncio"], anuncio)
    anuncios = list(anuncios_unicos.values())
    resultado["boletines_revisados"] = len(sumarios)
    if sumarios:
        resultado["fecha_boletin"] = sumarios[0]["fecha_publicacion"]
        resultado["numero_bop"] = sumarios[0]["numero_bop"]
    resultado["muestra_extraida"] = [{"organismo": x["organismo"], "titulo": x["titulo"], "referencia": x["referencia"]} for x in anuncios[:20]]
    resultado["sin_organismo"] = [{"titulo": x["titulo"], "referencia": x["referencia"], "numero_bop": x["numero_bop"], "fecha_publicacion": x["fecha_publicacion"]} for x in anuncios if not x["organismo"]]
    candidatos = []
    for anuncio in anuncios:
        ambito = clasificar_ambito_administrativo({"denominacion": anuncio["titulo"], "cuerpo_escala": None, "grupo": None})
        if ambito != "SI":
            continue
        item = dict(anuncio)
        item["ambito_administrativo"] = ambito
        item["clase"] = _clasificar_anuncio_castellon(item["titulo"])
        item["plazas"] = _extraer_plazas_titulo(item["titulo"])
        candidatos.append(item)
    from collections import Counter
    conteo = Counter(x["clase"] for x in candidatos)
    resultado["descubiertos"] = len(anuncios)
    resultado["administrativos"] = len(candidatos)
    resultado["revision"] = conteo.get("REVISION", 0)
    resultado["resumen_clases"] = dict(sorted(conteo.items()))
    resultado["detalle"] = candidatos
    return resultado



def _grupo_subgrupo_documento(url: str | None) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    try:
        with httpx.Client(timeout=45, follow_redirects=True) as client:
            return _grupo_subgrupo(_obtener_texto(client, url))
    except Exception:
        return None, None


def _fecha_bop(valor: str | None) -> date | None:
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return (
                date.fromisoformat(valor)
                if formato == "%Y-%m-%d"
                else datetime.strptime(valor, formato).date()
            )
        except ValueError:
            pass
    return None


def _identidad_organismo(organismo: str | None) -> dict[str, str | None]:
    """Normaliza tipo, provincia y municipio de la cabecera sin consultar BD."""
    import re

    texto = " ".join((organismo or "").split()).strip()
    normal = texto.upper()
    if normal.startswith("DIPUTACIÓ PROVINCIAL") or normal.startswith("DIPUTACIÓN PROVINCIAL"):
        return {"tipo": "DIPUTACION", "provincia": "Castellón", "municipio": None}

    patrones = (
        r"^AJUNTAMENT\s+D['’](.+)$",
        r"^AJUNTAMENT\s+DE\s+(.+)$",
        r"^AYUNTAMIENTO\s+D['’](.+)$",
        r"^AYUNTAMIENTO\s+DE\s+(.+)$",
    )
    for patron in patrones:
        m = re.match(patron, texto, re.I)
        if m:
            municipio = m.group(1).strip()
            # El BOPCS puede publicar conjuntamente las formas valenciana/castellana.
            # Conservamos una única identidad municipal para resolver organismos.
            if "/" in municipio:
                municipio = municipio.split("/", 1)[0].strip()
            return {"tipo": "AYUNTAMIENTO", "provincia": "Castellón", "municipio": municipio}
    return {"tipo": "DESCONOCIDO", "provincia": "Castellón", "municipio": None}


def _filtrar_revision_por_tipo_organismo(revision: dict[str, Any], tipo_organismo: str | None) -> dict[str, Any]:
    """Limita una revisión de Castellón a Diputación o ayuntamientos sin alterar la fuente."""
    if tipo_organismo is None:
        return revision
    tipo = tipo_organismo.upper()
    if tipo not in {"DIPUTACION", "AYUNTAMIENTO"}:
        raise ValueError("tipo_organismo debe ser DIPUTACION o AYUNTAMIENTO")
    filtrada = dict(revision)
    filtrada["detalle"] = [
        hallazgo for hallazgo in revision.get("detalle", [])
        if _identidad_organismo(hallazgo.get("organismo")).get("tipo") == tipo
    ]
    filtrada["tipo_organismo"] = tipo
    return filtrada


def preparar_revision_castellon(
    *,
    desde: date | None = None,
    hasta: date | None = None,
    tipo_organismo: str | None = None,
) -> dict[str, Any]:
    """Expone el plan de identidad institucional sin consultar ni modificar BD."""
    revision = consultar_bop_castellon(desde=desde, hasta=hasta)
    revision = _filtrar_revision_por_tipo_organismo(revision, tipo_organismo)
    detalle = []
    for hallazgo in revision["detalle"]:
        if hallazgo["clase"] not in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA", "SEGUIMIENTO"):
            continue
        identidad = _identidad_organismo(hallazgo.get("organismo"))
        detalle.append({
            "referencia": hallazgo["referencia"],
            "clase": hallazgo["clase"],
            "titulo": hallazgo["titulo"],
            "organismo_fuente": hallazgo.get("organismo"),
            "organismo": identidad,
            "identidad_admitida": (
                hallazgo["clase"] in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA")
                and identidad["tipo"] in ("AYUNTAMIENTO", "DIPUTACION")
            ),
        })
    return {
        "modo": "SOLO_REVISION_IDENTIDAD",
        "fuente": revision["fuente"],
        "desde": revision["desde"],
        "hasta": revision["hasta"],
        "errores": list(revision["errores"]),
        "detalle": detalle,
    }


def preparar_importacion_bop_castellon(
    *,
    desde: date | None = None,
    hasta: date | None = None,
    tipo_organismo: str | None = None,
) -> dict[str, Any]:
    """Prepara la persistencia con lecturas de BD; nunca escribe."""
    revision = consultar_bop_castellon(desde=desde, hasta=hasta)
    revision = _filtrar_revision_por_tipo_organismo(revision, tipo_organismo)
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION_BD",
        "fuente": revision["fuente"],
        "desde": revision["desde"],
        "hasta": revision["hasta"],
        "nuevas": 0,
        "existentes": 0,
        "seguimientos_vinculados": 0,
        "seguimientos_revision": 0,
        "excluidos": 0,
        "errores": list(revision["errores"]),
        "detalle": [],
    }
    recuperaciones_boe: list[tuple[int, date]] = []
    if resultado["errores"]:
        return resultado

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        for hallazgo in revision["detalle"]:
            clase = hallazgo["clase"]
            if clase not in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA", "SEGUIMIENTO"):
                resultado["excluidos"] += 1
                continue

            if clase in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA"):
                cursor.execute(
                    "SELECT id FROM procesos WHERE identificador_estable=%s",
                    (hallazgo["referencia"],),
                )
                existente = cursor.fetchone()
                if existente:
                    resultado["existentes"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "EXISTENTE",
                        "proceso_id": existente["id"],
                    })
                else:
                    resultado["nuevas"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "NUEVO",
                        "organismo": _identidad_organismo(hallazgo.get("organismo")),
                    })
                continue

            fecha_publicacion = _fecha_bop(hallazgo.get("fecha_publicacion"))
            if fecha_publicacion is None:
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "vinculacion": "FECHA_INVALIDA",
                    "proceso_id": None,
                })
                continue

            identidad = _identidad_organismo(hallazgo.get("organismo"))
            if identidad["tipo"] not in ("AYUNTAMIENTO", "DIPUTACION"):
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "organismo": identidad,
                    "vinculacion": "TIPO_ORGANISMO_NO_IMPLEMENTADO",
                    "proceso_id": None,
                })
                continue

            municipio = identidad["municipio"]
            cursor.execute(
                """
                SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,o.municipio,p.tipo_proceso,o.tipo AS organismo_tipo
                FROM procesos p
                JOIN organismos o ON o.id=p.organismo_id
                WHERE o.tipo=%s
                  AND LOWER(COALESCE(o.provincia,'')) IN ('castellón','castellon')
                  AND p.ambito_administrativo='SI'
                  AND p.estado='EN_CURSO'
                  AND p.fecha_convocatoria IS NOT NULL
                  AND p.fecha_convocatoria <= %s
                ORDER BY p.fecha_convocatoria DESC,p.id DESC
                """,
                (identidad["tipo"], fecha_publicacion),
            )
            candidatos = list(cursor.fetchall())
            if identidad["tipo"] == "DIPUTACION":
                for candidato in candidatos:
                    candidato["municipio"] = "Diputación Provincial de Castellón"
            hallazgo_matching = dict(hallazgo)
            hallazgo_matching["denominacion"] = municipio or "Diputación Provincial de Castellón"
            hallazgo_matching["extracto"] = hallazgo.get("titulo") or ""
            proceso, motivo = seleccionar_proceso_seguimiento(
                hallazgo_matching,
                candidatos,
            )
            if proceso:
                resultado["seguimientos_vinculados"] += 1
            else:
                resultado["seguimientos_revision"] += 1
            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "municipio": municipio,
                "vinculacion": motivo,
                "proceso_id": proceso["id"] if proceso else None,
            })

        connection.rollback()
    return resultado


def importar_bop_castellon(
    *,
    desde: date | None = None,
    hasta: date | None = None,
    aplicar: bool = False,
    tipo_organismo: str | None = None,
) -> dict[str, Any]:
    """Importación idempotente del BOP Castellón; por defecto solo revisión."""
    if not aplicar:
        return preparar_importacion_bop_castellon(
            desde=desde,
            hasta=hasta,
            tipo_organismo=tipo_organismo,
        )

    revision = consultar_bop_castellon(desde=desde, hasta=hasta)
    revision = _filtrar_revision_por_tipo_organismo(revision, tipo_organismo)
    resultado: dict[str, Any] = {
        "modo": "APLICAR",
        "fuente": revision["fuente"],
        "desde": revision["desde"],
        "hasta": revision["hasta"],
        "nuevas": 0,
        "existentes": 0,
        "publicaciones": 0,
        "seguimientos_vinculados": 0,
        "seguimientos_revision": 0,
        "organismos_creados": 0,
        "excluidos": 0,
        "errores": list(revision["errores"]),
        "detalle": [],
    }
    if resultado["errores"]:
        return resultado

    # Nuevos procesos BOP que, una vez confirmados, deben intentar absorber su BOE.
    # Se declara también en la ruta APLICAR; la ruta de solo revisión mantiene
    # su propia colección independiente.
    recuperaciones_boe: list[tuple[int, date]] = []

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        # La fuente productiva debe existir previamente; nunca se crea implícitamente.
        fuente = resolver_fuente(
            cursor,
            nombre="Boletín Oficial de la Provincia de Castellón",
            tipo="BOP",
        )
        fuente_id = fuente["id"]

        for hallazgo in revision["detalle"]:
            clase = hallazgo["clase"]
            if clase not in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA", "SEGUIMIENTO"):
                resultado["excluidos"] += 1
                continue

            fecha_publicacion = _fecha_bop(hallazgo.get("fecha_publicacion"))
            if fecha_publicacion is None:
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "estado": "REVISION",
                    "motivo": "FECHA_INVALIDA",
                })
                continue

            identidad = _identidad_organismo(hallazgo.get("organismo"))

            if clase == "SEGUIMIENTO":
                if identidad["tipo"] not in ("AYUNTAMIENTO", "DIPUTACION"):
                    resultado["seguimientos_revision"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "REVISION",
                        "motivo": "TIPO_ORGANISMO_NO_IMPLEMENTADO",
                        "organismo": identidad,
                    })
                    continue

                cursor.execute(
                    """
                    SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,o.municipio,p.tipo_proceso,o.tipo AS organismo_tipo
                    FROM procesos p
                    JOIN organismos o ON o.id=p.organismo_id
                    WHERE o.tipo=%s
                      AND LOWER(COALESCE(o.provincia,'')) IN ('castellón','castellon')
                      AND p.ambito_administrativo='SI'
                      AND p.estado='EN_CURSO'
                      AND p.fecha_convocatoria IS NOT NULL
                      AND p.fecha_convocatoria <= %s
                    ORDER BY p.fecha_convocatoria DESC,p.id DESC
                    """,
                    (identidad["tipo"], fecha_publicacion),
                )
                candidatos = list(cursor.fetchall())
                if identidad["tipo"] == "DIPUTACION":
                    for candidato in candidatos:
                        candidato["municipio"] = "Diputación Provincial de Castellón"
                hallazgo_matching = dict(hallazgo)
                hallazgo_matching["denominacion"] = identidad["municipio"] or "Diputación Provincial de Castellón"
                hallazgo_matching["extracto"] = hallazgo.get("titulo") or ""
                proceso, motivo = seleccionar_proceso_seguimiento(
                    hallazgo_matching,
                    candidatos,
                )
                if not proceso:
                    resultado["seguimientos_revision"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "REVISION",
                        "motivo": motivo,
                    })
                    continue
                proceso_id = proceso["id"]
                estado_terminal = clasificar_evento_terminal(
                    proceso.get("tipo_proceso"),
                    hallazgo.get("titulo") or "",
                )
                if estado_terminal:
                    cursor.execute(
                        "UPDATE procesos SET estado=%s,updated_at=NOW() WHERE id=%s",
                        (estado_terminal, proceso_id),
                    )
                resultado["seguimientos_vinculados"] += 1

            else:
                if identidad["tipo"] not in ("AYUNTAMIENTO", "DIPUTACION"):
                    resultado["seguimientos_revision"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "REVISION",
                        "motivo": "ORGANISMO_NO_ADMITIDO",
                        "organismo": identidad,
                    })
                    continue

                grupo, subgrupo = _grupo_subgrupo_documento(hallazgo.get("url_documento"))
                cursor.execute(
                    "SELECT id FROM procesos WHERE identificador_estable=%s",
                    (hallazgo["referencia"],),
                )
                existente = cursor.fetchone()
                if existente:
                    proceso_id = existente["id"]
                    resultado["existentes"] += 1
                    if grupo or subgrupo:
                        cursor.execute(
                            "UPDATE procesos SET grupo=COALESCE(%s,grupo),subgrupo=COALESCE(%s,subgrupo),updated_at=NOW() WHERE id=%s",
                            (grupo, subgrupo, proceso_id),
                        )
                    if hallazgo.get("plazas") is not None:
                        cursor.execute(
                            "UPDATE procesos SET plazas=%s,updated_at=NOW() WHERE id=%s AND plazas IS NULL",
                            (hallazgo["plazas"], proceso_id),
                        )
                else:
                    if identidad["tipo"] == "AYUNTAMIENTO":
                        municipio = identidad["municipio"]
                        org = resolver_organismo(
                            cursor,
                            tipo="AYUNTAMIENTO",
                            provincia="Castellón",
                            municipio=municipio,
                        )
                        if org is None:
                            nombre = f"Ayuntamiento de {municipio}"
                            cursor.execute(
                                """
                                INSERT INTO organismos
                                    (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                                VALUES (%s,'AYUNTAMIENTO',%s,'Castellón',TRUE,NOW(),NOW())
                                RETURNING id
                                """,
                                (nombre, municipio),
                            )
                            organismo_id = cursor.fetchone()["id"]
                            resultado["organismos_creados"] += 1
                        else:
                            organismo_id = org["id"]
                    else:
                        nombre = "Diputación Provincial de Castellón"
                        org = resolver_organismo(
                            cursor,
                            tipo="DIPUTACION",
                            provincia="Castellón",
                            nombre=nombre,
                        )
                        if org is None:
                            cursor.execute(
                                """
                                INSERT INTO organismos
                                    (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                                VALUES (%s,'DIPUTACION',NULL,'Castellón',TRUE,NOW(),NOW())
                                RETURNING id
                                """,
                                (nombre,),
                            )
                            organismo_id = cursor.fetchone()["id"]
                            resultado["organismos_creados"] += 1
                        else:
                            organismo_id = org["id"]

                    cursor.execute(
                        """
                        INSERT INTO procesos
                            (organismo_id,codigo_externo,identificador_estable,denominacion,tipo_proceso,grupo,subgrupo,
                             estado,plazas,fecha_convocatoria,fuente_principal_id,es_oportunidad,
                             ambito_administrativo,datos_json,updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,'EN_CURSO',%s,%s,%s,TRUE,'SI',%s,NOW())
                        RETURNING id
                        """,
                        (
                            organismo_id,
                            hallazgo["id_anuncio"],
                            hallazgo["referencia"],
                            hallazgo["titulo"],
                            (
                                "Anuncio difícil cobertura (ADC)"
                                if clase == "ANUNCIO_DIFICIL_COBERTURA"
                                else None
                            ),
                            grupo,
                            subgrupo,
                            hallazgo.get("plazas"),
                            fecha_publicacion,
                            fuente_id,
                            Jsonb({
                                "url_oficial": hallazgo["url_documento"],
                                "bop_referencia": hallazgo["referencia"],
                                "origen": "BOP_CASTELLON",
                            }),
                        ),
                    )
                    proceso_id = cursor.fetchone()["id"]
                    resultado["nuevas"] += 1
                    # La recuperación BOE se ejecuta después del commit BOP,
                    # para que la segunda conexión vea el proceso ya confirmado.
                    if fecha_publicacion:
                        recuperaciones_boe.append((proceso_id, fecha_publicacion))

            cursor.execute(
                "SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s LIMIT 1",
                (fuente_id, hallazgo["referencia"]),
            )
            publicacion = cursor.fetchone()
            if publicacion is None:
                cursor.execute(
                    """
                    INSERT INTO publicaciones
                        (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,
                         url,datos_json,detectada_at)
                    VALUES (%s,%s,%s,'BOP',%s,%s,%s,%s,NOW())
                    RETURNING id
                    """,
                    (
                        proceso_id,
                        fuente_id,
                        hallazgo["referencia"],
                        hallazgo["titulo"],
                        fecha_publicacion,
                        hallazgo["url_documento"],
                        Jsonb({"origen": "BOP_CASTELLON", "clase": clase}),
                    ),
                )
                cursor.fetchone()
                resultado["publicaciones"] += 1

            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "estado": "VINCULADO" if clase == "SEGUIMIENTO" else "IMPORTADO",
                "proceso_id": proceso_id,
            })

        connection.commit()

    resultado["recuperaciones_boe"] = []
    for proceso_id, fecha_bases in recuperaciones_boe:
        recuperacion = recuperar_boe_para_proceso_bop(
            proceso_id=proceso_id,
            fecha_bases=fecha_bases,
            hasta=hasta,
            aplicar=True,
        )
        resultado["recuperaciones_boe"].append({
            "proceso_id": proceso_id,
            **recuperacion,
        })
    return resultado
