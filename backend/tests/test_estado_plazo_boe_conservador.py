import importlib.util
from datetime import date
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1] / "app" / "estado_proceso.py"
_SPEC = importlib.util.spec_from_file_location("estado_proceso", APP)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class EstadoPlazoBoeConservadorTest(unittest.TestCase):
    def test_dias_naturales_calcula_cierre(self):
        r = _MODULE._estado_plazo_boe(
            fecha_boe=date(2026, 9, 28),
            literal="El plazo para presentar solicitudes será de veinte días naturales.",
            hoy=date(2026, 10, 3),
            organismo="Ayuntamiento de ejemplo",
        )
        self.assertEqual(r["codigo"], "ABIERTO")
        self.assertEqual(r["fecha_apertura"], date(2026, 9, 29))
        self.assertEqual(r["fecha_cierre"], date(2026, 10, 18))

    def test_dias_habiles_no_inventa_cierre_sin_calendario_local(self):
        r = _MODULE._estado_plazo_boe(
            fecha_boe=date(2026, 9, 28),
            literal="El plazo para presentar solicitudes será de veinte días hábiles.",
            hoy=date(2026, 10, 3),
            organismo="Ayuntamiento de ejemplo",
        )
        self.assertEqual(r["codigo"], "PLAZO_LITERAL")
        self.assertEqual(r["fecha_referencia"], date(2026, 9, 29))
        self.assertEqual(r["dias_habiles"], 20)
        self.assertNotIn("fecha_cierre", r)


if __name__ == "__main__":
    unittest.main()
