"""Grupo/subgrupo deducido del NOMBRE oficial del puesto (último recurso).

Solo para denominaciones exactas que en la administración local designan una
subescala concreta. Se aplica después de las fuentes documentales (BOE, bases) y
únicamente a procesos que siguen sin grupo ni subgrupo. Queda marcado en
``datos_json.clasificacion_origen.origen = "DEDUCIDO_DEL_NOMBRE"``.

No se aplica a nombres con otros apellidos ("Administrativo de servicios
sociales", "Técnico medio de ..."), porque pueden esconder otro grupo.
"""
from __future__ import annotations

import json
import re
import unicodedata
from contextlib import contextmanager
from typing import Any

# Denominación normalizada (sin tildes, minúsculas, sin "/a", "o ...a") -> subgrupo
_NOMBRES = {
    "tecnico de administracion general": "A1",
    "tecnico administracion general": "A1",
    "administrativo": "C1",
    "auxiliar administrativo": "C2",
    "auxiliar de administracion general": "C2",
}


def _normalizar(denominacion: str | None) -> str:
    t = unicodedata.normalize("NFD", (denominacion or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\btecnico\s+o\s+tecnica\b", "tecnico", t)
    t = re.sub(r"\b(administrativo)\s+(?:o|/)\s*administrativa\b", r"\1", t)
    t = re.sub(r"\b(tecnico|administrativo|auxiliar)\s*/\s*(?:a|[a-z]+a)\b", r"\1", t)
    t = re.sub(r"(?<=[a-z])/a\b", "", t)
    t = re.sub(r"[^a-z ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clasificar_por_nombre(denominacion: str | None) -> dict[str, str] | None:
    """{"grupo","subgrupo","nombre"} si la denominación es una de las exactas."""
    n = _normalizar(denominacion)
    subgrupo = _NOMBRES.get(n)
    if not subgrupo:
        return None
    return {"grupo": subgrupo[0], "subgrupo": subgrupo, "nombre": n}


def sincronizar_grupos_por_nombre(*, aplicar: bool = False, abrir_cursor=None) -> dict[str, Any]:
    from .clasificacion_subescala import SQL_CANDIDATOS

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
            c = clasificar_por_nombre(proceso.get("denominacion"))
            fila = {"proceso_id": proceso["id"], "organismo": proceso.get("organismo"),
                    "denominacion": proceso.get("denominacion"), "resultado": None}
            if c is None:
                r["sin_clasificar"] += 1
                r["detalle"].append(fila)
                continue
            r["clasificados"] += 1
            fila["resultado"] = c["subgrupo"]
            if aplicar:
                cursor.execute(
                    "UPDATE procesos SET grupo=%s, subgrupo=%s, "
                    "datos_json = COALESCE(datos_json,'{}'::jsonb) || %s::jsonb, updated_at=NOW() "
                    "WHERE id=%s AND NULLIF(BTRIM(grupo),'') IS NULL AND NULLIF(BTRIM(subgrupo),'') IS NULL",
                    (c["grupo"], c["subgrupo"],
                     json.dumps({"clasificacion_origen": {"origen": "DEDUCIDO_DEL_NOMBRE", "nombre": c["nombre"]}}),
                     proceso["id"]),
                )
            r["detalle"].append(fila)
    return r
