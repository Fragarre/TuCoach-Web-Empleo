"""Diagnóstico (solo lectura, sin base de datos) de bolsas GVA que no se descubren.

Uso:  $env:PYTHONPATH="."; python scripts/diagnosticar_bolsas_faltantes.py 241-B 332-B 444 234
Para cada referencia busca en la Sede (campo de texto) y lista las tarjetas devueltas.
Sin argumentos, comprueba además cuántas tarjetas devuelve cada código de búsqueda
(A1-01, A2-01, C1-01, C2-01) y si el listado llega al tope de 100 resultados.
"""
from __future__ import annotations

import re
import sys
import time

from bs4 import BeautifulSoup

from app import gva_clean
from app.gva_bolsas_complementarias import CODIGOS_ADMIN_ESTRICTOS, _es_tarjeta_bolsa, _id_emp, descubrir_paginado
from app.gva_estatal_service import _get_gva_con_reintentos
from app.gva_estatal_source import nuevo_cliente


def buscar(client, texto: str):
    r = _get_gva_con_reintentos(
        client,
        gva_clean.GVA_SEARCH_URL,
        params={"convocatoria": "1", "descripcion": texto, "pagina": "0",
                "tamanyoPagina": "100", "elementosPaginacion": "100"},
        intentos=1,
    )
    soup = BeautifulSoup(r.text, "html.parser")
    total = re.search(r"([\d.]+)\s+resultados?", " ".join(soup.get_text(" ", strip=True).split()))
    tarjetas = []
    for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
        ident = _id_emp(str(enlace.get("href") or ""))
        card = enlace.find_parent(class_=lambda v: v and "card" in str(v).split())
        texto_card = " ".join((card or enlace).get_text(" ", strip=True).split())
        if ident is not None:
            tarjetas.append((ident, _es_tarjeta_bolsa(enlace), texto_card))
    vistos = {}
    for t in tarjetas:
        vistos.setdefault(t[0], t)
    return (total.group(1) if total else "?"), list(vistos.values())


def main(refs: list[str]) -> None:
    with nuevo_cliente() as client:
        if not refs:
            for codigo in CODIGOS_ADMIN_ESTRICTOS:
                total, tarjetas = buscar(client, codigo)
                bolsas = [t for t in tarjetas if t[1]]
                print(f"código {codigo}: total_sede={total}, tarjetas={len(tarjetas)}, bolsas={len(bolsas)}"
                      + ("  <-- TOPE 100: puede haber bolsas sin descubrir" if len(tarjetas) >= 100 else ""))
                completas = descubrir_paginado(client, codigo, _es_tarjeta_bolsa)
                print(f"   con paginación: bolsas descubiertas={len(completas)}")
                time.sleep(1)
            return
        for ref in refs:
            total, tarjetas = buscar(client, ref)
            print(f"\n=== '{ref}': total_sede={total}, tarjetas={len(tarjetas)}")
            for ident, es_bolsa, texto in tarjetas[:8]:
                print(f"  id_emp={ident} bolsa={es_bolsa} :: {texto[:230]}")
            time.sleep(1)


if __name__ == "__main__":
    main(sys.argv[1:])
