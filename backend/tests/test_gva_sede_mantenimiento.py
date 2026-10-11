import httpx
import pytest

from app import gva_estatal_service as svc


class _Cliente:
    def __init__(self, textos):
        self.textos = list(textos)

    def get(self, url, params=None):
        texto = self.textos.pop(0) if len(self.textos) > 1 else self.textos[0]
        return httpx.Response(200, text=texto, request=httpx.Request("GET", url))


def test_detecta_pagina_de_mantenimiento():
    assert svc.sede_fuera_de_servicio("<html><h1>Aplicación fuera de servicio</h1></html>")
    assert not svc.sede_fuera_de_servicio("<a href='x?id_emp=1'>Ficha</a> fuera de servicio")
    assert not svc.sede_fuera_de_servicio("<html>Listado normal</html>")


def test_detecta_pagina_de_mantenimiento_grande_real():
    relleno = "<div class='plantilla'>" + ("x " * 17000) + "</div>"
    html = "<html><body><h1>\ufeff Aplicación fuera de servicio</h1><p>Aquesta aplicació està temporalment fora de servei</p>" + relleno + "</body></html>"
    assert len(html) > 30000
    assert svc.sede_fuera_de_servicio(html)


def test_ficha_normal_que_menciona_el_texto_al_final_no_es_mantenimiento():
    html = "<html><body><h1>Bolsa de trabajo Educación</h1>" + ("texto " * 100) + "<p>aviso: servicio fuera de servicio un dia</p></body></html>"
    assert not svc.sede_fuera_de_servicio(html)


def test_reintenta_y_se_recupera(monkeypatch):
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    c = _Cliente(["<h1>Aplicación fuera de servicio</h1>", "<html>ok</html>"])
    assert "ok" in svc._get_gva_con_reintentos(c, "https://sede.gva.es/x").text


def test_mantenimiento_persistente_lanza_error_de_red(monkeypatch):
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    c = _Cliente(["<h1>Aplicación fuera de servicio</h1>"])
    with pytest.raises(httpx.TransportError):
        svc._get_gva_con_reintentos(c, "https://sede.gva.es/x")
