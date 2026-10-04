from __future__ import annotations

from datetime import date, timedelta
from html import unescape
import re
from typing import Any

import httpx

from .ambito_administrativo import clasificar_ambito_administrativo

BOE_SUMARIO = "https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}"
BOE_TEXTO = "https://www.boe.es/diario_boe/txt.php?id={ident}"
SECCION_OPOSICIONES_CODIGO = "2B"
DEPARTAMENTO_LOCAL_CODIGO = "9525"
PROVINCIAS = ("alicante", "castellon", "castellón", "valencia", "valència")


def _lista(valor: Any) -> list[Any]:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def _texto_url(valor: Any) -> str | None:
    if isinstance(valor, str):
        return valor.strip() or None
    if isinstance(valor, dict):
        texto = valor.get("texto")
        return str(texto).strip() if texto else None
    return None


def _iter_items_locales(data: dict[str, Any]):
    sumario = ((data.get("data") or {}).get("sumario") or {})
    for diario in _lista(sumario.get("diario")):
        for seccion in _lista((diario or {}).get("seccion")):
            if str((seccion or {}).get("codigo") or "").strip() != SECCION_OPOSICIONES_CODIGO:
                continue
            nombre_seccion = str((seccion or {}).get("nombre") or "").strip()
            for departamento in _lista((seccion or {}).get("departamento")):
                if str((departamento or {}).get("codigo") or "").strip() != DEPARTAMENTO_LOCAL_CODIGO:
                    continue
                nombre_departamento = str((departamento or {}).get("nombre") or "").strip()
                contenido_departamento = (departamento or {}).get("texto")
                if not isinstance(contenido_departamento, dict):
                    contenido_departamento = departamento or {}
                for epigrafe in _lista(contenido_departamento.get("epigrafe")):
                    nombre_epigrafe = str((epigrafe or {}).get("nombre") or "").strip()
                    for item in _lista((epigrafe or {}).get("item")):
                        if isinstance(item, dict):
                            yield nombre_seccion, nombre_departamento, nombre_epigrafe, item


def _texto_documento_boe(client: httpx.Client, ident: str) -> str:
    r = client.get(BOE_TEXTO.format(ident=ident))
    r.raise_for_status()
    texto = re.sub(r"<script\b[^>]*>.*?</script>", " ", r.text, flags=re.I | re.S)
    texto = re.sub(r"<style\b[^>]*>.*?</style>", " ", texto, flags=re.I | re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _fragmentos_plazas(texto: str) -> list[str]:
    """Extrae únicamente frases que describen plazas, evitando clasificar toda la página BOE."""
    fragmentos: list[str] = []
    for frag in re.split(r"(?<=[.!?])\s+", texto):
        limpio = frag.strip()
        if re.search(r"\bplazas?\s+de\b", limpio, flags=re.I):
            fragmentos.append(limpio)
    return fragmentos


def _clasificar_documento_por_plazas(texto: str) -> tuple[str, list[dict[str, str]]]:
    detalle: list[dict[str, str]] = []
    estados: list[str] = []
    for frag in _fragmentos_plazas(texto):
        estado = clasificar_ambito_administrativo(
            {"denominacion": frag, "cuerpo_escala": None, "grupo": None}
        )
        estados.append(estado)
        detalle.append({"ambito": estado, "texto": frag[:300]})
    if "SI" in estados:
        return "SI", detalle
    if "REVISION" in estados:
        return "REVISION", detalle
    return "NO", detalle


def diagnosticar_boe_local(*, hasta: date | None = None, dias: int = 30) -> dict[str, Any]:
    """SOLO LECTURA. Diagnóstico por etapas de convocatorias locales CV desde BOE."""
    hasta = hasta or date.today()
    desde = hasta - timedelta(days=max(0, dias - 1))
    hallazgos: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    vistos: set[str] = set()
    items_locales = 0
    candidatos_cv = 0
    documentos_leidos = 0
    descartados_ambito: list[dict[str, Any]] = []
    headers = {
        "Accept": "application/json",
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
    }

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        fecha = desde
        while fecha <= hasta:
            try:
                r = client.get(BOE_SUMARIO.format(fecha=fecha.strftime("%Y%m%d")))
                if r.status_code == 404:
                    fecha += timedelta(days=1)
                    continue
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                errores.append({"fecha": fecha.isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
                fecha += timedelta(days=1)
                continue

            for seccion, departamento, epigrafe, item in _iter_items_locales(data):
                items_locales += 1
                ident = str(item.get("identificador") or "").strip()
                titulo = str(item.get("titulo") or "").strip()
                if not ident or ident in vistos or not titulo:
                    continue

                contexto = f"{epigrafe} {titulo}".lower()
                if not any(p in contexto for p in PROVINCIAS):
                    continue
                candidatos_cv += 1

                ambito_sumario = clasificar_ambito_administrativo(
                    {"denominacion": f"{epigrafe}. {titulo}", "cuerpo_escala": None, "grupo": None}
                )
                ambito = ambito_sumario
                fuente_ambito = "SUMARIO"
                texto_documento = ""
                plazas_clasificadas: list[dict[str, str]] = []

                if ambito_sumario != "SI":
                    try:
                        texto_documento = _texto_documento_boe(client, ident)
                        documentos_leidos += 1
                        ambito, plazas_clasificadas = _clasificar_documento_por_plazas(texto_documento)
                        fuente_ambito = "DOCUMENTO_BOE_PLAZAS"
                    except Exception as exc:
                        errores.append({
                            "fecha": fecha.isoformat(),
                            "error": f"{ident}: {type(exc).__name__}: {str(exc)[:160]}",
                        })
                        ambito = ambito_sumario

                if ambito != "SI":
                    if len(descartados_ambito) < 20:
                        descartados_ambito.append({
                            "fecha": fecha.isoformat(),
                            "boe_id": ident,
                            "epigrafe": epigrafe,
                            "titulo": titulo,
                            "ambito_sumario": ambito_sumario,
                            "ambito_documento": ambito,
                            "fuente_ambito": fuente_ambito,
                            "plazas_clasificadas": plazas_clasificadas,
                        })
                    continue

                vistos.add(ident)
                hallazgos.append(
                    {
                        "fecha": fecha.isoformat(),
                        "boe_id": ident,
                        "seccion": seccion,
                        "departamento": departamento,
                        "epigrafe": epigrafe,
                        "titulo": titulo,
                        "url_html": _texto_url(item.get("url_html")),
                        "url_xml": _texto_url(item.get("url_xml")),
                        "url_pdf": _texto_url(item.get("url_pdf")),
                        "ambito_administrativo": ambito,
                        "fuente_ambito": fuente_ambito,
                        "plazas_clasificadas": plazas_clasificadas,
                    }
                )
            fecha += timedelta(days=1)

    return {
        "modo": "SOLO_LECTURA",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "items_locales": items_locales,
        "candidatos_cv": candidatos_cv,
        "documentos_leidos": documentos_leidos,
        "descartados_ambito": descartados_ambito,
        "hallazgos": len(hallazgos),
        "dias_con_error": len(errores),
        "errores": errores,
        "detalle": hallazgos,
    }
