from contextlib import contextmanager

from app import gva_adc


class _Resp:
    text = "<html></html>"


def test_inventario_no_descarga_fichas_de_adc_terminales(monkeypatch):
    pedidas = []

    @contextmanager
    def cliente():
        yield object()

    monkeypatch.setattr(gva_adc, "nuevo_cliente", cliente)
    monkeypatch.setattr(gva_adc, "CODIGOS_ADMIN_ESTRICTOS", ["X"])
    monkeypatch.setattr(
        gva_adc, "_descubrir_por_codigo",
        lambda client, codigo: {1: "https://sede/1", 2: "https://sede/2", 3: "https://sede/3"},
    )

    def get(client, url, intentos=1):
        pedidas.append(url)
        return _Resp()

    monkeypatch.setattr(gva_adc, "_get_gva_con_reintentos", get)
    monkeypatch.setattr(
        gva_adc, "_clasificar",
        lambda client, id_emp, url, html: {"id_emp": id_emp, "valido_empleo": True, "bolsas_relacionadas": []},
    )

    r = gva_adc.inventariar_adc_gva(omitir_id_emp={1, 3})
    assert pedidas == ["https://sede/2"]
    assert r["descubiertos"] == 3
    assert r["omitidos_terminales"] == 2
    assert r["validos_empleo"] == 1


def test_inventario_sin_omitidos_descarga_todas(monkeypatch):
    pedidas = []

    @contextmanager
    def cliente():
        yield object()

    monkeypatch.setattr(gva_adc, "nuevo_cliente", cliente)
    monkeypatch.setattr(gva_adc, "CODIGOS_ADMIN_ESTRICTOS", ["X"])
    monkeypatch.setattr(gva_adc, "_descubrir_por_codigo", lambda c, k: {1: "u1", 2: "u2"})
    monkeypatch.setattr(gva_adc, "_get_gva_con_reintentos", lambda c, u, intentos=1: pedidas.append(u) or _Resp())
    monkeypatch.setattr(gva_adc, "_clasificar", lambda c, i, u, h: {"id_emp": i, "valido_empleo": True, "bolsas_relacionadas": []})
    gva_adc.inventariar_adc_gva()
    assert pedidas == ["u1", "u2"]
