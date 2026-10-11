import importlib.util
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import sys
import types

_APP = Path(__file__).resolve().parents[1] / "app"
if "app" not in sys.modules:  # paquete mínimo: evita cargar el __init__ completo
    _pkg = types.ModuleType("app")
    _pkg.__path__ = [str(_APP)]
    sys.modules["app"] = _pkg
_SPEC = importlib.util.spec_from_file_location("app.grupos_documento", _APP / "grupos_documento.py")
gd = importlib.util.module_from_spec(_SPEC)
sys.modules["app.grupos_documento"] = gd
_SPEC.loader.exec_module(gd)

BENIARBEIG = (
    "AYUNTAMIENTO BENIARBEIG BASES Y CONVOCATORIA PARA CUBRIR DOS PLAZAS DE ADMINISTRATIVO/A (GRUPO C1) ... "
    "Subescala Administrativa, grupo C, subgrupo C1, de la plantilla ... "
    "GRUPO SUBGRUPO SUBESCALA N.º VACANTES DENOMINACIÓN C C1 Administrativa 2 Administrativo "
    "BASE PRIMERA ... GRUPO SUBGRUPO SUBESCALA N.º VACANTES DENOMINACIÓN C C1 Administrativa 2 Administrativo "
)


class TestExtraerPuesto(unittest.TestCase):
    def test_tabla_beniarbeig(self):
        r = gd.extraer_puesto(BENIARBEIG)
        self.assertEqual((r["grupo"], r["subgrupo"], r["plazas"], r["origen"]), ("C", "C1", 2, "TABLA"))

    def test_tabla_repetida_no_duplica_plazas(self):
        self.assertEqual(gd.extraer_puesto(BENIARBEIG)["plazas"], 2)

    def test_subgrupos_distintos_no_decide(self):
        t = "N.º VACANTES DENOMINACIÓN C C1 Administrativa 1 Administrativo A A1 Tecnica 1 Tecnico"
        self.assertIsNone(gd.extraer_puesto(t))

    def test_solo_prosa(self):
        r = gd.extraer_puesto("plaza de la escala X, grupo C, subgrupo C2, por oposición")
        self.assertEqual((r["subgrupo"], r["plazas"], r["origen"]), ("C2", None, "TEXTO"))

    def test_sin_datos(self):
        self.assertIsNone(gd.extraer_puesto("convocatoria de una plaza"))
        self.assertIsNone(gd.extraer_puesto(None))


class TestMunicipio(unittest.TestCase):
    def test_menciona(self):
        self.assertTrue(gd.menciona_municipio("AYUNTAMIENTO BENIARBEIG bases", "Ayuntamiento de Beniarbeig"))
        self.assertTrue(gd.menciona_municipio("Ajuntament de Monòver", "Ayuntamiento de Monóvar/Monòver"))
        self.assertTrue(gd.menciona_municipio("Ajuntament de l'Eliana", "Ayuntamiento de l'Eliana"))

    def test_no_menciona(self):
        self.assertFalse(gd.menciona_municipio("Ayuntamiento de Turís", "Ayuntamiento de Foios"))

    def test_fallback_subescala(self):
        r = gd.extraer_puesto("escala de Administración General, subescala Auxiliar, por oposición")
        self.assertEqual((r["subgrupo"], r["origen"]), ("C2", "SUBESCALA"))


class TestUrls(unittest.TestCase):
    def test_excluye_boe_y_duplicados(self):
        p = {"urls_publicaciones": ["https://www.boe.es/x", "https://sede/a.pdf"],
             "datos_json": {"url_oficial": "https://sede/a.pdf", "bases_bop": {"url": "https://bop/b"}}}
        self.assertEqual(gd.urls_documento(p), ["https://sede/a.pdf", "https://bop/b"])


class TestSincronizar(unittest.TestCase):
    def _cursor(self, filas):
        cur = MagicMock()
        cur.fetchall.return_value = filas
        @contextmanager
        def abrir():
            yield cur
        return cur, abrir

    def _fila(self, i, urls):
        return {"id": i, "denominacion": "Administrativo/a", "plazas": None, "organismo": "Ayuntamiento de Beniarbeig",
                "datos_json": {}, "urls_publicaciones": urls}

    def test_revision_no_escribe(self):
        cur, abrir = self._cursor([self._fila(109, ["https://sede/a.pdf"]), self._fila(11, [])])
        r = gd.sincronizar_grupos_documento(abrir_cursor=abrir, obtener_texto=lambda u: BENIARBEIG)
        self.assertEqual((r["clasificados"], r["sin_documento"]), (1, 1))
        updates = [c for c in cur.execute.call_args_list if "UPDATE" in str(c)]
        self.assertEqual(updates, [])

    def test_aplicar_escribe(self):
        cur, abrir = self._cursor([self._fila(109, ["https://sede/a.pdf"])])
        gd.sincronizar_grupos_documento(aplicar=True, abrir_cursor=abrir, obtener_texto=lambda u: BENIARBEIG)
        updates = [c for c in cur.execute.call_args_list if "UPDATE" in str(c)]
        self.assertEqual(len(updates), 1)

    def test_error_de_descarga_no_rompe(self):
        def falla(u):
            raise RuntimeError("red")
        _, abrir = self._cursor([self._fila(1, ["https://x"])])
        r = gd.sincronizar_grupos_documento(abrir_cursor=abrir, obtener_texto=falla)
        self.assertEqual((r["errores"], r["sin_clasificar"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
