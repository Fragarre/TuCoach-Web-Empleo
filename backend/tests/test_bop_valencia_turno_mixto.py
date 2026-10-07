from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import bop_valencia as _MODULE


TEXTO_MIXTO = (
    "05/26.- OPOSICIÓN LIBRE 21 PLAZAS TÉCNICO/A DE ADMINISTRACIÓN GENERAL "
    "(15 PLAZAS POR TURNO LIBRE Y 6 POR PROMOCIÓN INTERNA). CONVOCATORIA 05/26."
)


class BopValenciaTurnoMixtoTest(unittest.TestCase):
    def test_mixta_con_cuota_libre_es_oportunidad_publica(self):
        turno = _MODULE._turno(TEXTO_MIXTO)
        self.assertEqual(turno, "TURNO_LIBRE")
        self.assertTrue(_MODULE._es_oportunidad_administrativa("SI", turno))

    def test_mixta_muestra_solo_plazas_libres_y_conserva_desglose(self):
        self.assertEqual(_MODULE._plazas_turno_libre(TEXTO_MIXTO), 15)
        self.assertEqual(_MODULE._plazas_catalogo(TEXTO_MIXTO, "TURNO_LIBRE"), 15)
        self.assertEqual(
            _MODULE._desglose_plazas(TEXTO_MIXTO),
            {"plazas_turno_libre": 15, "plazas_promocion_interna": 6},
        )

    def test_extrae_convocatoria_con_dos_puntos(self):
        self.assertEqual(
            _MODULE._convocatoria("Designación del tribunal. Convocatoria: 06/25."),
            "06/25",
        )

    def test_seguimiento_no_es_convocatoria_base(self):
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Relación definitiva de admitidos. Convocatoria 53/21.",
                "Fecha de examen del proceso selectivo.",
            )
        )
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Designación de miembros del órgano técnico. Convocatoria: 06/25.",
                "",
            )
        )
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Nombramiento como funcionario de carrera. Convocatoria 36/20.",
                "",
            )
        )

    def test_solo_promocion_interna_sigue_excluida(self):
        turno = _MODULE._turno("Convocatoria de 6 plazas por promoción interna.")
        self.assertEqual(turno, "PROMOCION_INTERNA")
        self.assertFalse(_MODULE._es_oportunidad_administrativa("SI", turno))
