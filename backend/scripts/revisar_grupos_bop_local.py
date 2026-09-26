"""Revisa y, opcionalmente, completa grupo/subgrupo desde la convocatoria oficial.

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
    """Audita oportunidades locales sin grupo/subgrupo y completa solo datos confirmados."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
        """
        SELECT p.id, p.denominacion, p.datos_json->>'url_oficial'
        FROM procesos p
        WHERE p.es_oportunidad = TRUE
          AND p.grupo IS NULL
          AND p.subgrupo IS NULL
          AND p.datos_json->>'url_oficial' IS NOT NULL
          AND (
              p.codigo_externo LIKE 'BOPV-%'
              OR p.codigo_externo LIKE 'BOPA-%'
              OR p.codigo_externo LIKE 'BOPCS-%'
              OR p.datos_json->>'provincia' IN ('Valencia', 'Alicante', 'Castellón', 'Castellon')
          )
        ORDER BY p.id
        """
    )
        filas = cur.fetchall()
        if limite:
            filas = filas[:limite]

        encontrados = 0
        for proceso_id, denominacion, url in filas:
            grupo, subgrupo = extraer_grupo(url)
            if not (grupo or subgrupo):
                continue
            encontrados += 1
            print(f"{proceso_id} | {subgrupo or grupo} | {denominacion}")
            if aplicar:
                cur.execute(
                    """
                    UPDATE procesos
                    SET grupo = COALESCE(%s, grupo),
                        subgrupo = COALESCE(%s, subgrupo),
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
        resultado = {"revisadas": len(filas), "clasificadas": encontrados, "modo": "APLICAR" if aplicar else "SOLO_REVISION"}
        print(f"Revisadas: {len(filas)} | Clasificadas: {encontrados} | Modo: {resultado['modo']}")
        return resultado


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--limite", type=int)
    args = parser.parse_args()
    revisar_grupos_bop_local(aplicar=args.aplicar, limite=args.limite)


if __name__ == "__main__":
    main()
