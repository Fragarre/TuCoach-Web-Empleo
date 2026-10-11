import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

_APP = Path(__file__).resolve().parents[1] / "app"
if "app" not in sys.modules:
    _pkg = types.ModuleType("app")
    _pkg.__path__ = [str(_APP)]
    sys.modules["app"] = _pkg
_SPEC = importlib.util.spec_from_file_location("app.grupos_por_nombre", _APP / "grupos_por_nombre.py")
gn = importlib.util.module_from_spec(_SPEC)
sys.modules["app.grupos_por_nombre"] = gn
_SPEC.loader.exec_module(gn)


class TestNombre(unittest.TestCase):
    def sub(self, nombre):
        r = gn.clasificar_por_nombre(nombre)
        return r["subgrupo"] if r else None

    def test_exactos(self):
        for nombre, esperado in [
            ("Técnico/a de Administración General", "A1"),
            ("Técnico o Técnica de Administración General", "A1"),
            ("Técnico de Administración General", "A1"),
            ("Técnico Administración General", "A1"),
            ("Administrativo/a", "C1"),
            ("Administrativo o administrativa", "C1"),
            ("Administrativo/Administrativa", "C1"),
            ("Auxiliar Administrativo/a", "C2"),
            ("Auxiliar administrativo", "C2"),
            ("Auxiliar de Administración General", "C2"),
        ]:
            self.assertEqual(self.sub(nombre), esperado, nombre)

    def test_no_exactos_no_se_deducen(self):
        for nombre in [
            "Administrativo o administrativa de servicios sociales",
            "Administrativo/Administrativa Servicios Sociales de la plantilla de personal laboral fijo",
            "Técnico/a medio/a de relaciones laborales",
            "Técnico/a de Gestión",
            "Técnico/a de Tesorería",
            "Informador-a tramitador-a",
            "", None,
        ]:
            self.assertIsNone(self.sub(nombre), nombre)


class TestSincronizar(unittest.TestCase):
    def _abrir(self, filas):
        cur = MagicMock()
        cur.fetchall.return_value = filas
        @contextmanager
        def abrir():
            yield cur
        return cur, abrir

    def test_revision_y_aplicar(self):
        filas = [{"id": 1, "denominacion": "Administrativo/a", "organismo": "A"},
                 {"id": 2, "denominacion": "Técnico/a de Tesorería", "organismo": "B"}]
        cur, abrir = self._abrir(filas)
        r = gn.sincronizar_grupos_por_nombre(abrir_cursor=abrir)
        self.assertEqual((r["clasificados"], r["sin_clasificar"]), (1, 1))
        self.assertFalse([c for c in cur.execute.call_args_list if "UPDATE" in str(c)])
        cur, abrir = self._abrir(filas)
        gn.sincronizar_grupos_por_nombre(aplicar=True, abrir_cursor=abrir)
        self.assertEqual(len([c for c in cur.execute.call_args_list if "UPDATE" in str(c)]), 1)


if __name__ == "__main__":
    unittest.main()
