import importlib.util
from pathlib import Path
import unittest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "clasificacion_puesto.py"
_SPEC = importlib.util.spec_from_file_location("clasificacion_puesto", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
extraer_clasificacion_declarada = _MODULE.extraer_clasificacion_declarada


class ClasificacionPuestoTests(unittest.TestCase):
    def test_extrae_campos_explicitos_en_castellano(self) -> None:
        texto = """
        Denominación: ADMINISTRATIVA/O.
        Grupo: C.
        Subgrupo: C1.
        Escala: Administración General.
        """
        self.assertEqual(
            extraer_clasificacion_declarada(texto),
            {"grupo": "C", "subgrupo": "C1", "cuerpo_escala": "Administración General"},
        )

    def test_extrae_campos_explicitos_en_valenciano(self) -> None:
        texto = "Grup: C / Subgrup: C2. Escala: Administració General."
        self.assertEqual(
            extraer_clasificacion_declarada(texto),
            {"grupo": "C", "subgrupo": "C2", "cuerpo_escala": "Administración General"},
        )

    def test_no_infiere_clasificacion_desde_la_denominacion(self) -> None:
        self.assertEqual(
            extraer_clasificacion_declarada("Dos plazas de auxiliar administrativa C2."),
            {"grupo": None, "subgrupo": None, "cuerpo_escala": None},
        )

    def test_extrae_etiquetas_valencianas(self) -> None:
        self.assertEqual(
            extraer_clasificacion_declarada("Grup: C. Subgrup: C1. Escala: Administració General."),
            {"grupo": "C", "subgrupo": "C1", "cuerpo_escala": "Administración General"},
        )

    def test_extrae_redaccion_valenciana_de_bases(self) -> None:
        texto = (
            "enquadrada en l’escala d’administració general, subescala administrativa, "
            "subgrup C1, les funcions a realitzar seran les definides"
        )
        self.assertEqual(
            extraer_clasificacion_declarada(texto),
            {"grupo": "C", "subgrupo": "C1", "cuerpo_escala": "Administración General"},
        )

    def test_acepta_grupo_sin_subgrupo(self) -> None:
        self.assertEqual(
            extraer_clasificacion_declarada("Grupo: A. Escala: Administración Especial."),
            {"grupo": "A", "subgrupo": None, "cuerpo_escala": "Administración Especial"},
        )


if __name__ == "__main__":
    unittest.main()
