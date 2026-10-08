"""Regresión: códigos de convocatoria reutilizados para provisión de puestos."""

from app import bop_valencia_integrity as integridad


def test_nombramiento_jefatura_no_se_asocia_a_oposicion_06_26():
    titulo = (
        "Nomenament per al lloc de treball cap d'unitat d'administració, "
        "incardinat en el centre Gabinet de Presidència. Convocatòria 06/26."
    )
    assert integridad._es_provision_puesto(titulo)
    assert not integridad._es_convocatoria_base(titulo, "")
    assert integridad._identificador_estable(titulo, "") != "DVAL:06/26"
    assert integridad._identificador_estable(titulo, "").startswith("DVAL:T:")


def test_seguimiento_selectivo_no_se_confunde_con_provision():
    titulo = "Relació definitiva de persones admeses al procés selectiu de professorat. Convocatòria 06/26."
    assert not integridad._es_provision_puesto(titulo)


def test_provision_en_castellano():
    titulo = "Nombramiento para el puesto de trabajo de jefatura de unidad. Convocatoria 06/26."
    assert integridad._es_provision_puesto(titulo)
    assert integridad._identificador_estable(titulo, "") != "DVAL:06/26"
