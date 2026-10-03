from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)

_MODULE_PATH = APP_DIR / "boe_local_import.py"
_SPEC = importlib.util.spec_from_file_location("app.boe_local_import", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_familia = _MODULE._familia


class FamiliaBoeLocalTest(unittest.TestCase):
    def test_errata_boe_adminstrativo_se_normaliza(self) -> None:
        self.assertEqual(
            _familia("Auxiliar Adminstrativo/a"),
            "AUXILIAR_ADMINISTRATIVO",
        )

    def test_formas_ordinarias_se_mantienen(self) -> None:
        self.assertEqual(_familia("Auxiliar Administrativo/a"), "AUXILIAR_ADMINISTRATIVO")
        self.assertEqual(_familia("Administrativo/a"), "ADMINISTRATIVO")
        self.assertEqual(
            _familia("Técnico/a de Administración General"),
            "TAG",
        )


if __name__ == "__main__":
    unittest.main()
