from __future__ import annotations

from datetime import date
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from . import gva_clean
from .gva_dogv_diagnostico import diagnosticar_seguimiento_dogv
from .gva_estatal_import import construir_registro
from .gva_estatal_persist import persistir_registros
from .gva_estatal_seguimiento import actualizar_seguimientos_gva
from .gva_estatal_source import (
    clasificar_oportunidad,
    descubrir_referencias,
    nuevo_cliente,
    obtener_detalle,
)


def _signatura_dogv(url: str | None) -> str | None:
    """Extrae la signatura estable YYYY_NUM de una URL PDF del DOGV."""
    m = re.search(r"/pdf/(\d{4})_(\d+)_", str(url or ""), re.I)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _get_gva_con_reintentos(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    intentos: int = 3,
) -> httpx.Response:
    """GET GVA con reintentos limitados ante fallos transitorios de red."""
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            respuesta = client.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            if intento < intentos:
                time.sleep(2 * intento)
    assert ultimo is not None
    raise ultimo


def _descubrir_detalles_para_resolver(
    client: httpx.Client,
    max_paginas: int = 10,
) -> tuple[list[tuple[int, str]], list[dict[str, Any]]]:
    """Descubre fichas GVA sin limitarse a plazos de solicitud abiertos.

    Es una búsqueda auxiliar exclusiva para resolver el id_emp de una
    convocatoria ya identificada por la fuente estatal. No cambia el
    descubrimiento ordinario de convocatorias GVA, que sigue usando
    gva_clean.descubrir_detalles() con plazos=A.
    """
    encontrados: dict[int, str] = {}
    diagnostico_red: list[dict[str, Any]] = []

    for pagina in range(1, max_paginas + 1):
        try:
            respuesta = _get_gva_con_reintentos(
                client,
                gva_clean.GVA_SEARCH_URL,
                params={
                    "pagina": str(pagina),
                    "tipoOrganismo": "1",
                    "tamanyoPagina": "30",
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            diagnostico_red.append({
                "fase": "listado",
                "pagina": pagina,
                "url": str(getattr(getattr(exc, "request", None), "url", gva_clean.GVA_SEARCH_URL)),
                "error": f"{type(exc).__name__}: {exc}",
            })
            break

        soup = BeautifulSoup(respuesta.text, "html.parser")
        encontrados_pagina = 0
        for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
            href = enlace.get("href")
            if not href:
                continue
            m = re.search(r"id_emp=(\d+)", href)
            if not m:
                continue
            id_emp = int(m.group(1))
            encontrados[id_emp] = urljoin(gva_clean.GVA_BASE_URL, href)
            encontrados_pagina += 1

        if encontrados_pagina == 0:
            break

    return sorted(encontrados.items()), diagnostico_red


def diagnosticar_filtros_plazo_gva(id_emp_objetivo: int | None = None) -> dict[str, Any]:
    """Diagnóstico aislado de acceso a los dos buscadores oficiales GVA.

    No persiste nada ni modifica el resolver. Mantiene las pruebas A/C/P sobre
    sede.gva.es y añade una prueba del buscador clásico de www.gva.es, que usa
    el mismo identificador id_emp.
    """
    variantes = ("A", "C", "P")
    resultado: dict[str, Any] = {
        "modo": "SOLO_DIAGNOSTICO",
        "objetivo_id_emp": id_emp_objetivo,
        "controles_plazo": [],
        "variantes": [],
        "www_gva": {},
    }

    with nuevo_cliente() as client:
        for valor in variantes:
            params = {
                "pagina": "1",
                "tipoOrganismo": "1",
                "plazos": valor,
                "tamanyoPagina": "30",
            }
            try:
                respuesta = _get_gva_con_reintentos(
                    client,
                    gva_clean.GVA_SEARCH_URL,
                    params=params,
                    intentos=1,
                )
                soup = BeautifulSoup(respuesta.text, "html.parser")
                ids: list[int] = []
                for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
                    href = str(enlace.get("href") or "")
                    m = re.search(r"id_emp=(\d+)", href)
                    if m:
                        ids.append(int(m.group(1)))
                ids_unicos = sorted(set(ids))

                if valor == "A":
                    controles: list[dict[str, Any]] = []
                    for elemento in soup.find_all(["input", "option", "select"]):
                        nombre = str(elemento.get("name") or "")
                        identificador = str(elemento.get("id") or "")
                        if "plazo" not in nombre.lower() and "plazo" not in identificador.lower():
                            continue
                        controles.append({
                            "tag": elemento.name,
                            "name": nombre or None,
                            "id": identificador or None,
                            "value": elemento.get("value"),
                            "texto": " ".join(elemento.get_text(" ", strip=True).split())[:160] or None,
                        })
                    resultado["controles_plazo"] = controles[:30]

                resultado["variantes"].append({
                    "plazos": valor,
                    "ok": True,
                    "status_code": respuesta.status_code,
                    "url": str(respuesta.url),
                    "fichas_en_pagina": len(ids_unicos),
                    "muestra_id_emp": ids_unicos[:10],
                    "contiene_objetivo": (
                        id_emp_objetivo in ids_unicos if id_emp_objetivo is not None else None
                    ),
                })
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                resultado["variantes"].append({
                    "plazos": valor,
                    "ok": False,
                    "url": str(getattr(getattr(exc, "request", None), "url", gva_clean.GVA_SEARCH_URL)),
                    "error": f"{type(exc).__name__}: {exc}",
                })

        url_clasico = "https://www.gva.es/es/inicio/atencion_ciudadano/buscadores/busc_empleo_publico"
        try:
            respuesta = _get_gva_con_reintentos(
                client,
                url_clasico,
                params={"buscar": "buscar", "paginaActual": "1"},
                intentos=1,
            )
            soup = BeautifulSoup(respuesta.text, "html.parser")
            ids: list[int] = []
            for enlace in soup.select('a[href*="detalle_oposiciones"]'):
                href = str(enlace.get("href") or "")
                m = re.search(r"id_emp=(\d+)", href)
                if m:
                    ids.append(int(m.group(1)))
            ids_unicos = sorted(set(ids))
            resultado["www_gva"] = {
                "ok": True,
                "status_code": respuesta.status_code,
                "url": str(respuesta.url),
                "fichas_en_pagina": len(ids_unicos),
                "muestra_id_emp": ids_unicos[:10],
                "contiene_objetivo": (
                    id_emp_objetivo in ids_unicos if id_emp_objetivo is not None else None
                ),
            }
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            resultado["www_gva"] = {
                "ok": False,
                "url": str(getattr(getattr(exc, "request", None), "url", url_clasico)),
                "error": f"{type(exc).__name__}: {exc}",
            }

    return resultado


def _enriquecer_fichas_oficiales_gva(registros: list[dict[str, Any]], client) -> dict[str, Any]:
    """Relaciona una alta estatal con su ficha de Sede GVA sin heurísticas débiles.

    La correspondencia se acepta únicamente cuando el PDF DOGV primario de la
    convocatoria aparece en exactamente una ficha descubierta en la Sede GVA.
    Si no hay coincidencia o hay más de una, el proceso sigue siendo válido pero
    se deja sin ficha para volver a intentarlo en una ejecución posterior.

    Los fallos puntuales de red o timeout de la Sede GVA no bloquean la
    importación estatal: la ficha queda pendiente y se podrá resolver después.
    """
    pendientes: dict[str, list[dict[str, Any]]] = {}
    for registro in registros:
        datos = registro.get("datos_json") or {}
        if datos.get("url_detalle"):
            continue
        signatura = _signatura_dogv(datos.get("url_publicacion_oficial"))
        if signatura:
            pendientes.setdefault(signatura, []).append(registro)

    if not pendientes:
        return {
            "pendientes": 0,
            "resueltas": 0,
            "sin_coincidencia": 0,
            "ambiguas": 0,
            "errores_red": 0,
            "diagnostico_red": [],
        }

    candidatas_por_signatura: dict[str, list[tuple[int, str]]] = {
        signatura: [] for signatura in pendientes
    }

    detalles, diagnostico_red = _descubrir_detalles_para_resolver(client, max_paginas=10)

    for id_emp, url in detalles:
        try:
            respuesta = _get_gva_con_reintentos(client, url)
            html = respuesta.text
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            diagnostico_red.append({
                "fase": "detalle",
                "id_emp": int(id_emp),
                "url": str(url),
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        for signatura in pendientes:
            if len(candidatas_por_signatura[signatura]) >= 2:
                continue
            if signatura in html:
                candidatas_por_signatura[signatura].append((int(id_emp), str(url)))

    errores_red = len(diagnostico_red)
    resueltas = 0
    ambiguas = 0
    sin_coincidencia = 0

    for signatura, registros_signatura in pendientes.items():
        candidatas = candidatas_por_signatura.get(signatura) or []
        if len(candidatas) == 1 and errores_red == 0:
            id_emp, url = candidatas[0]
            for registro in registros_signatura:
                datos = dict(registro.get("datos_json") or {})
                datos["id_emp"] = id_emp
                datos["codigo_gva"] = str(id_emp)
                datos["url_detalle"] = url
                datos["ficha_gva_resuelta_por"] = "DOGV_SIGNATURA_EXACTA"
                registro["datos_json"] = datos
                resueltas += 1
        elif len(candidatas) > 1:
            ambiguas += len(registros_signatura)
        else:
            sin_coincidencia += len(registros_signatura)

    return {
        "pendientes": sum(len(v) for v in pendientes.values()),
        "resueltas": resueltas,
        "sin_coincidencia": sin_coincidencia,
        "ambiguas": ambiguas,
        "errores_red": errores_red,
        "diagnostico_red": diagnostico_red,
    }


def importar_gva_estatal(*, desde: date, hasta: date, aplicar: bool = False) -> dict[str, Any]:
    """Descubre convocatorias nuevas y revisa las fichas GVA ya conocidas.

    `aplicar=False` es el modo por defecto y no escribe en base de datos.
    Las fechas delimitan únicamente el descubrimiento de nuevas convocatorias;
    el seguimiento de procesos activos se hace por sus fichas ya persistidas.
    """
    if desde > hasta:
        raise ValueError("La fecha 'desde' no puede ser posterior a 'hasta'")

    registros: list[dict[str, Any]] = []
    revision: list[dict[str, Any]] = []
    excluidas = {
        "fuera_ambito_administrativo": 0,
        "promocion_interna": 0,
        "organismo_excluido": 0,
        "via_no_incluida": 0,
    }

    with nuevo_cliente() as client:
        tarjetas = descubrir_referencias(client, desde, hasta)
        for tarjeta in tarjetas:
            detalle = obtener_detalle(client, int(tarjeta["referencia"]))
            clasificada = clasificar_oportunidad(tarjeta, detalle)

            if clasificada.get("es_oportunidad"):
                registros.append(construir_registro(tarjeta, detalle))
                continue

            motivo = clasificada.get("motivo") or "revision"
            if motivo == "organismo_revision" or (
                clasificada.get("ambito_administrativo") == "SI"
                and motivo == "via_no_incluida"
            ):
                revision.append({
                    "referencia_estatal": clasificada.get("referencia"),
                    "titulo": clasificada.get("titulo"),
                    "organo": clasificada.get("organo") or clasificada.get("organo_detalle"),
                    "via": clasificada.get("via"),
                    "codigos_administrativos": clasificada.get("codigos_administrativos") or [],
                    "motivo": motivo,
                    "url": clasificada.get("url"),
                })
                continue

            excluidas[motivo] = excluidas.get(motivo, 0) + 1

        fichas_gva = _enriquecer_fichas_oficiales_gva(registros, client)

    persistencia = persistir_registros(registros, aplicar=aplicar)
    seguimiento = actualizar_seguimientos_gva(aplicar=aplicar, solo_oportunidades=True)
    auditoria_dogv = diagnosticar_seguimiento_dogv() if not aplicar else None
    errores_seguimiento = list(seguimiento.get("errores") or [])
    bolsas = seguimiento.get("bolsas") or {}
    errores_seguimiento.extend(list(bolsas.get("errores") or []))
    return {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "tarjetas_autonomico_cv": len(tarjetas),
        "oportunidades_incluidas": len(registros),
        "fichas_gva": fichas_gva,
        "revision_manual": len(revision),
        "excluidas": excluidas,
        "revision": revision,
        "persistencia": persistencia,
        "seguimiento": seguimiento,
        "errores": errores_seguimiento,
        "auditoria_dogv": auditoria_dogv,
    }
