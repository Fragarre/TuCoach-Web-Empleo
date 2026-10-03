import importlib.util
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1] / "app" / "ambito_administrativo.py"
_SPEC = importlib.util.spec_from_file_location("ambito_administrativo_perfiles", APP)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class AmbitoPerfilesNoAdministrativosTest(unittest.TestCase):
    def test_comercio_y_desarrollo_local_no_entran_por_administrativo_generico(self):
        casos = (
            "Técnico de Comercio y Desarrollo Local - administrativo",
            "Agente para el fomento de la innovación comercial administrativo",
            "Tècnic de comerç i desenvolupament local administratiu",
        )
        for denominacion in casos:
            with self.subTest(denominacion=denominacion):
                self.assertEqual(
                    _MODULE.clasificar_ambito_administrativo({"denominacion": denominacion}),
                    "NO",
                )


    def test_perfiles_tributarios_no_son_puestos_administrativos(self):
        casos = (
            "Oficial/a de Recaudación",
            "Agente tributario",
            "Técnico/a tributario",
            "Gestión tributaria",
            "Oficial de recaptació",
            "Gestió tributària",
        )
        for denominacion in casos:
            with self.subTest(denominacion=denominacion):
                self.assertEqual(
                    _MODULE.clasificar_ambito_administrativo({"denominacion": denominacion}),
                    "NO",
                )


if __name__ == "__main__":
    unittest.main()
