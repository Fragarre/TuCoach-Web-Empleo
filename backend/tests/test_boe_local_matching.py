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
_filtrar_extraccion_boe_desde = _MODULE._filtrar_extraccion_boe_desde


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


class VentanaRecuperacionBoeTest(unittest.TestCase):
    def test_bases_antiguas_conservan_boe_reciente_de_la_extraccion(self) -> None:
        extraccion = {
            "detalle": [
                {"boe_id": "ANTIGUO", "fecha_boe": "2026-03-01"},
                {"boe_id": "RECIENTE", "fecha_boe": "2026-10-02"},
            ],
            "errores": [],
        }

        filtrada = _filtrar_extraccion_boe_desde(extraccion, __import__("datetime").date(2026, 4, 7))

        self.assertEqual(
            [item["boe_id"] for item in filtrada["detalle"]],
            ["RECIENTE"],
        )

    def test_filtro_no_amplia_una_extraccion_incremental(self) -> None:
        extraccion = {
            "detalle": [
                {"boe_id": "AYER", "fecha_boe": "2026-10-02"},
                {"boe_id": "HOY", "fecha_boe": "2026-10-03"},
            ],
            "errores": [],
        }

        filtrada = _filtrar_extraccion_boe_desde(extraccion, __import__("datetime").date(2026, 4, 7))

        self.assertEqual(filtrada["detalle"], extraccion["detalle"])
