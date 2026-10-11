"""Grupo/subgrupo y plazas desde el documento oficial de las bases.

Los anuncios del BOP (y su copia en la sede de la Diputación de Alicante)
incluyen una tabla "GRUPO | SUBGRUPO | SUBESCALA | N.º VACANTES | DENOMINACIÓN"
y suelen repetir "grupo C, subgrupo C1" en el texto. Es la fuente más fiable del
puesto concreto. Reglas de seguridad:

* Solo rellena grupo/subgrupo vacíos y plazas vacías; nunca sustituye datos.
* Si el documento contiene puestos de subgrupos distintos, no decide (hay que
  repartirlos por puesto).
* Solo usa lo que declara el documento, nunca el nombre del puesto.
"""
from __future__ import annotations

import json
import re
import unicodedata
from contextlib import contextmanager
from typing import Any, Callable

_SUBGRUPOS = "A1|A2|C1|C2"
_RE_FILA = re.compile(
    rf"\b([abc])\s+({_SUBGRUPOS})\s+[a-z ]{{3,30}}?\s+(\d{{1,3}})\b", re.I
)
_RE_PROSA = re.compile(rf"\bsubgrupo\s*[:.-]?\s*({_SUBGRUPOS})\b", re.I)


def _sin(texto: str | None) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def extraer_puesto(texto: str | None) -> dict[str, Any] | None:
    """{"grupo","subgrupo","plazas"|None,"origen"} o None. Función pura."""
    n = re.sub(r"\s+", " ", _sin(texto))
    if not n.strip():
        return None
    filas: set[tuple[str, int]] = set()
    pos = n.find("vacantes")
    if pos >= 0:
        for m in _RE_FILA.finditer(n, pos):
            if m.group(2)[0].lower() == m.group(1):
                filas.add((m.group(2).upper(), int(m.group(3))))
    if filas:
        subgrupos = {s for s, _ in filas}
        if len(subgrupos) != 1:
            return None
        subgrupo = next(iter(subgrupos))
        return {"grupo": subgrupo[0], "subgrupo": subgrupo,
                "plazas": sum(v for _, v in filas), "origen": "TABLA"}
    prosa = {m.group(1).upper() for m in _RE_PROSA.finditer(n)}
    if len(prosa) == 1:
        subgrupo = next(iter(prosa))
        return {"grupo": subgrupo[0], "subgrupo": subgrupo, "plazas": None, "origen": "TEXTO"}
    from .clasificacion_subescala import clasificar_subescala
    c = clasificar_subescala(texto)
    if c:
        return {"grupo": c["grupo"], "subgrupo": c["subgrupo"], "plazas": None, "origen": "SUBESCALA"}
    return None


def menciona_municipio(texto: str | None, organismo: str | None) -> bool:
    """El documento nombra al municipio del proceso (evita leer otro anuncio)."""
    n = re.sub(r"[^a-z0-9 ]", " ", _sin(texto))
    n = re.sub(r"\s+", " ", n)
    nombre = re.sub(r"^(ayuntamiento|mancomunidad|mancomunitat|diputacion provincial)\s+(de |del |d')?", "",
                    _sin(organismo).strip())
    alias = [re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", a)).strip() for a in nombre.split("/")]
    return any(a and a in n for a in alias)


def urls_documento(proceso: dict[str, Any]) -> list[str]:
    """Enlaces candidatos al documento del BOP/sede (no BOE)."""
    datos = proceso.get("datos_json") if isinstance(proceso.get("datos_json"), dict) else {}
    candidatas = list(proceso.get("urls_publicaciones") or [])
    candidatas.append(datos.get("url_oficial"))
    bases = datos.get("bases_bop")
    if isinstance(bases, dict):
        candidatas.append(bases.get("url"))
    vistas: list[str] = []
    for u in candidatas:
        if isinstance(u, str) and u.startswith("http") and "boe.es" not in u and u not in vistas:
            vistas.append(u)
    return vistas


SQL_CANDIDATOS = (
    "SELECT p.id, p.denominacion, p.plazas, p.datos_json, o.nombre AS organismo, "
    "COALESCE((SELECT array_agg(pub.url) FROM publicaciones pub "
    "          WHERE pub.proceso_id = p.id AND pub.url IS NOT NULL), ARRAY[]::text[]) AS urls_publicaciones "
    "FROM procesos p JOIN organismos o ON o.id = p.organismo_id "
    "WHERE p.es_oportunidad = TRUE AND p.ambito_administrativo = 'SI' "
    "AND o.tipo IN ('AYUNTAMIENTO','DIPUTACION','MANCOMUNIDAD') "
    "AND NULLIF(BTRIM(p.grupo),'') IS NULL AND NULLIF(BTRIM(p.subgrupo),'') IS NULL "
    "ORDER BY p.id"
)


def _obtener_texto_defecto(url: str) -> str:
    import httpx
    from .bop_valencia import _obtener_texto
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        return _obtener_texto(client, url)


def sincronizar_grupos_documento(
    *, aplicar: bool = False, abrir_cursor=None,
    obtener_texto: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Rellena grupo/subgrupo (y plazas) desde el documento de las bases."""

    @contextmanager
    def _cursor_por_defecto():
        from psycopg.rows import dict_row
        from .database import get_connection
        with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            yield cursor

    leer = obtener_texto or _obtener_texto_defecto
    r: dict[str, Any] = {"modo": "APLICADO" if aplicar else "SOLO_REVISION",
                         "revisados": 0, "clasificados": 0, "sin_clasificar": 0,
                         "sin_documento": 0, "errores": 0, "detalle": []}
    with (abrir_cursor or _cursor_por_defecto)() as cursor:
        cursor.execute(SQL_CANDIDATOS)
        for proceso in list(cursor.fetchall()):
            r["revisados"] += 1
            urls = urls_documento(proceso)
            fila = {"proceso_id": proceso["id"], "organismo": proceso.get("organismo"),
                    "denominacion": proceso.get("denominacion"), "resultado": None}
            if not urls:
                r["sin_documento"] += 1
                r["detalle"].append(fila)
                continue
            puesto = None
            for url in urls:
                try:
                    texto = leer(url)
                    if not menciona_municipio(texto, proceso.get("organismo")):
                        continue
                    puesto = extraer_puesto(texto)
                except Exception:  # noqa: BLE001
                    r["errores"] += 1
                    continue
                if puesto:
                    fila["url"] = url
                    break
            if not puesto:
                r["sin_clasificar"] += 1
                r["detalle"].append(fila)
                continue
            r["clasificados"] += 1
            fila.update(resultado=puesto["subgrupo"], plazas=puesto["plazas"], origen_dato=puesto["origen"])
            if aplicar:
                cursor.execute(
                    "UPDATE procesos SET grupo=%s, subgrupo=%s, "
                    "plazas = CASE WHEN (plazas IS NULL OR plazas = 0) AND %s::int IS NOT NULL THEN %s::int ELSE plazas END, "
                    "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s::jsonb, updated_at=NOW() "
                    "WHERE id=%s AND NULLIF(BTRIM(grupo),'') IS NULL AND NULLIF(BTRIM(subgrupo),'') IS NULL",
                    (puesto["grupo"], puesto["subgrupo"], puesto["plazas"], puesto["plazas"],
                     json.dumps({"clasificacion_origen": {"origen": "DOCUMENTO_BASES_" + puesto["origen"],
                                                           "url": fila.get("url")}}),
                     proceso["id"]),
                )
            r["detalle"].append(fila)
    return r
