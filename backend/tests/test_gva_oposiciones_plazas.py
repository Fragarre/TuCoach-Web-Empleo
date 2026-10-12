from app.gva_oposiciones_sede import extraer_plazas_totales


def test_formato_a_num_de_plazas_totales():
    t = "Plazas Núm. de plazas totales 29 Distribución de plazas Turno libre 26 Discapacidad 2"
    assert extraer_plazas_totales(t) == 29


def test_formato_b_numero_plazas_totales():
    t = "Plazas 122 Descripción de plazas Nº plazas totales: 122 Libre general: 89 Discapacidad: 7"
    assert extraer_plazas_totales(t) == 122


def test_formato_b_solo_resumen():
    assert extraer_plazas_totales("Plazas 1.250 Descripción de plazas Libre 1.000") == 1250


def test_sin_plazas():
    assert extraer_plazas_totales("Sin datos de plazas") is None
