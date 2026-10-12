from app import gva_adc


class _Cursor:
    def __init__(self, filas):
        self.filas = filas
        self.patron = None

    def execute(self, sql, params):
        self.patron = params[0]

    def fetchall(self):
        return self.filas


def test_referencia_con_barra_se_enlaza_si_hay_una_bolsa_con_b_ref():
    r = gva_adc._resolver_bolsas_relacionadas(_Cursor([{"id": 81}]), ["500/22"])
    assert r["resueltas"] == {"500/22": 81}
    assert r["no_resueltas"] == []


def test_referencia_con_barra_sin_bolsa_sigue_sin_resolverse():
    r = gva_adc._resolver_bolsas_relacionadas(_Cursor([]), ["500/22"])
    assert r["resueltas"] == {}
    assert r["no_resueltas"] == ["500/22"]


def test_referencia_con_barra_ambigua_no_se_enlaza():
    r = gva_adc._resolver_bolsas_relacionadas(_Cursor([{"id": 1}, {"id": 2}]), ["500/22"])
    assert r["resueltas"] == {}
    assert r["ambiguas"] == {"500/22": [1, 2]}


def test_patron_exige_b_guion_referencia_exacta():
    import re

    c = _Cursor([])
    gva_adc._resolver_bolsas_relacionadas(c, ["500/22"])
    patron = c.patron
    assert re.search(patron, "Bolsa específica LABORA (B-500/22)", re.I)
    assert not re.search(patron, "Bolsa 1500/22 otra", re.I)
    assert not re.search(patron, "Bolsa 500/22 sin prefijo", re.I)
