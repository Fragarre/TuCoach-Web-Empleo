from __future__ import annotations

"""Auditoría de solo lectura para bolsas administrativas de la Sede GVA.

No escribe en PostgreSQL, no llama a persistencia y no genera notificaciones.
Sirve para validar el hueco de descubrimiento antes de modificar el importador.
"""

import argparse
import re
import unicodedata

from bs4 import BeautifulSoup

from app.gva_estatal_service import (
    _descubrir_detalles_para_resolver,
    _get_gva_con_reintentos,
)
from app.gva_estatal_source import nuevo_cliente

CODIGOS_ADMIN_ESTRICTOS = ("A1-01", "A2-01", "C1-01", "C2-01")
EXCLUSIONES = (
    "promocion interna",
    "promocio interna",
    "dificil cobertura",
    "difícil cobertura",
    "acto unico",
    "acte unic",
    "consulta de baremo",
    "consulta de estados",
    "cesion de integrantes",
    "cessio d'integrants",
)


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn").lower()


def clasificar_ficha_html(id_emp: int, url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    normalizado = _sin_acentos(texto)
    codigos = [c for c in CODIGOS_ADMIN_ESTRICTOS if c.lower() in normalizado]

    es_bolsa = bool(re.search(r"\bbolsa(?: de (?:trabajo|empleo))?\b", normalizado))
    exclusion = next(
        (x for x in EXCLUSIONES if _sin_acentos(x) in normalizado),
        None,
    )
    return {
        "id_emp": id_emp,
        "url": url,
        "codigos": codigos,
        "es_bolsa": es_bolsa,
        "exclusion": exclusion,
        "candidata": bool(codigos and es_bolsa and exclusion is None),
    }


def auditar(ids_objetivo: set[int], max_paginas: int = 10) -> dict:
    with nuevo_cliente() as client:
        detalles, errores = _descubrir_detalles_para_resolver(
            client, max_paginas=max_paginas
        )
        encontrados = {id_emp: url for id_emp, url in detalles}
        resultados = []

        for id_emp in sorted(ids_objetivo):
            url = encontrados.get(id_emp)
            if not url:
                resultados.append({
                    "id_emp": id_emp,
                    "en_listado": False,
                    "candidata": False,
                    "motivo": "no_aparece_en_listado_gva",
                })
                continue

            respuesta = _get_gva_con_reintentos(client, url, intentos=1)
            fila = clasificar_ficha_html(id_emp, url, respuesta.text)
            fila["en_listado"] = True
            resultados.append(fila)

    return {
        "modo": "SOLO_LECTURA",
        "paginas_revisadas": max_paginas,
        "errores_red": errores,
        "resultados": resultados,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ids",
        nargs="+",
        type=int,
        default=[113884, 113885],
        help="id_emp GVA a comprobar",
    )
    parser.add_argument("--max-paginas", type=int, default=10)
    args = parser.parse_args()

    resultado = auditar(set(args.ids), max_paginas=args.max_paginas)
    print("MODO:", resultado["modo"])
    print("ERRORES_RED:", len(resultado["errores_red"]))
    for fila in resultado["resultados"]:
        print(
            fila["id_emp"],
            "LISTADO=" + str(fila.get("en_listado")),
            "CANDIDATA=" + str(fila.get("candidata")),
            "CODIGOS=" + ",".join(fila.get("codigos") or []),
            "EXCLUSION=" + str(fila.get("exclusion")),
            fila.get("motivo") or "",
        )


if __name__ == "__main__":
    main()
