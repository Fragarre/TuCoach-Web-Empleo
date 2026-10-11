from __future__ import annotations

import unittest

from app.bop_valencia import _tipo


class TipoProcesoBopValenciaTests(unittest.TestCase):
    BASES = (
        "Bases del proceso selectivo por el sistema de oposición libre. "
        "Se constituirá una bolsa de trabajo con los aspirantes que no obtengan plaza."
    )

    def test_bolsa_mencionada_solo_en_las_bases_no_convierte_la_oposicion_en_bolsa(self):
        titulo = ("Anuncio de la Diputación Provincial de Valencia sobre las bases del proceso "
                  "selectivo para veintiuna plazas de técnico/a superior. Convocatoria 05/26.")
        self.assertEqual(_tipo(self.BASES, titulo), "Oposición")

    def test_titulo_que_es_una_bolsa_sigue_siendo_bolsa(self):
        titulo = "Anuncio sobre la creación de una bolsa de trabajo de auxiliares administrativos."
        self.assertEqual(_tipo("texto cualquiera", titulo), "Bolsa de trabajo")

    def test_borsa_en_valenciano_en_el_titulo(self):
        self.assertEqual(_tipo("text", "Anunci sobre la borsa de treball d'administratius"), "Bolsa de trabajo")

    def test_sin_titulo_conserva_el_comportamiento_anterior(self):
        self.assertEqual(_tipo("Constitución de la bolsa de trabajo"), "Bolsa de trabajo")

    def test_concurso_oposicion_se_reconoce_aunque_las_bases_hablen_de_bolsa(self):
        bases = "Sistema de concurso-oposición. Se creará una bolsa de trabajo."
        self.assertEqual(_tipo(bases, "Anuncio sobre las bases de la convocatoria"), "Concurso-oposición")
