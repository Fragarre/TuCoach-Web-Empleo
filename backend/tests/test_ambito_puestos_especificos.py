import importlib.util
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1] / "app" / "ambito_administrativo.py"
_SPEC = importlib.util.spec_from_file_location("ambito_administrativo_especificos", APP)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
clasificar = _MODULE.clasificar_ambito_administrativo


class AmbitoPuestosEspecificosTest(unittest.TestCase):
    def test_puestos_tecnicos_especificos_no_entran(self):
        casos = (
            "Técnico/a de Gestión de Fiestas",
            "Técnico/a medio/a de relaciones laborales",
            "Tècnic de relacions laborals",
            "Tècnic/a de festes",
            "Técnico/a de Tesorería",
            "Tècnic de tresoreria",
            "Administrativo/Administrativa Servicios Sociales de la plantilla de personal laboral fijo",
            "Informador-a tramitador-a",
            "Informador/a tramitador/a",
        )
        for denominacion in casos:
            with self.subTest(denominacion=denominacion):
                self.assertEqual(clasificar({"denominacion": denominacion}), "NO")

    def test_escala_administracion_especial_no_entra_aunque_diga_administrativo(self):
        self.assertEqual(
            clasificar({
                "denominacion": "Técnico/a de Gestión de Fiestas",
                "contexto": "Una plaza de Técnico/a de Gestión de Fiestas, escala de Administración Especial, subescala técnica, procedimiento administrativo",
            }),
            "NO",
        )

    def test_tag_adscrito_a_tesoreria_sigue_entrando(self):
        self.assertEqual(
            clasificar({
                "denominacion": "Técnico de Administración General",
                "contexto": "Una plaza de Técnico de Administración General, escala de Administración General, adscrita al área de Tesorería",
            }),
            "SI",
        )

    def test_tecnico_de_gestion_de_administracion_general_entra_por_contexto(self):
        self.assertEqual(
            clasificar({
                "denominacion": "Técnico/a de Gestión",
                "contexto": "Dos plazas de Técnico/a de Gestión, escala de Administración General, subescala de gestión",
            }),
            "SI",
        )

    def test_mencion_a_personal_laboral_en_el_anuncio_no_retira_plaza_funcionarial(self):
        self.assertEqual(
            clasificar({
                "denominacion": "Administrativo/a",
                "contexto": "Una plaza de Administrativo/a, escala de Administración General. Podrá participar el personal funcionario o laboral",
            }),
            "SI",
        )

    def test_puestos_administrativos_habituales_siguen_entrando(self):
        casos = (
            "Administrativo/a",
            "Auxiliar administrativo/a",
            "Técnico/a de Administración General",
            "Auxiliar de Administración General",
            "Administrativo/a de Administración General",
        )
        for denominacion in casos:
            with self.subTest(denominacion=denominacion):
                self.assertEqual(clasificar({"denominacion": denominacion}), "SI")


if __name__ == "__main__":
    unittest.main()
