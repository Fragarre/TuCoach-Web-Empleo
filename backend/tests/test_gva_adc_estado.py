from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gva_adc import (
    _es_promocion_interna,
    _estado_proceso_adc,
    _extraer_bolsas_texto_oficial,
    _numero_adc,
    _persistir_relaciones_adc_bolsas,
    _relacion_bolsas_efectiva,
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
    def test_extrae_listado_estructurado_de_bolsas_adc(self) -> None:
        texto = """
        Únicamente podrán participar las personas integrantes de las siguientes bolsas que
        figuren en algún ámbito como disponibles:

        804-B. C1-01, Administrativo. Modalidad Estabilización.
        679-B. Cuerpo C1-01. Administrativo.
        435-B. Cuerpo C1-01 administrativos.
        332-B. Cuerpo C1-01, administrativos PI.
        241-B. Cuerpo Administrativo C1-01.
        913-L. Cuerpo C1-01. Administrativos.
        914-L. Cuerpo C1-01. Administrativos.
        891-L. Cuerpo C1-01. Administrativos.
        890-L. Cuerpo C1-01. Administrativos.
        677-L. Cuerpo C1-01 administrativos.

        El plazo de presentación de solicitudes será el establecido.
        """
        self.assertEqual(
            _extraer_bolsas_texto_oficial(texto),
            [
                "241-B",
                "332-B",
                "435-B",
                "677-L",
                "679-B",
                "804-B",
                "890-L",
                "891-L",
                "913-L",
                "914-L",
            ],
        )

    def test_numero_adc_formato_estandar(self) -> None:
        self.assertEqual(_numero_adc("ADC 172/26 A1-01"), "172/26")

    def test_numero_adc_formato_edu_pas(self) -> None:
        self.assertEqual(
            _numero_adc("ADC-EDU-PAS 1/26 C1-01 Administrativo/a"),
            "1/26",
        )

    def test_conserva_bolsas_previas_si_etapa_actual_no_aporta_bolsas(self) -> None:
        bolsas, evidencia = _relacion_bolsas_efectiva(
            [],
            None,
            {
                "bolsas_relacionadas": ["241-B", "804-B", "913-L"],
                "evidencia_relacion": "PDF_OFICIAL",
            },
        )
        self.assertEqual(bolsas, ["241-B", "804-B", "913-L"])
        self.assertEqual(evidencia, "PDF_OFICIAL")

    def test_persistencia_relacion_resuelta_guarda_proceso_bolsa(self) -> None:
        cursor = MagicMock()

        _persistir_relaciones_adc_bolsas(
            cursor,
            adc_proceso_id=100,
            referencias=["804-B"],
            relaciones={
                "resueltas": {"804-B": 200},
                "ambiguas": {},
                "no_resueltas": [],
            },
            evidencia="PDF_OFICIAL",
        )

        self.assertEqual(cursor.execute.call_count, 1)
        parametros = cursor.execute.call_args.args[1]
        self.assertEqual(parametros, (100, "804-B", 200, "PDF_OFICIAL"))
        self.assertIn("ON CONFLICT", cursor.execute.call_args.args[0])

    def test_persistencia_relacion_no_resuelta_conserva_referencia(self) -> None:
        cursor = MagicMock()

        _persistir_relaciones_adc_bolsas(
            cursor,
            adc_proceso_id=100,
            referencias=["241-B"],
            relaciones={
                "resueltas": {},
                "ambiguas": {},
                "no_resueltas": ["241-B"],
            },
            evidencia="PDF_OFICIAL",
        )

        self.assertEqual(cursor.execute.call_count, 1)
        parametros = cursor.execute.call_args.args[1]
        self.assertEqual(parametros, (100, "241-B", None, "PDF_OFICIAL"))


if __name__ == "__main__":
    unittest.main()
