import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)

_MODULE_PATH = APP_DIR / "gva_bolsas_complementarias.py"
_SPEC = importlib.util.spec_from_file_location("app.gva_bolsas_complementarias", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_cambia = _MODULE._bolsa_directa_cambia


class BolsaGvaIdempotenciaTest(unittest.TestCase):
    def _existente(self):
        return {
            "denominacion": "Bolsa administrativa",
            "cuerpo_escala": "C1-01",
            "grupo": "C1",
            "turno": None,
            "anio_convocatoria": 2026,
            "fecha_apertura": None,
            "fecha_cierre": None,
            "ultima_publicacion_at": None,
            "datos_json": {
                "fase_gva": "Bolsa en funcionamiento",
                "etapa_actual_gva": "Listado definitivo",
                "huella_novedad_bolsa": "abc123",
            },
        }

    def _proceso(self, huella="abc123"):
        return {
            "denominacion": "Bolsa administrativa",
            "cuerpo_escala": "C1-01",
            "grupo": "C1",
            "turno": None,
            "anio_convocatoria": 2026,
            "fecha_apertura": None,
            "fecha_cierre": None,
            "ultima_publicacion_at": None,
            "datos_json": {
                "fase_gva": "Bolsa en funcionamiento",
                "etapa_actual_gva": "Listado definitivo",
            },
            "publicacion": {"contenido_hash": huella},
        }

    def test_misma_huella_no_genera_actualizacion_repetida(self):
        self.assertFalse(_cambia(self._existente(), self._proceso()))

    def test_huella_nueva_genera_actualizacion(self):
        self.assertTrue(_cambia(self._existente(), self._proceso("def456")))


if __name__ == "__main__":
    unittest.main()
