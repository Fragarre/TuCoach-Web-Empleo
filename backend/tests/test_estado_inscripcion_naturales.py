import importlib.util
from datetime import date
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)
_SPEC = importlib.util.spec_from_file_location("app.estado_proceso", APP_DIR / "estado_proceso.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
estado_inscripcion = _MODULE.estado_inscripcion


class PlazoNaturalEstadoTest(unittest.TestCase):
    def test_boe_veinte_dias_naturales(self):
        proceso = {
            "fecha_boe_publicacion": date(2026, 9, 28),
            "datos_json": {
                "boe_local": {
                    "plazo_solicitudes_literal": "El plazo de presentación de solicitudes será de veinte días naturales."
                }
            },
        }
        estado = estado_inscripcion(proceso, hoy=date(2026, 10, 3))
        self.assertEqual(estado["codigo"], "ABIERTO")
        self.assertEqual(estado["fecha_apertura"], date(2026, 9, 29))
        self.assertEqual(estado["fecha_cierre"], date(2026, 10, 18))


if __name__ == "__main__":
    unittest.main()
