from app.gva_bolsas_complementarias import motivo_exclusion_texto


def test_acto_unico_en_el_historial_de_etapas_no_excluye():
    ficha = "444-b cuerpo a1-01 administracion general listado de etapas adjudicacion acto unico telematico"
    assert motivo_exclusion_texto("444-b. cuerpo a1-01 administracion general", ficha) is None


def test_acto_unico_en_el_titulo_si_excluye():
    assert motivo_exclusion_texto("acto unico de llamamiento a1-01", "acto unico de llamamiento") == "acto unico"


def test_promocion_interna_se_busca_en_toda_la_ficha():
    ficha = "332-b cuerpo c1-01 requisitos convocatoria promocion interna 3/14"
    assert motivo_exclusion_texto("332-b. cuerpo c1-01", ficha) == "promocion interna"


def test_dificil_cobertura_se_busca_en_toda_la_ficha():
    assert motivo_exclusion_texto("anuncio", "anuncio dificil cobertura c1-01") == "dificil cobertura"


def test_consulta_de_baremo_solo_en_titulo():
    assert motivo_exclusion_texto("bolsa c1-01", "etapas: consulta de baremo") is None
    assert motivo_exclusion_texto("consulta de baremo c1-01", "x") == "consulta de baremo"
