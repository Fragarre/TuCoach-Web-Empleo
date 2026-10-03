import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)
_SPEC = importlib.util.spec_from_file_location("app.bop_alicante", APP_DIR / "bop_alicante.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
seleccionar = _MODULE.seleccionar_proceso_seguimiento


class SeguimientoDiputacionTest(unittest.TestCase):
    def test_identidad_sintetica_permita_matching_conservador(self):
        hallazgo = {
            "denominacion": "Diputación Provincial de Castellón",
            "extracto": "Relación definitiva de admitidos para 9 plazas de Auxiliar Administrativo",
        }
        candidatos = [{
            "id": 408,
            "municipio": "Diputación Provincial de Castellón",
            "denominacion": "Bases Concurso Oposición Libre 9 Plazas Auxiliar Administrativo",
        }]
        proceso, motivo = seleccionar(hallazgo, candidatos)
        self.assertEqual(proceso["id"], 408)
        self.assertEqual(motivo, "UNICO_HITO_SELECTIVO")


if __name__ == "__main__":
    unittest.main()
