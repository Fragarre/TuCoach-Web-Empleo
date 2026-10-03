import importlib.util
from pathlib import Path
import unittest


APP = Path(__file__).resolve().parents[1] / "app" / "ambito_administrativo.py"
_SPEC = importlib.util.spec_from_file_location("ambito_administrativo", APP)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class AmbitoCodigoGvaTest(unittest.TestCase):
    def test_codigos_objetivo(self):
        for codigo in ("A1-01", "A2-01", "C1-01", "C2-01"):
            self.assertEqual(
                _MODULE.clasificar_ambito_administrativo({"denominacion": f"General {codigo}"}, aplicar_codigos_gva=True),
                "SI",
            )

    def test_codigo_gva_extendido_no_se_acepta_por_prefijo(self):
        for codigo in ("A1-01-01", "A2-01-ES", "C1-01-02", "C2-01-X"):
            self.assertEqual(
                _MODULE.clasificar_ambito_administrativo(
                    {"denominacion": f"Administrativo {codigo}"},
                    aplicar_codigos_gva=True,
                ),
                "NO",
            )
    def test_codigo_gva_no_objetivo_prevalece_sobre_nombre_generico(self):
        self.assertEqual(
            _MODULE.clasificar_ambito_administrativo(
                {"denominacion": "Agentes tributarios C1-07 administrativo"}, aplicar_codigos_gva=True
            ),
            "NO",
        )
        self.assertEqual(
            _MODULE.clasificar_ambito_administrativo(
                {"denominacion": "Técnico tributario A2-05 administración"}, aplicar_codigos_gva=True
            ),
            "NO",
        )


if __name__ == "__main__":
    unittest.main()
