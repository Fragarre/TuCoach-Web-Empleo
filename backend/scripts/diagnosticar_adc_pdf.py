"""Diagnóstico (solo lectura) de por qué un ADC no obtiene bolsas del PDF.

Uso:  $env:PYTHONPATH="."; python scripts/diagnosticar_adc_pdf.py 114380 114052 109515
(cada número es el id_emp de la ficha GVA). No escribe en la base de datos.
"""
from __future__ import annotations

import io
import re
import sys

from bs4 import BeautifulSoup
from pypdf import PdfReader

from app import gva_clean
from app.gva_adc import _datos_etapas, _extraer_bolsas_texto_oficial, _sin_acentos
from app.gva_estatal_service import _get_gva_con_reintentos
from app.gva_estatal_source import nuevo_cliente


def diagnosticar(id_emp: int) -> None:
    url = f"{gva_clean.GVA_BASE_URL}/detall-ocupacio-publica?id_emp={id_emp}"
    print(f"\n=== id_emp {id_emp} ===")
    with nuevo_cliente() as client:
        try:
            ficha = _get_gva_con_reintentos(client, url, intentos=1)
        except Exception as exc:
            print(f"  FICHA: error {type(exc).__name__}: {exc}")
            return
        soup = BeautifulSoup(ficha.text, "html.parser")
        texto = " ".join(soup.get_text(" ", strip=True).split())
        documentos = _datos_etapas(soup, texto)["documentos_pdf"]
        print(f"  documentos_pdf: {len(documentos)}")
        for doc in documentos:
            print(f"  - {doc.get('url')}")
            try:
                r = _get_gva_con_reintentos(client, doc["url"], intentos=1)
            except Exception as exc:
                print(f"    DESCARGA: error {type(exc).__name__}: {exc}")
                continue
            tipo = r.headers.get("content-type")
            print(f"    http={r.status_code} tipo={tipo} bytes={len(r.content)} empieza={r.content[:5]!r}")
            try:
                lector = PdfReader(io.BytesIO(r.content))
                paginas = [(p.extract_text() or "") for p in lector.pages]
            except Exception as exc:
                print(f"    PDF ilegible: {type(exc).__name__}: {exc}")
                continue
            completo = "\n".join(paginas)
            print(f"    paginas={len(paginas)} caracteres={len(completo)}")
            n = _sin_acentos(completo)
            m = re.search(r"(seguents\s+borses|siguientes\s+bolsas|bolsas|borses)", n)
            if m:
                ini = max(0, m.start() - 80)
                print("    fragmento:", repr(completo[ini: m.end() + 300]))
            else:
                print("    no aparece 'bolsas'/'borses'. Inicio del texto:", repr(completo[:300]))
            print("    bolsas extraidas:", _extraer_bolsas_texto_oficial(completo))


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        diagnosticar(int(arg))
