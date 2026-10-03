import importlib.util
from datetime import date
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconciliar_boe_historico.py"
_SPEC = importlib.util.spec_from_file_location("reconciliar_boe_historico", SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class VentanasHistoricasTest(unittest.TestCase):
    def test_ventanas_no_dejan_huecos(self):
        ventanas = list(_MODULE._ventanas(date(2026, 1, 1), date(2026, 3, 5), 30))
        self.assertEqual(ventanas[0], (date(2026, 1, 1), date(2026, 1, 30)))
        self.assertEqual(ventanas[1], (date(2026, 1, 31), date(2026, 3, 1)))
        self.assertEqual(ventanas[2], (date(2026, 3, 2), date(2026, 3, 5)))

    def test_fallback_exige_evidencia_de_bases(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'", texto)
        self.assertIn("COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'", texto)

    def test_incluye_fuentes_aval_y_dval(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("p.identificador_estable LIKE 'DVAL:%'", texto)
        self.assertIn("p.identificador_estable LIKE 'AVAL:%'", texto)


if __name__ == "__main__":
    unittest.main()
