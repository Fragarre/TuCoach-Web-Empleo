from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "gva_adc.py"

# El módulo usa imports relativos; se carga dentro del paquete app real.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gva_adc import _es_promocion_interna, _estado_proceso_adc


class EstadoProcesoAdcTest(unittest.TestCase):
    def test_promocion_interna_se_detecta_en_castellano_y_valenciano(self) -> None:
        self.assertTrue(_es_promocion_interna("Promoción interna"))
        self.assertTrue(_es_promocion_interna("Promoció interna"))

    def test_anulacion_castellano_es_terminal(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Anulación de anuncio difícil cobertura"),
            "ANULADO",
        )

    def test_anulacion_valenciano_es_terminal(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Anul·lació de l'anunci de difícil cobertura"),
            "ANULADO",
        )

    def test_adjudicacion_con_plazo_ya_cerrado_sigue_activa(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Adjudicación difícil cobertura"),
            "EN_CURSO",
        )

    def test_correccion_adjudicacion_sigue_activa(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Corrección errores de la adjudicación difícil cobertura"),
            "EN_CURSO",
        )


if __name__ == "__main__":
    unittest.main()
