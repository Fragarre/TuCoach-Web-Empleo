from __future__ import annotations

"""Descubrimiento complementario, de solo lectura, de bolsas administrativas GVA.

Este módulo NO persiste procesos, publicaciones ni cambios y NO genera
notificaciones. Su función es producir un plan auditable antes de habilitar
cualquier escritura.
"""

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from psycopg.rows import dict_row

from . import gva_clean
from .database import get_connection
from .gva_estatal_service import _get_gva_con_reintentos
from .gva_estatal_source import nuevo_cliente
from .organismos import resolver_organismo


CODIGOS_ADMIN_ESTRICTOS = ("A1-01", "A2-01", "C1-01", "C2-01")
EXCLUSIONES = (
    "promocion interna",
    "dificil cobertura",
    "acto unico",
    "consulta de baremo",
    "consulta de estados",
    "cesion de integrantes",
)


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto or "")
    return "".join(
        c for c in normalizado if unicodedata.category(c) != "Mn"
    ).lower()


def _id_emp(href: str) -> int | None:
    valores = parse_qs(urlparse(href).query).get("id_emp")
    if not valores:
        return None
    try:
        return int(valores[0])
    except (TypeError, ValueError):
        return None


def _es_tarjeta_bolsa(enlace) -> bool:
    tarjeta = enlace.find_parent(
        class_=lambda valor: valor and "card" in str(valor).split()
    )
    texto = " ".join((tarjeta or enlace).get_text(" ", strip=True).split())
    return bool(
        re.search(
            r"proceso selectivo:\s*bolsa de trabajo\b",
            _sin_acentos(texto),
        )
    )


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
        if identificador is None or not _es_tarjeta_bolsa(enlace):
            continue
        encontrados[identificador] = urljoin(gva_clean.GVA_BASE_URL, href)
    return encontrados


def _clasificar_detalle(id_emp: int, url: str, html: str) -> dict[str, Any]:
    proceso = gva_clean.parsear_detalle(url, html, id_emp)
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    normalizado = _sin_acentos(texto)
    codigos = [
        codigo
        for codigo in CODIGOS_ADMIN_ESTRICTOS
        if codigo.lower() in normalizado
    ]
    exclusion = next(
        (x for x in EXCLUSIONES if _sin_acentos(x) in normalizado),
        None,
    )
    es_bolsa = proceso.get("tipo_proceso") == "Bolsa de trabajo"
    proceso["cuerpo_escala"] = codigos[0] if len(codigos) == 1 else None
    proceso["ambito_administrativo"] = "SI" if codigos else "NO"
    proceso["es_oportunidad"] = bool(codigos and es_bolsa and exclusion is None)
    proceso["motivo_exclusion"] = exclusion
    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        "fuente_descubrimiento": "sede.gva.es",
        "codigos_administrativos": codigos,
    }
    return proceso


def _cargar_coincidencias(candidatos: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    ids = [int(p["datos_json"]["id_emp"]) for p in candidatos]
    identificadores = [str(p["identificador_estable"]) for p in candidatos]
    if not ids:
        return {}

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo = resolver_organismo(
            cursor,
            tipo="ADMINISTRACION_AUTONOMICA",
            provincia=None,
            nombre="Generalitat Valenciana",
        )
        if organismo is None:
            raise RuntimeError(
                "Auditoría GVA bloqueada: no existe el organismo Generalitat Valenciana"
            )
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, datos_json
            FROM procesos
            WHERE organismo_id=%s
              AND (
                    identificador_estable = ANY(%s)
                    OR CASE
                         WHEN COALESCE(datos_json->>'id_emp','') ~ '^[0-9]+$'
                         THEN (datos_json->>'id_emp')::bigint = ANY(%s)
                         ELSE FALSE
                       END
                    OR CASE
                         WHEN COALESCE(datos_json->>'codigo_gva','') ~ '^[0-9]+$'
                         THEN (datos_json->>'codigo_gva')::bigint = ANY(%s)
                         ELSE FALSE
                       END
                  )
            """,
            (organismo["id"], identificadores, ids, ids),
        )
        filas = list(cursor.fetchall())

    salida: dict[int, dict[str, Any]] = {}
    for fila in filas:
        datos = fila.get("datos_json") or {}
        valores = [datos.get("id_emp"), datos.get("codigo_gva")]
        identificador = str(fila.get("identificador_estable") or "")
        m = re.fullmatch(r"GVA:(\d+)", identificador)
        if m:
            valores.append(m.group(1))
        for valor in valores:
            try:
                numero = int(valor)
            except (TypeError, ValueError):
                continue
            if numero in ids:
                salida[numero] = dict(fila)
    return salida


def planificar_bolsas_gva_complementarias() -> dict[str, Any]:
    """Genera un plan NUEVA/YA_EXISTE sin realizar ninguna escritura."""
    descubiertas: dict[int, str] = {}
    diagnostico: list[dict[str, Any]] = []

    with nuevo_cliente() as client:
        for codigo in CODIGOS_ADMIN_ESTRICTOS:
            try:
                encontradas = _descubrir_por_codigo(client, codigo)
                descubiertas.update(encontradas)
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "OK",
                    "bolsas": len(encontradas),
                })
            except Exception as exc:
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        candidatos: list[dict[str, Any]] = []
        excluidos: list[dict[str, Any]] = []
        for id_emp, url in sorted(descubiertas.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url, intentos=1)
                proceso = _clasificar_detalle(id_emp, url, respuesta.text)
            except Exception as exc:
                excluidos.append({
                    "id_emp": id_emp,
                    "url": url,
                    "motivo": "error_detalle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            if proceso["es_oportunidad"]:
                candidatos.append(proceso)
            else:
                excluidos.append({
                    "id_emp": id_emp,
                    "url": url,
                    "motivo": proceso.get("motivo_exclusion") or "fuera_filtro",
                })

    existentes = _cargar_coincidencias(candidatos)
    acciones: list[dict[str, Any]] = []
    for proceso in candidatos:
        id_emp = int(proceso["datos_json"]["id_emp"])
        existente = existentes.get(id_emp)
        acciones.append({
            "accion": "YA_EXISTE" if existente else "NUEVA",
            "id_emp": id_emp,
            "identificador_estable": proceso["identificador_estable"],
            "denominacion": proceso["denominacion"],
            "cuerpo_escala": proceso.get("cuerpo_escala"),
            "proceso_existente_id": existente.get("id") if existente else None,
            "identificador_existente": (
                existente.get("identificador_estable") if existente else None
            ),
        })

    return {
        "modo": "SOLO_REVISION",
        "escrituras_bd": False,
        "descubiertas": len(descubiertas),
        "candidatas": len(candidatos),
        "nuevas": sum(a["accion"] == "NUEVA" for a in acciones),
        "ya_existentes": sum(a["accion"] == "YA_EXISTE" for a in acciones),
        "diagnostico": diagnostico,
        "acciones": acciones,
        "excluidos": excluidos,
    }
