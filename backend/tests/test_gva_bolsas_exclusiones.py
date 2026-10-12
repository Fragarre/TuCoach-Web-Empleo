from app.gva_bolsas_complementarias import motivo_exclusion_texto


def test_acto_unico_en_el_historial_de_etapas_no_excluye():
    ficha = "444-b cuerpo a1-01 administracion general listado de etapas adjudicacion acto unico telematico"
    assert motivo_exclusion_texto("444-b. cuerpo a1-01 administracion general", ficha) is None


def test_acto_unico_en_el_titulo_si_excluye():
    assert motivo_exclusion_texto("acto unico de llamamiento a1-01", "acto unico de llamamiento") == "acto unico"


def test_promocion_interna_en_los_requisitos_no_excluye_una_bolsa_mixta():
    ficha = "387-b cuerpo a1-01 requisitos convocatoria 22/15 turno libre y 23/15 turno de promocion interna"
    assert motivo_exclusion_texto("387-b. cuerpo a1-01 administracion general (oep-15)", ficha) is None


def test_bolsa_pi_se_detecta_por_el_titulo():
    assert motivo_exclusion_texto("332-b. cuerpo c1-01, administrativos. pi (ope-14)", "x") == "promocion interna"
    assert motivo_exclusion_texto("500-b. promocion interna c1-01", "x") == "promocion interna"


def test_la_palabra_pi_dentro_de_otra_no_excluye():
    assert motivo_exclusion_texto("a1-01 pisos tutelados administracion general", "x") is None


def test_dificil_cobertura_se_busca_en_toda_la_ficha():
    assert motivo_exclusion_texto("anuncio", "anuncio dificil cobertura c1-01") == "dificil cobertura"


def test_consulta_de_baremo_solo_en_titulo():
    assert motivo_exclusion_texto("bolsa c1-01", "etapas: consulta de baremo") is None
    assert motivo_exclusion_texto("consulta de baremo c1-01", "x") == "consulta de baremo"


def test_acceso_reservado_a_empleados_sin_libre_se_excluye():
    titulo = "241-b. cuerpo de administrativo de la administracion de la generalitat c1-01. acceso para funcionarios de carrera y personal contratado laboral del personal del consell"
    assert motivo_exclusion_texto(titulo, "x") == "no turno libre"


def test_mismo_texto_con_libre_si_entra():
    titulo = "325-b. cuerpo a1-01 administracion general, y funcionarios de carrera o laborales fijos (libre)"
    assert motivo_exclusion_texto(titulo, "x") is None
