from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.periodic import _validar_dias_solape, ejecutar_periodico


class PeriodicHistoricoTest(unittest.TestCase):
    def test_ventana_ordinaria_no_supera_treinta_dias(self):
        with self.assertRaises(ValueError):
            _validar_dias_solape(dias_solape=31, historico=False)

    def test_ventana_historica_exige_modo_explicito_pero_no_tiene_tope(self):
        _validar_dias_solape(dias_solape=180, historico=True)

    def test_no_admite_ventanas_no_positivas(self):
        with self.assertRaises(ValueError):
            _validar_dias_solape(dias_solape=0, historico=True)

    def test_historico_aplicado_no_consulta_ni_envia_notificaciones(self):
        fuentes = {
            "importar_bop_valencia": lambda **_: {},
            "importar_municipales_bop": lambda **_: {},
            "importar_bop_castellon": lambda **_: {},
            "bootstrap_otras_entidades_alicante": lambda **_: {},
            "importar_bop_alicante": lambda **_: {},
            "_recuperar_boe_pendientes_activos": lambda **_: {},
            "previsualizar_importacion_boe_local": lambda **_: {},
            "importar_gva_estatal": lambda **_: {},
            "persistir_bolsas_gva_complementarias": lambda **_: {},
            "persistir_adc_gva": lambda **_: {},
            "persistir_cesiones_gva": lambda **_: {},
        }
        with patch.multiple("app.periodic", **fuentes), patch(
            "app.periodic.ids_oportunidades_visibles"
        ) as visibles, patch(
            "app.periodic.ids_novedades_seguimiento"
        ) as novedades, patch("app.periodic.enviar_envios_pendientes") as envio:
            resultado = ejecutar_periodico(aplicar=True, dias_solape=180, historico=True)

        self.assertFalse(resultado["notificaciones_habilitadas"])
        self.assertEqual(resultado["notificaciones_generales"], {"omitidas_modo_historico": True})
        self.assertEqual(
            set(resultado["duraciones_fuentes_segundos"]),
            {
                "bop_valencia_diputacion",
                "bop_valencia_municipios",
                "bop_castellon",
                "alicante_otras_entidades",
                "bop_alicante",
                "boe_pendientes_activos",
                "boe_local",
                "gva",
                "gva_bolsas_administrativas",
                "gva_adc",
                "gva_cesiones_datos",
            },
        )
        visibles.assert_not_called()
        novedades.assert_not_called()
        envio.assert_not_called()
