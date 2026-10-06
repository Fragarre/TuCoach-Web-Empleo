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

_CASTELLON_SPEC = importlib.util.spec_from_file_location(
    "app.bop_castellon", APP_DIR / "bop_castellon.py"
)
assert _CASTELLON_SPEC and _CASTELLON_SPEC.loader
_CASTELLON = importlib.util.module_from_spec(_CASTELLON_SPEC)
_CASTELLON_SPEC.loader.exec_module(_CASTELLON)
clasificar_castellon = _CASTELLON._clasificar_anuncio_castellon


class SeguimientoDiputacionTest(unittest.TestCase):
    def test_dificil_cobertura_administrativa_se_clasifica_como_candidata(self):
        self.assertEqual(
            clasificar_castellon(
                "Diputación. Bolsa provisión puestos difícil cobertura de auxiliar administrativo"
            ),
            "ANUNCIO_DIFICIL_COBERTURA",
        )

    def test_dificil_cobertura_promocion_interna_sigue_excluida(self):
        self.assertEqual(
            clasificar_castellon(
                "Bolsa de difícil cobertura de auxiliar administrativo por promoción interna"
            ),
            "EXCLUIDO_INTERNO",
        )

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
