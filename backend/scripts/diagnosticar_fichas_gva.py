"""Diagnóstico (solo lectura, sin base de datos) de acceso a fichas de la Sede GVA.

Uso:  $env:PYTHONPATH="."; python scripts/diagnosticar_fichas_gva.py 75625 94166 110369
(cada número es un id_emp). Muestra para cada ficha: código HTTP, tamaño, si es la
página de mantenimiento y si el parser lee la etapa. No escribe nada.
"""
from __future__ import annotations

import sys
import time

import httpx
from bs4 import BeautifulSoup

from app import gva_clean
from app.gva_bolsas_complementarias import _clasificar_detalle
from app.gva_estatal_service import sede_fuera_de_servicio
from app.gva_estatal_source import nuevo_cliente


def main(ids: list[int]) -> None:
    with nuevo_cliente() as client:
        for id_emp in ids:
            url = f"{gva_clean.GVA_BASE_URL}/es/detall-ocupacio-publica?id_emp={id_emp}"
            t0 = time.time()
            try:
                r = client.get(url)
            except Exception as exc:
                print(f"{id_emp}: ERROR RED {type(exc).__name__}: {exc} ({time.time()-t0:.1f}s)")
                continue
            texto = r.text
            titulo = " ".join(BeautifulSoup(texto, "html.parser").get_text(" ", strip=True).split())[:90]
            etapa = None
            if r.status_code == 200 and not sede_fuera_de_servicio(texto):
                try:
                    etapa = (_clasificar_detalle(id_emp, url, texto).get("datos_json") or {}).get("etapa_actual_gva")
                except Exception as exc:
                    etapa = f"parser: {type(exc).__name__}"
            print(
                f"{id_emp}: HTTP {r.status_code}, {len(texto)} bytes, {time.time()-t0:.1f}s, "
                f"mantenimiento={sede_fuera_de_servicio(texto)}, etapa={etapa!r}, inicio={titulo!r}"
            )
            time.sleep(1)


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
