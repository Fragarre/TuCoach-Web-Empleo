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

    def test_mantiene_oposicion_administrativa(self):
        self.assertTrue(_MODULE._incluido("Convocatoria oposición libre para 66 plazas de Administrativo/a"))


if __name__ == "__main__":
    unittest.main()
