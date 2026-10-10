from __future__ import annotations

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "app" / "festivos.py"
_SPEC = importlib.util.spec_from_file_location("festivos", _PATH)
assert _SPEC and _SPEC.loader
fe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fe)


class FestivosTest(unittest.TestCase):
    def test_pascua_conocida(self) -> None:
        self.assertEqual(fe.pascua(2026), date(2026, 4, 5))
        self.assertEqual(fe.pascua(2027), date(2027, 3, 28))
        self.assertEqual(fe.pascua(2028), date(2028, 4, 16))

    def test_reglas_reproducen_calendario_oficial_2026(self) -> None:
        p = fe.pascua(2026)
        calculados = {date(2026, m, d) for m, d in fe._FIJOS}
        calculados |= {p - timedelta(days=2), p + timedelta(days=1)}
        # El 1-nov-2026 es domingo: irrelevante para días hábiles.
        self.assertEqual(calculados - fe.CALENDARIOS_VERIFICADOS[2026], {date(2026, 11, 1)})
        self.assertEqual(fe.CALENDARIOS_VERIFICADOS[2026] - calculados, set())

    def test_anios_sin_verificar_tienen_calendario(self) -> None:
        for anio in (2027, 2028, 2030):
            self.assertIn(date(anio, 1, 1), fe.festivos_cv(anio))
            self.assertFalse(fe.calendario_verificado(anio))
        self.assertTrue(fe.calendario_verificado(2026))

    def test_plazo_que_cruza_de_anio(self) -> None:
        # Antes de la corrección daba 2027-01-08 (sin festivos de 2027).
        self.assertEqual(fe.sumar_dias_habiles(date(2026, 12, 10), 20), date(2027, 1, 12))

    def test_plazo_enero_2027_ya_se_calcula(self) -> None:
        self.assertEqual(fe.sumar_dias_habiles(date(2027, 1, 15), 20), date(2027, 2, 12))

    def test_plazo_normal_2026_no_cambia(self) -> None:
        self.assertEqual(fe.sumar_dias_habiles(date(2026, 10, 1), 20), date(2026, 11, 2))

    def test_dias_invalidos(self) -> None:
        with self.assertRaises(ValueError):
            fe.sumar_dias_habiles(date(2026, 1, 1), 0)

    def test_hoy_es_devuelve_fecha(self) -> None:
        self.assertIsInstance(fe.hoy_es(), date)


if __name__ == "__main__":
    unittest.main()
