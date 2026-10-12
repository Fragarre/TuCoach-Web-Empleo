from datetime import date

from app import gva_bolsas_complementarias as bc


class _Resp:
    def __init__(self, text):
        self.text = text


def _html(ids, total):
    enlaces = "".join(
        f'<div class="card"><a href="/es/detall-ocupacio-publica?id_emp={i}">x</a> bolsa</div>' for i in ids
    )
    return f"<html><body><p>{total} resultados</p>{enlaces}</body></html>"


def _sede_simulada(ids_por_anyo, total):
    """Sede con tope de 100: sin fechas (o rango amplio) devuelve solo los 100 primeros."""
    todos = [i for ids in ids_por_anyo.values() for i in ids]

    def get(client, url, params=None, intentos=3):
        desde = params.get("fechaPublicacionDesde")
        hasta = params.get("fechaPublicacionHasta")
        if not desde:
            ids = todos
        else:
            d, h = date.fromisoformat(desde), date.fromisoformat(hasta)
            ids = [i for a, lista in ids_por_anyo.items() if d <= date(a, 6, 1) <= h for i in lista]
        return _Resp(_html(ids[:100], len(ids)))

    return get


def test_divide_por_fechas_cuando_la_sede_trunca(monkeypatch):
    datos = {2022: list(range(1, 53)), 2025: list(range(100, 146)), 2026: list(range(200, 258))}
    monkeypatch.setattr(bc, "_get_gva_con_reintentos", _sede_simulada(datos, 156))
    r = bc.descubrir_paginado(object(), "A2-01", lambda enlace: True, hoy=date(2026, 10, 12))
    assert len(r) == 52 + 46 + 58


def test_una_sola_consulta_si_no_hay_truncado(monkeypatch):
    llamadas = []

    def get(client, url, params=None, intentos=3):
        llamadas.append(params)
        return _Resp(_html(range(1, 11), 10))

    monkeypatch.setattr(bc, "_get_gva_con_reintentos", get)
    assert len(bc.descubrir_paginado(object(), "X", lambda enlace: True)) == 10
    assert len(llamadas) == 1


def test_error_si_hay_demasiadas_consultas(monkeypatch):
    # Siempre truncado y siempre con ids nuevos: debe fallar en alto, no quedarse en bucle.
    def get(client, url, params=None, intentos=3):
        return _Resp(_html(range(1, 101), 500))

    monkeypatch.setattr(bc, "_get_gva_con_reintentos", get)
    import pytest

    with pytest.raises(RuntimeError):
        bc.descubrir_paginado(object(), "X", lambda enlace: True, hoy=date(2026, 10, 12))
