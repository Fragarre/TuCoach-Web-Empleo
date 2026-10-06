from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gva_adc import (
    _es_promocion_interna,
    _estado_proceso_adc,
    _extraer_bolsas_texto_oficial,
)


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

    def test_extrae_bolsa_singular_de_adjudicacion(self) -> None:
        texto = "A1-01. Cuerpo Superior de Administración (Val, Al, Cas). Bolsa 444"
        self.assertEqual(_extraer_bolsas_texto_oficial(texto), ["444"])

    def test_extrae_borsa_singular_en_valenciano(self) -> None:
        texto = "A1-01. Cos Superior d'Administració (Val, Al, Cas). Borsa 444"
        self.assertEqual(_extraer_bolsas_texto_oficial(texto), ["444"])

    def test_extrae_lista_plural_de_bolsas(self) -> None:
        texto = "Bolsas: 241, 332, 435, 677, 679, 804, 890, 891, 913, 914\nTexto posterior"
        self.assertEqual(
            _extraer_bolsas_texto_oficial(texto),
            ["241", "332", "435", "677", "679", "804", "890", "891", "913", "914"],
        )


if __name__ == "__main__":
    unittest.main()
