"""Grupo/subgrupo de convocatorias locales a partir de la subescala oficial.

Los anuncios del BOE de ayuntamientos y diputaciones declaran la plaza así:
"... perteneciente a la escala de Administración General, subescala Auxiliar".
En la administración local esa subescala fija el subgrupo de forma inequívoca:

    Administración General:  Técnica -> A1 | Gestión -> A2
                             Administrativa -> C1 | Auxiliar -> C2
    Administración Especial: clase Técnico Superior -> A1 | Técnico Medio -> A2
                             Técnico Auxiliar -> C1

No se deduce nada del nombre del puesto: solo se usa lo que declara el texto
oficial. Si hay dos subescalas o ninguna reconocible, no se rellena nada. Nunca
sustituye un grupo ya guardado (p. ej. uno declarado en las bases).
"""
from __future__ import annotations

import json
import re
import unicodedata
from contextlib import contextmanager
from typing import Any

_SUBESCALA_GENERAL = {
    "tecnica": "A1",
    "gestion": "A2",
    "administrativa": "C1",
    "auxiliar": "C2",
}
_CLASE_ESPECIAL = (
    ("tecnico superior", "A1"),
    ("tecnico medio", "A2"),
    ("tecnico auxiliar", "C1"),
)


def _sin(texto: str | None) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def clasificar_subescala(texto: str | None) -> dict[str, str] | None:
    """{"grupo": "C", "subgrupo": "C1", "subescala": ...} o None. Función pura."""
    n = _sin(texto)
    if not n.strip():
        return None
    subgrupo = literal = None

    if "administracion general" in n:
        m = re.search(r"subescala\s+(?:de\s+|d')?([^,.;]{1,50})", n)
        if m:
            fragmento = m.group(1)
            presentes = {clave for clave in _SUBESCALA_GENERAL
                         if re.search(rf"\b{clave}s?\b", fragmento)}
            if len(presentes) == 1:
                clave = next(iter(presentes))
                subgrupo, literal = _SUBESCALA_GENERAL[clave], f"Administración General / {clave}"
    elif "administracion especial" in n:
        encontradas = [(c, g) for c, g in _CLASE_ESPECIAL if c in n]
        if len(encontradas) == 1:
            subgrupo, literal = encontradas[0][1], f"Administración Especial / {encontradas[0][0]}"

    if not subgrupo:
        return None
    return {"grupo": subgrupo[0], "subgrupo": subgrupo, "subescala": literal}


def textos_oficiales(datos_json: dict[str, Any] | None) -> list[str]:
    """Textos del anuncio guardados en datos_json (raíz, boe_local o agregados)."""
    datos = datos_json if isinstance(datos_json, dict) else {}
    textos = [datos.get("texto_plaza")]
    boe = datos.get("boe_local")
    if isinstance(boe, dict):
        textos.append(boe.get("texto_plaza"))
    for item in datos.get("boe_local_agregados") or []:
        if isinstance(item, dict):
            textos.append(item.get("texto_plaza"))
    return [t for t in textos if isinstance(t, str) and t.strip()]


def clasificar_proceso(datos_json: dict[str, Any] | None) -> dict[str, str] | None:
    """Clasificación si TODOS los textos oficiales coinciden; None si no."""
    resultados = [clasificar_subescala(t) for t in textos_oficiales(datos_json)]
    resultados = [r for r in resultados if r]
    if not resultados:
        return None
    if len({r["subgrupo"] for r in resultados}) != 1:
        return None
    return resultados[0]


SQL_CANDIDATOS = (
    "SELECT p.id, p.denominacion, p.datos_json, o.nombre AS organismo "
    "FROM procesos p JOIN organismos o ON o.id = p.organismo_id "
    "WHERE p.es_oportunidad = TRUE AND p.ambito_administrativo = 'SI' "
    "AND o.tipo IN ('AYUNTAMIENTO','DIPUTACION','MANCOMUNIDAD') "
    "AND NULLIF(BTRIM(p.grupo),'') IS NULL AND NULLIF(BTRIM(p.subgrupo),'') IS NULL "
    "ORDER BY p.id"
)

LIMITE_DETALLE = 200


def sincronizar_grupos_subescala(*, aplicar: bool = False, abrir_cursor=None) -> dict[str, Any]:
    """Rellena grupo/subgrupo vacíos con la subescala oficial. Idempotente."""

    @contextmanager
    def _cursor_por_defecto():
        from psycopg.rows import dict_row
        from .database import get_connection
        with get_connection() as conexion, conexion.cursor(row_factory=dict_row) as cursor:
            yield cursor

    r: dict[str, Any] = {"modo": "APLICADO" if aplicar else "SOLO_REVISION",
                         "revisados": 0, "clasificados": 0, "sin_clasificar": 0, "detalle": []}
    with (abrir_cursor or _cursor_por_defecto)() as cursor:
        cursor.execute(SQL_CANDIDATOS)
        for proceso in list(cursor.fetchall()):
            r["revisados"] += 1
            c = clasificar_proceso(proceso.get("datos_json"))
            if c is None:
                r["sin_clasificar"] += 1
                if len(r["detalle"]) < LIMITE_DETALLE:
                    r["detalle"].append({"proceso_id": proceso["id"], "organismo": proceso.get("organismo"),
                                         "denominacion": proceso.get("denominacion"), "resultado": None})
                continue
            r["clasificados"] += 1
            if aplicar:
                cursor.execute(
                    "UPDATE procesos SET grupo=%s, subgrupo=%s, "
                    "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s::jsonb, updated_at=NOW() "
                    "WHERE id=%s AND NULLIF(BTRIM(grupo),'') IS NULL AND NULLIF(BTRIM(subgrupo),'') IS NULL",
                    (c["grupo"], c["subgrupo"],
                     json.dumps({"clasificacion_origen": {"origen": "SUBESCALA_BOE", "subescala": c["subescala"]}}),
                     proceso["id"]),
                )
            if len(r["detalle"]) < LIMITE_DETALLE:
                r["detalle"].append({"proceso_id": proceso["id"], "organismo": proceso.get("organismo"),
                                     "denominacion": proceso.get("denominacion"),
                                     "resultado": c["subgrupo"], "subescala": c["subescala"]})
    return r
