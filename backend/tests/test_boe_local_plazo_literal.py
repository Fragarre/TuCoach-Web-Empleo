import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)
_SPEC = importlib.util.spec_from_file_location("app.boe_local_extractor", APP_DIR / "boe_local_extractor.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
extraer = _MODULE._extraer_plazo_literal


class PlazoLiteralBoeTest(unittest.TestCase):
    def test_formula_presentacion(self):
        texto = "El plazo de presentación de solicitudes será de veinte días naturales. Otra frase."
        self.assertEqual(extraer(texto), "El plazo de presentación de solicitudes será de veinte días naturales.")

    def test_formula_para_presentar(self):
        texto = "El plazo para presentar solicitudes será de veinte días hábiles. Otra frase."
        self.assertEqual(extraer(texto), "El plazo para presentar solicitudes será de veinte días hábiles.")

    def test_formula_solicitudes_se_presentaran(self):
        texto = "Las solicitudes se presentarán en el plazo de diez días naturales. Otra frase."
        self.assertEqual(extraer(texto), "Las solicitudes se presentarán en el plazo de diez días naturales.")


if __name__ == "__main__":
    unittest.main()
