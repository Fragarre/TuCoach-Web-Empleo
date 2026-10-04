from app.procesos import es_proceso_privado


def test_es_proceso_privado_por_tipo_bolsa():
    assert es_proceso_privado({"tipo_proceso": "Bolsa de trabajo", "datos_json": {}})


def test_es_proceso_privado_por_tipo_adc():
    assert es_proceso_privado({"tipo_proceso": "Anuncio difícil cobertura (ADC)", "datos_json": {}})


def test_es_proceso_privado_por_categoria_bolsa():
    assert es_proceso_privado({"tipo_proceso": "Oposición", "datos_json": {"categoria_gva": "BOLSA"}})


def test_es_proceso_privado_por_categoria_adc():
    assert es_proceso_privado({"tipo_proceso": "Oposición", "datos_json": {"categoria_gva": "ADC"}})


def test_oposicion_ordinaria_no_es_privada():
    assert not es_proceso_privado({"tipo_proceso": "Oposición", "datos_json": {"categoria_gva": "OPOSICION"}})
