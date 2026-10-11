"""Grupo/subgrupo desde la subescala declarada en el anuncio del BOE."""
import importlib.util
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

_RUTA = Path(__file__).resolve().parents[1] / "app" / "clasificacion_subescala.py"
_SPEC = importlib.util.spec_from_file_location("clasificacion_subescala", _RUTA)
cs = importlib.util.module_from_spec(_SPEC)
sys.modules["clasificacion_subescala"] = cs
_SPEC.loader.exec_module(cs)

PRE = "Oposiciones y concursos Departamento: Administración Local Referencia: BOE-A-2026-1 TEXTO ORIGINAL En el «Boletín Oficial de la Provincia de Valencia» número 136, de 20 de julio de 2026, se han publicado las bases que han de regir la convocatoria para proveer: "


class SubescalaTest(unittest.TestCase):
    def sub(self, frase):
        r = cs.clasificar_subescala(PRE + frase)
        return r["subgrupo"] if r else None

    def test_textos_reales_del_boe(self):
        self.assertEqual(self.sub("Una plaza de Administrativo/a, perteneciente a la escala de Administración General, subescala Administrativa, por el sistema de concurso-oposición, en turno libre."), "C1")  # Cerdà
        self.assertEqual(self.sub("Dos plazas de Auxiliar Administrativo/a, pertenecientes a la escala de Administración General, subescala Auxiliar, por el sistema de concurso-oposición, en turno libre."), "C2")  # Canals
        self.assertEqual(self.sub("Una plaza de Administrativo/a, perteneciente a la escala de Administración General, subescala Administrativa y clase Media, por el sistema de oposición, en turno libre."), "C1")  # Petrés
        self.assertEqual(self.sub("Una plaza de Técnico, escala de Administración General, subescala Técnica, por oposición."), "A1")
        self.assertEqual(self.sub("Una plaza de Técnico/a de Gestión, escala de Administración General, subescala de Gestión, por oposición."), "A2")

    def test_grupo_es_la_letra_del_subgrupo(self):
        r = cs.clasificar_subescala(PRE + "escala de Administración General, subescala Auxiliar, turno libre")
        self.assertEqual((r["grupo"], r["subgrupo"]), ("C", "C2"))

    def test_dos_subescalas_no_se_deciden(self):  # Polinyà: Administrativa/Auxiliar
        self.assertIsNone(self.sub("Dos plazas de Auxiliar Administrativo/a, escala de Administración General, subescala Administrativa/Auxiliar, por concurso-oposición"))

    def test_administracion_especial_por_clase(self):
        self.assertEqual(self.sub("Una plaza de Técnico/a medio de relaciones laborales, escala de Administración Especial, subescala técnica, clase técnico medio"), "A2")
        self.assertEqual(self.sub("escala de Administración Especial, subescala técnica, clase técnico superior"), "A1")
        self.assertIsNone(self.sub("escala de Administración Especial, subescala servicios especiales, clase policía local"))

    def test_sin_subescala_o_sin_texto_no_se_inventa_nada(self):
        self.assertIsNone(cs.clasificar_subescala(None))
        self.assertIsNone(cs.clasificar_subescala(""))
        self.assertIsNone(cs.clasificar_subescala("Una plaza de Auxiliar Administrativo/a por oposición"))

    def test_nunca_se_deduce_del_nombre_del_puesto(self):
        self.assertIsNone(cs.clasificar_subescala("Plaza de Administrativo/a"))


class ProcesoTest(unittest.TestCase):
    def test_textos_en_la_raiz_y_en_boe_local(self):
        t = "escala de Administración General, subescala Auxiliar"
        self.assertEqual(cs.clasificar_proceso({"texto_plaza": t})["subgrupo"], "C2")
        self.assertEqual(cs.clasificar_proceso({"boe_local": {"texto_plaza": t}})["subgrupo"], "C2")

    def test_textos_contradictorios_no_se_deciden(self):
        datos = {"boe_local_agregados": [
            {"texto_plaza": "escala de Administración General, subescala Auxiliar"},
            {"texto_plaza": "escala de Administración General, subescala Administrativa"}]}
        self.assertIsNone(cs.clasificar_proceso(datos))

    def test_sin_datos(self):
        self.assertIsNone(cs.clasificar_proceso({}))
        self.assertIsNone(cs.clasificar_proceso(None))


class _Cursor:
    def __init__(self, filas):
        self.filas, self.ejecutadas = filas, []

    def execute(self, sql, params=None):
        self.ejecutadas.append((sql, params))

    def fetchall(self):
        return self.filas


class SincronizarTest(unittest.TestCase):
    def _correr(self, aplicar):
        filas = [
            {"id": 1, "organismo": "A", "denominacion": "x",
             "datos_json": {"texto_plaza": "escala de Administración General, subescala Administrativa"}},
            {"id": 2, "organismo": "B", "denominacion": "y", "datos_json": {"origen": "BOP"}},
        ]
        cur = _Cursor(filas)

        @contextmanager
        def abrir():
            yield cur
        return cs.sincronizar_grupos_subescala(aplicar=aplicar, abrir_cursor=abrir), cur

    def test_simulacion_no_escribe(self):
        r, cur = self._correr(False)
        self.assertEqual((r["clasificados"], r["sin_clasificar"]), (1, 1))
        self.assertFalse(any(s.lstrip().startswith("UPDATE") for s, _ in cur.ejecutadas))

    def test_aplicar_solo_rellena_vacios(self):
        _, cur = self._correr(True)
        upd = [(s, p) for s, p in cur.ejecutadas if s.lstrip().startswith("UPDATE")]
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][1][:2], ("C", "C1"))
        self.assertIn("IS NULL AND NULLIF(BTRIM(subgrupo),'') IS NULL", upd[0][0])


if __name__ == "__main__":
    unittest.main()


class TestClaseMedia(unittest.TestCase):
    def test_tecnica_clase_media_es_a2(self):
        r = cs.clasificar_subescala(
            "perteneciente a la escala de Administración General, subescala Técnica y clase Media, por el sistema de oposición")
        self.assertEqual(r["subgrupo"], "A2")

    def test_tecnica_sin_clase_sigue_a1(self):
        r = cs.clasificar_subescala("escala de Administración General, subescala Técnica, por oposición")
        self.assertEqual(r["subgrupo"], "A1")
