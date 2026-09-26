"""Revisa y, opcionalmente, completa grupo/subgrupo desde publicaciones BOP oficiales.

Por defecto es SOLO_REVISION. Use --aplicar para persistir los valores encontrados.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bop_valencia import _grupo_subgrupo, _obtener_texto
from app.database import get_connection

import httpx


def extraer_grupo(url: str) -> tuple[str | None, str | None]:
    try:
        with httpx.Client(timeout=45, follow_redirects=True) as client:
            return _grupo_subgrupo(_obtener_texto(client, url))
    except Exception:
        return None, None


def revisar_grupos_bop_local(*, aplicar: bool = False, limite: int | None = None) -> dict[str, int | str]:
    """Audita oportunidades locales y completa solo clasificaciones explícitas en BOP."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT p.id, p.denominacion, pub.url
            FROM procesos p
            JOIN organismos o ON o.id = p.organismo_id
            JOIN publicaciones pub ON pub.proceso_id = p.id
            WHERE p.es_oportunidad = TRUE
              AND (p.grupo IS NULL OR p.subgrupo IS NULL)
              AND o.tipo IN ('AYUNTAMIENTO', 'DIPUTACION')
              AND LOWER(COALESCE(o.provincia, '')) IN
                  ('valencia', 'alicante', 'castellón', 'castellon')
              AND UPPER(COALESCE(pub.tipo, '')) = 'BOP'
              AND pub.url IS NOT NULL
            ORDER BY p.id, pub.url
            """
        )
        filas = cur.fetchall()

        procesos = {}
        for proceso_id, denominacion, url in filas:
            entrada = procesos.setdefault(proceso_id, {"denominacion": denominacion, "urls": []})
            if url not in entrada["urls"]:
                entrada["urls"].append(url)

        items = list(procesos.items())
        if limite:
            items = items[:limite]

        encontrados = 0
        for proceso_id, datos in items:
            grupo = subgrupo = None
            for url in datos["urls"]:
                grupo, subgrupo = extraer_grupo(url)
                if grupo or subgrupo:
                    break
            if not (grupo or subgrupo):
                continue
            encontrados += 1
            print(f"{proceso_id} | {subgrupo or grupo} | {datos['denominacion']}")
            if aplicar:
                cur.execute(
                    """
                    UPDATE procesos
                    SET grupo = COALESCE(grupo, %s),
                        subgrupo = COALESCE(subgrupo, %s),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (grupo, subgrupo, proceso_id),
                )

        if aplicar:
            conn.commit()
        else:
            conn.rollback()

        cur.close()
        resultado = {"revisadas": len(items), "clasificadas": encontrados, "modo": "APLICAR" if aplicar else "SOLO_REVISION"}
        print(f"Revisadas: {len(items)} | Clasificadas: {encontrados} | Modo: {resultado['modo']}")
        return resultado

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--limite", type=int)
    args = parser.parse_args()
    revisar_grupos_bop_local(aplicar=args.aplicar, limite=args.limite)


if __name__ == "__main__":
    main()
