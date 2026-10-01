from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.gva_adc import inventariar_adc_gva
from app.gva_estatal_service import _get_gva_con_reintentos
from app.gva_estatal_source import nuevo_cliente
from app import gva_clean


def _diagnosticar_ficha(id_emp: int) -> dict:
    url = f"{gva_clean.GVA_BASE_URL}/detall-ocupacio-publica?id_emp={id_emp}"
    with nuevo_cliente() as client:
        respuesta = _get_gva_con_reintentos(client, url, intentos=1)
    soup = BeautifulSoup(respuesta.text, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    fragmentos = []
    for patron in (r".{0,100}plazo.{0,180}", r".{0,100}termini.{0,180}", r".{0,100}fecha.{0,180}", r".{0,100}data.{0,180}"):
        fragmentos.extend(re.findall(patron, texto, re.I))
    enlaces = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        etiqueta = " ".join(a.get_text(" ", strip=True).split())
        combinado = f"{etiqueta} {href}".lower()
        if any(x in combinado for x in (".pdf", "document", "resol", "adjud", "listado", "llistat", "anuncio", "anunci")):
            enlaces.append({"texto": etiqueta, "url": urljoin(gva_clean.GVA_BASE_URL, href)})
    return {
        "id_emp": id_emp,
        "url": url,
        "fragmentos_fecha_plazo": list(dict.fromkeys(fragmentos))[:30],
        "enlaces_documentales": enlaces[:50],
    }


def main() -> None:
    resultado = inventariar_adc_gva()
    resultado["diagnostico_fichas"] = [
        _diagnosticar_ficha(113072),
        _diagnosticar_ficha(114059),
    ]
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
