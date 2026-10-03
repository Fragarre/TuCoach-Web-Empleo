import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconciliar_ambito_administrativo.py"
_SPEC = importlib.util.spec_from_file_location("reconciliar_ambito_administrativo", SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class ReconciliarAmbitoAdministrativoTest(unittest.TestCase):
    def test_script_es_revision_por_defecto_y_no_borra_historial(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("aplicar: bool = False", texto)
        self.assertIn("SET es_oportunidad=FALSE, ambito_administrativo='NO'", texto)
        self.assertNotIn("DELETE FROM procesos", texto)
        self.assertNotIn("DELETE FROM publicaciones", texto)

    def test_solo_actua_sobre_clasificacion_no(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if clasificacion == "NO":', texto)
        self.assertIn("WHERE p.es_oportunidad=TRUE", texto)
        self.assertIn("AND p.ambito_administrativo='SI'", texto)


if __name__ == "__main__":
    unittest.main()
