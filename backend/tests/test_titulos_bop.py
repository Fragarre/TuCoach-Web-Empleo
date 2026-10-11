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
_SPEC = importlib.util.spec_from_file_location("app.titulos_bop", _APP / "titulos_bop.py")
tb = importlib.util.module_from_spec(_SPEC)
sys.modules["app.titulos_bop"] = tb
_SPEC.loader.exec_module(tb)

CASOS = [
    ("Anunci de l'Ajuntament de Cerdà sobre l'aprovació de les bases reguladores per a la provisió d'una plaça d'administratiu/va, per concurs oposició, de l'oferta d'ocupació pública de 2025.", "Administrativo/a"),
    ("Anunci de l'Ajuntament de l'Eliana sobre l'aprovació de les bases que regiran el procés selectiu per a la provisió definitiva del lloc d'administratiu/va cap de negociat en OAC, Estadística i Padró d'habitants.", "Administrativo/a jefe/a de negociado en OAC, Estadística y Padrón de habitantes"),
    ("Anunci de l'Ajuntament de Polinyà de Xúquer sobre l'aprovació de les bases de la convocatòria per a cobrir en propietat dues places d'auxiliar administratiu/va d'administració general.", "Auxiliar administrativo/a de Administración General"),
    ("Anunci de l'Ajuntament de Xirivella sobre l'aprovació de les bases de la convocatòria d'oposició per a set llocs de personal auxiliar administratiu/va.", "Auxiliar administrativo/a"),
    ("Anunci de l'Ajuntament de Petrés sobre l'aprovació de les bases i la convocatòria per a cobrir la plaça d'administratiu/va, C1, mitjançant sistema d'oposició lliure.", "Administrativo/a"),
    ("Anunci de l'Ajuntament de Foios sobre l'aprovació de les bases reguladores del procés de selecció per a la cobertura en propietat de tres places vacants d'administratiu/va, mitjançant el sistema de concurs oposició.", "Administrativo/a"),
    ("Anunci de l'Ajuntament de Benavites sobre l'aprovació de les bases de la convocatòria d'una plaça per a administratiu/va.", "Administrativo/a"),
    ("Anunci de l'Ajuntament de Canals sobre l'aprovació de les bases de la convocatòria, mitjançant torn lliure i pel sistema de concurs oposició, per a diverses places d'auxiliar administratiu/va.", "Auxiliar administrativo/a"),
    ("Anunci de l'Ajuntament de Carcaixent sobre l'aprovació de les bases de la convocatòria d'una plaça de tècnic/a mitjà/na d'administració general.", "Técnico/a medio/a de Administración General"),
]


class TestLimpiar(unittest.TestCase):
    def test_casos_reales(self):
        for original, esperado in CASOS:
            self.assertEqual(tb.limpiar_titulo(original), esperado, original)

    def test_no_toca_lo_que_no_es_anuncio(self):
        for t in ["Administrativo/a", "Técnico/a de Gestión", "Convocatoria 58/26. Pruebas selectivas", "", None]:
            self.assertIsNone(tb.limpiar_titulo(t))

    def test_resto_en_valenciano_no_cambia(self):
        self.assertIsNone(tb.limpiar_titulo("Anunci de l'Ajuntament de X sobre bases per a una plaça de peó de la brigada municipal amb dret"))


class TestPlazas(unittest.TestCase):
    def test_casos_reales(self):
        esperado = [1, 1, 2, 7, 1, 3, 1, None, 1]
        for (original, _), n in zip(CASOS, esperado):
            self.assertEqual(tb.plazas_del_titulo(original), n, original[:80])

    def test_compuestos_y_no_anuncio(self):
        self.assertEqual(tb.plazas_del_titulo("Anunci sobre vint-i-una places de tècnic/a"), 21)
        self.assertIsNone(tb.plazas_del_titulo("Administrativo/a, dues places"))
        self.assertIsNone(tb.plazas_del_titulo(None))


class TestAlcance(unittest.TestCase):
    def test_excluye_diputacion(self):
        self.assertNotIn("DIPUTACION", tb.SQL_CANDIDATOS)
        self.assertIn("AYUNTAMIENTO", tb.SQL_CANDIDATOS)


class TestSincronizar(unittest.TestCase):
    def test_titulo_ya_limpio_aun_rellena_plazas_desde_el_original(self):
        fila = {"id": 8, "organismo": "l'Eliana", "denominacion": "Administrativo/a jefe/a de negociado",
                "plazas": None, "original": CASOS[1][0]}
        cur = MagicMock()
        cur.fetchall.return_value = [fila]
        @contextmanager
        def abrir():
            yield cur
        r = tb.limpiar_titulos_bop(aplicar=True, abrir_cursor=abrir)
        self.assertEqual((r["limpiados"], r["plazas_rellenadas"]), (0, 1))
        sqls = [str(c) for c in cur.execute.call_args_list if "UPDATE" in str(c)]
        self.assertEqual(len(sqls), 1)
        self.assertIn("plazas", sqls[0])

    def test_no_pisa_plazas_existentes(self):
        fila = {"id": 7, "organismo": "Cerdà", "denominacion": CASOS[0][0], "plazas": 1, "original": None}
        cur = MagicMock()
        cur.fetchall.return_value = [fila]
        @contextmanager
        def abrir():
            yield cur
        r = tb.limpiar_titulos_bop(abrir_cursor=abrir)
        self.assertEqual(r["plazas_rellenadas"], 0)

    def _abrir(self, filas):
        cur = MagicMock()
        cur.fetchall.return_value = filas
        @contextmanager
        def abrir():
            yield cur
        return cur, abrir

    def test_revision_y_aplicar(self):
        filas = [{"id": 7, "organismo": "Cerdà", "denominacion": CASOS[0][0], "plazas": None, "original": None},
                 {"id": 99, "organismo": "X", "denominacion": "Anunci sense plaça reconeixible", "plazas": None, "original": None}]
        cur, abrir = self._abrir(filas)
        r = tb.limpiar_titulos_bop(abrir_cursor=abrir)
        self.assertEqual((r["limpiados"], r["sin_cambio"]), (1, 1))
        self.assertEqual(r["plazas_rellenadas"], 1)
        self.assertFalse([c for c in cur.execute.call_args_list if "UPDATE" in str(c)])
        cur, abrir = self._abrir(filas)
        tb.limpiar_titulos_bop(aplicar=True, abrir_cursor=abrir)
        # título + plazas del proceso 7; el 99 no cambia nada
        self.assertEqual(len([c for c in cur.execute.call_args_list if "UPDATE" in str(c)]), 2)


if __name__ == "__main__":
    unittest.main()
