import importlib.util
from pathlib import Path
import sys
import types
import unittest
from datetime import date


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)

_MODULE_PATH = APP_DIR / "bop_valencia.py"
_SPEC = importlib.util.spec_from_file_location("app.bop_valencia", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_cambios_base_existente = _MODULE._cambios_base_existente


class RefrescoBasesDiputacionValenciaTest(unittest.TestCase):
    def test_releer_bases_no_modifica_estado_terminal(self) -> None:
        existente = (
            123,
            "Administrativo/a",
            "C",
            "C1",
            "Oposición",
            "Libre",
            2,
            "FINALIZADO",
            2026,
            date(2026, 1, 15),
            {},
        )
        nuevos = (
            "Administrativo/a",
            "C",
            "C1",
            "Oposición",
            "Libre",
            2,
            2026,
            date(2026, 1, 15),
        )

        cambios = _cambios_base_existente(existente, nuevos)

        self.assertEqual(cambios, [])
        self.assertNotIn("estado", [campo for campo, _, _ in cambios])

    def test_refresca_metadatos_sin_tocar_estado(self) -> None:
        existente = (
            123,
            "Administrativo/a",
            "C",
            "C1",
            "Oposición",
            "Libre",
            2,
            "ANULADO",
            2026,
            date(2026, 1, 15),
            {},
        )
        nuevos = (
            "Administrativo/a",
            "C",
            "C1",
            "Oposición",
            "Libre",
            3,
            2026,
            date(2026, 1, 15),
        )

        cambios = _cambios_base_existente(existente, nuevos)

        self.assertEqual(cambios, [("plazas", 2, 3)])
        self.assertNotIn("estado", [campo for campo, _, _ in cambios])


if __name__ == "__main__":
    unittest.main()
