from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import bop_valencia as _MODULE


class BopValenciaFiltroProvisionTest(unittest.TestCase):
    def test_excluye_concurso_meritos_provision_puesto(self):
        casos = (
            "Anunci sobre concurs de mèrits per a la provisió de dos llocs de treball cap de Grup d'Oficina",
            "Anuncio sobre concurso de méritos para la provisión de un puesto de jefatura",
        )
        for titulo in casos:
            with self.subTest(titulo=titulo):
                self.assertFalse(_MODULE._incluido(titulo))

    def test_clasificacion_ambito_diputacion(self):
        administrativo = _MODULE.clasificar_ambito_administrativo(
            {
                "denominacion": "Convocatoria oposición libre para plazas de Administrativo/a",
                "cuerpo_escala": None,
                "grupo": None,
            }
        )
        no_administrativo = _MODULE.clasificar_ambito_administrativo(
            {
                "denominacion": "Convocatoria oposición libre para plazas de Arquitecto/a",
                "cuerpo_escala": None,
                "grupo": None,
            }
        )

        self.assertEqual(administrativo, "SI")
        self.assertEqual(no_administrativo, "NO")
    def test_mantiene_oposicion_administrativa(self):
        self.assertTrue(_MODULE._incluido("Convocatoria oposición libre para 66 plazas de Administrativo/a"))

    def test_mantiene_eventos_terminales_para_cerrar_convocatorias(self):
        casos = (
            "Nombramiento como funcionario de carrera de la convocatoria 05/26",
            "Desistimiento de la convocatoria 03/25 de plazas de Administrativo/a",
            "Anulación de la convocatoria 07/26",
        )
        for titulo in casos:
            with self.subTest(titulo=titulo):
                self.assertTrue(_MODULE._incluido(titulo))

    def test_promocion_interna_no_es_oportunidad_publica(self):
        self.assertFalse(_MODULE._es_oportunidad_administrativa("SI", "PROMOCION_INTERNA"))
        self.assertTrue(_MODULE._es_oportunidad_administrativa("SI", "TURNO_LIBRE"))
        self.assertFalse(_MODULE._es_oportunidad_administrativa("NO", "TURNO_LIBRE"))


if __name__ == "__main__":
    unittest.main()
