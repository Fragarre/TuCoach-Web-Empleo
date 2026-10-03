from pathlib import Path
import importlib.util
import unittest

APP = Path(__file__).resolve().parents[1] / "app" / "bop_valencia.py"
_SPEC = importlib.util.spec_from_file_location("bop_valencia_filtro_provision", APP)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


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
