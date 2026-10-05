from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "revisar_oportunidades_activas.py"


class RevisionActivosScriptTest(unittest.TestCase):
    def test_modo_por_defecto_no_aplica(self) -> None:
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("aplicar: bool = False", texto)
        self.assertIn('parser.add_argument("--aplicar", action="store_true")', texto)
        self.assertIn('"notificaciones": False', texto)

    def test_revisa_tres_familias_de_fuentes(self) -> None:
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("previsualizar_importacion_boe_local", texto)
        self.assertIn("_revisar_bop_valencia_diputacion", texto)
        self.assertIn("bootstrap_otras_entidades_alicante", texto)
        self.assertIn("importar_municipales_bop", texto)
        self.assertIn("importar_bop_castellon", texto)
        self.assertIn("importar_bop_alicante", texto)
        self.assertIn("importar_gva_estatal", texto)
        self.assertIn("persistir_bolsas_gva_complementarias", texto)
        self.assertIn("persistir_adc_gva", texto)
        self.assertLess(texto.index('"bop_valencia_municipios"'), texto.index('"boe_local_historico"'))

    def test_ventana_historica_por_defecto_es_180_dias(self) -> None:
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--dias", type=int, default=180)', texto)


if __name__ == "__main__":
    unittest.main()
