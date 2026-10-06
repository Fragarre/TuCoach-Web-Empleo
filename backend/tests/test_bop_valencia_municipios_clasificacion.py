from __future__ import annotations

import unittest

from app.bop_valencia_municipios import _clasificar_anuncio


class ClasificacionMunicipalBopTest(unittest.TestCase):
    def test_anuncio_dificil_cobertura_es_candidato_independiente(self) -> None:
        self.assertEqual(
            _clasificar_anuncio(
                "Ayuntamiento de X. Anuncio de difícil cobertura de auxiliar administrativo"
            ),
            "ANUNCIO_DIFICIL_COBERTURA",
        )

    def test_promocion_interna_sigue_excluida(self) -> None:
        self.assertEqual(
            _clasificar_anuncio(
                "Ayuntamiento de X. Promoción interna de administrativo"
            ),
            "EXCLUIDO_INTERNO",
        )

    def test_convocatoria_mixta_con_turno_libre_no_se_excluye(self) -> None:
        self.assertEqual(
            _clasificar_anuncio(
                "Bases de la convocatoria de auxiliar administrativo: turno libre y promoción interna"
            ),
            "NUEVA_CONVOCATORIA",
        )

