"""Plazos de inscripción locales: N días (hábiles/naturales) desde el BOE."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if "app" not in sys.modules:
    _PKG = types.ModuleType("app")
    _PKG.__path__ = [str(APP_DIR)]
    sys.modules["app"] = _PKG


def _cargar(nombre: str):
    clave = f"app.{nombre}"
    if clave in sys.modules:
        return sys.modules[clave]
    spec = importlib.util.spec_from_file_location(clave, APP_DIR / f"{nombre}.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[clave] = modulo
    spec.loader.exec_module(modulo)
    return modulo


ep = _cargar("estado_proceso")
pl = _cargar("plazos_inscripcion")

LIT20 = "El plazo de presentación de solicitudes será de veinte días hábiles a contar desde el siguiente al de la publicación de esta resolución en el «Boletín Oficial del Estado»."
LIT10 = LIT20.replace("veinte", "diez")
LIT20N = LIT20.replace("hábiles", "naturales")


class NumerosEnLetrasTest(unittest.TestCase):
    def test_literales_reales_del_boe(self):
        self.assertEqual(ep._dias_habiles_literal(LIT20), 20)
        self.assertEqual(ep._dias_habiles_literal(LIT10), 10)

    def test_cifras_compuestos_y_valenciano(self):
        self.assertEqual(ep._dias_habiles_literal("plazo de 15 días hábiles"), 15)
        self.assertEqual(ep._dias_habiles_literal("cuarenta y cinco días hábiles"), 45)
        self.assertEqual(ep._dias_habiles_literal("quinze dies hàbils"), 15)

    def test_naturales_no_se_recalculan_como_habiles(self):
        self.assertIsNone(ep._dias_habiles_literal(LIT20N))
        self.assertEqual(ep.numero_dias_literal(LIT20N), 20)

    def test_sin_plazo(self):
        self.assertIsNone(ep.numero_dias_literal("sin indicación"))
        self.assertIsNone(ep.numero_dias_literal(None))


class CalculoPlazoTest(unittest.TestCase):
    def test_boe_local_anidado_veinte_habiles(self):  # Cerdà, id 7
        p = pl.calcular_plazo({"boe_local": {"fecha_boe": "2026-09-11", "plazo_solicitudes_literal": LIT20}})
        self.assertEqual(p["fecha_apertura"], date(2026, 9, 12))
        self.assertEqual(p["fecha_cierre"], date(2026, 10, 13))

    def test_datos_en_la_raiz_diez_habiles(self):  # Almenara, id 17
        p = pl.calcular_plazo({"fecha_boe": "2026-07-15", "plazo_solicitudes_literal": LIT10})
        self.assertEqual(p["fecha_cierre"], date(2026, 7, 29))
        self.assertEqual(p["tipo_dias"], "HABILES")

    def test_rojales_coincide_con_las_bases(self):  # ids 44 y 110
        p = pl.calcular_plazo({"fecha_boe": "2026-10-07", "plazo_solicitudes_literal": LIT10})
        self.assertEqual((p["fecha_apertura"], p["fecha_cierre"]), (date(2026, 10, 8), date(2026, 10, 23)))

    def test_naturales_cuentan_todos_los_dias(self):  # Alto Palancia, id 38
        p = pl.calcular_plazo({"fecha_boe": "2026-09-17", "plazo_solicitudes_literal": LIT20N})
        self.assertEqual(p["fecha_cierre"], date(2026, 10, 7))
        self.assertEqual(p["tipo_dias"], "NATURALES")

    def test_naturales_que_acaban_en_inhabil_pasan_al_siguiente_habil(self):
        # 14/09/2026 + 20 naturales = domingo 04/10 -> lunes 05/10
        p = pl.calcular_plazo({"fecha_boe": "2026-09-14", "plazo_solicitudes_literal": LIT20N})
        self.assertEqual(p["fecha_cierre"], date(2026, 10, 5))

    def test_sin_datos_suficientes(self):
        self.assertIsNone(pl.calcular_plazo({}))
        self.assertIsNone(pl.calcular_plazo({"fecha_boe": "2026-09-17"}))
        self.assertIsNone(pl.calcular_plazo({"plazo_solicitudes_literal": LIT10}))
        self.assertIsNone(pl.calcular_plazo({"fecha_boe": "2026-09-17", "plazo_solicitudes_literal": "ver bases"}))
        self.assertIsNone(pl.calcular_plazo(None))

    def test_varios_boe_agregados_no_se_deciden(self):
        datos = {"boe_local_agregados": [
            {"fecha_boe": "2026-09-01", "plazo_solicitudes_literal": LIT10},
            {"fecha_boe": "2026-09-20", "plazo_solicitudes_literal": LIT10},
        ]}
        self.assertIsNone(pl.calcular_plazo(datos))

    def test_un_solo_agregado_si_se_calcula(self):
        datos = {"boe_local_agregados": [{"fecha_boe": "2026-09-01", "plazo_solicitudes_literal": LIT10}]}
        self.assertEqual(pl.calcular_plazo(datos)["fecha_cierre"], date(2026, 9, 15))


class DecidirTest(unittest.TestCase):
    DATOS = {"fecha_boe": "2026-09-23", "plazo_solicitudes_literal": LIT20}

    def test_rellena_si_no_hay_fechas(self):
        d = pl.decidir({"datos_json": self.DATOS, "fecha_apertura": None, "fecha_cierre": None})
        self.assertEqual(d["accion"], "RELLENAR")
        self.assertEqual(d["plazo"]["fecha_cierre"], date(2026, 10, 23))

    def test_no_hace_nada_si_ya_coincide(self):
        self.assertIsNone(pl.decidir({"datos_json": self.DATOS,
                                      "fecha_apertura": date(2026, 9, 24), "fecha_cierre": date(2026, 10, 23)}))

    def test_nunca_sobrescribe_y_avisa_de_discrepancia(self):
        d = pl.decidir({"datos_json": self.DATOS,
                        "fecha_apertura": date(2026, 9, 24), "fecha_cierre": date(2026, 10, 24)})
        self.assertEqual(d["accion"], "DISCREPANCIA")

    def test_fechas_parciales_no_se_tocan(self):
        d = pl.decidir({"datos_json": self.DATOS, "fecha_apertura": date(2026, 9, 24), "fecha_cierre": None})
        self.assertEqual(d["accion"], "PARCIAL_SIN_TOCAR")


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
            {"id": 1, "organismo": "A", "denominacion": "x", "fecha_apertura": None, "fecha_cierre": None,
             "datos_json": {"fecha_boe": "2026-09-23", "plazo_solicitudes_literal": LIT20}},
            {"id": 2, "organismo": "B", "denominacion": "y", "fecha_apertura": date(2026, 10, 5),
             "fecha_cierre": date(2026, 11, 4), "datos_json": {"fecha_boe": "2026-10-03", "plazo_solicitudes_literal": LIT20}},
            {"id": 3, "organismo": "C", "denominacion": "z", "fecha_apertura": None, "fecha_cierre": None,
             "datos_json": {"boe_local": {}}},
        ]
        cur = _Cursor(filas)

        @contextmanager
        def abrir():
            yield cur
        return pl.sincronizar_plazos_inscripcion(aplicar=aplicar, abrir_cursor=abrir), cur

    def test_simulacion_no_escribe(self):
        r, cur = self._correr(False)
        self.assertEqual((r["rellenados"], r["discrepancias"], r["sin_plazo"]), (1, 1, 1))
        self.assertFalse(any(s.lstrip().startswith("UPDATE") for s, _ in cur.ejecutadas))

    def test_aplicar_escribe_solo_los_vacios(self):
        r, cur = self._correr(True)
        updates = [(s, p) for s, p in cur.ejecutadas if s.lstrip().startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1][:2], (date(2026, 9, 24), date(2026, 10, 23)))
        self.assertIn("fecha_apertura IS NULL AND fecha_cierre IS NULL", updates[0][0])


class EstadoInscripcionConMarcaTest(unittest.TestCase):
    def test_conserva_el_aviso_de_calculo_sin_festivos_locales(self):
        proceso = {"fecha_apertura": date(2026, 9, 24), "fecha_cierre": date(2026, 10, 23),
                   "datos_json": {"plazo_inscripcion": {"origen": "BOE_CALCULADO", "calendario": "COMUNITAT_VALENCIANA",
                                                        "calendario_verificado": True, "dias": 20,
                                                        "tipo_dias": "HABILES", "literal": LIT20}}}
        r = ep.estado_inscripcion(proceso, hoy=date(2026, 10, 11))
        self.assertEqual(r["codigo"], "ABIERTO")
        self.assertTrue(r["fecha_cierre_calculada"])
        self.assertEqual(r["dias_habiles"], 20)

    def test_fechas_de_sede_no_llevan_marca_de_calculo(self):
        r = ep.estado_inscripcion({"fecha_apertura": date(2026, 9, 24), "fecha_cierre": date(2026, 10, 23),
                                   "datos_json": {}}, hoy=date(2026, 10, 11))
        self.assertNotIn("fecha_cierre_calculada", r)


if __name__ == "__main__":
    unittest.main()
