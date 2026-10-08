import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)

_MODULE_PATH = APP_DIR / "gva_estatal_source.py"
_SPEC = importlib.util.spec_from_file_location("app.gva_estatal_source", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_es_admin = _MODULE._es_admin\n_obtener_listado = _MODULE._obtener_listado\n_parse_tarjetas = _MODULE._parse_tarjetas


class AmbitoGvaEstatalTest(unittest.TestCase):
    def test_codigos_administrativos_objetivo_se_incluyen(self) -> None:
        for codigo in ("A1-01", "A2-01", "C1-01", "C2-01"):
            incluido, codigos = _es_admin("Cuerpo administrativo", f"Convocatoria {codigo}")
            self.assertTrue(incluido, codigo)
            self.assertEqual(codigos, [codigo])

    def test_especialidades_explicitas_se_excluyen_aunque_el_titulo_sea_generico(self) -> None:
        for codigo in ("A2-05", "C1-07"):
            incluido, codigos = _es_admin("Cuerpo administrativo", f"Convocatoria {codigo}")
            self.assertFalse(incluido, codigo)
            self.assertEqual(codigos, [])

    def test_denominacion_administrativa_sin_codigo_sigue_siendo_valida(self) -> None:
        incluido, codigos = _es_admin("Auxiliar administrativo/a", "Ingreso libre")
        self.assertTrue(incluido)
        self.assertEqual(codigos, [])


if __name__ == "__main__":
    unittest.main()
