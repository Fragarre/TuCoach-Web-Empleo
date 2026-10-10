from __future__ import annotations

import importlib.util
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ciclos", Path(__file__).resolve().parents[1] / "app" / "ciclos.py"
)
assert _SPEC and _SPEC.loader
ci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci)
ci._jsonb = lambda v: v

HOY = date(2026, 10, 10)


class VentanaTest(unittest.TestCase):
    def dias(self, ultimo, **kw):
        return ci.dias_necesarios(ultimo, HOY, solape_minimo=7, tope=30, **kw)

    def test_sin_historial_usa_el_minimo(self):
        self.assertEqual(self.dias(None), (7, False))

    def test_exito_reciente_mantiene_el_minimo(self):
        self.assertEqual(self.dias(date(2026, 10, 9)), (7, False))

    def test_caida_de_diez_dias_se_cubre_entera(self):
        # último éxito el 30-sep: hay que leer del 30-sep a hoy (+1 de solape)
        self.assertEqual(self.dias(date(2026, 9, 30)), (11, False))

    def test_supera_el_tope_se_limita_y_avisa(self):
        self.assertEqual(self.dias(date(2026, 7, 1)), (30, True))

    def test_justo_en_el_tope(self):
        self.assertEqual(self.dias(date(2026, 9, 11)), (30, False))


def _payload(estados, total=None):
    total = len(estados) if total is None else total
    return {
        "estado_fuentes": {n: ({"estado": e} if e != "ERROR" else {"estado": e, "error": "boom"}) for n, e in estados.items()},
        "resumen_fuentes": {"total": total, "errores": sum(e == "ERROR" for e in estados.values())},
        "ciclo": {"disponible": True},
    }


class EvaluacionTest(unittest.TestCase):
    def test_todo_correcto(self):
        r = ci.evaluar_resultado(_payload({"a": "OK", "b": "SIN_NOVEDADES"}))
        self.assertEqual((r.codigo, r.nivel, r.mensajes), (0, "OK", []))

    def test_una_fuente_en_error_hace_fallar_el_cron(self):
        r = ci.evaluar_resultado(_payload({"a": "OK", "b": "ERROR"}))
        self.assertEqual((r.codigo, r.nivel), (1, "ERROR"))
        self.assertIn("1 de 2", r.mensajes[0])
        self.assertIn("b (boom)", r.mensajes[0])

    def test_todas_fallan(self):
        r = ci.evaluar_resultado(_payload({"a": "ERROR", "b": "ERROR"}))
        self.assertTrue(r.mensajes[0].startswith("TODAS las fuentes fallaron"))

    def test_tolerancia_configurable(self):
        r = ci.evaluar_resultado(_payload({"a": "OK", "b": "ERROR"}), errores_tolerados=1)
        self.assertEqual(r.codigo, 0)

    def test_degradada_avisa_pero_no_falla(self):
        r = ci.evaluar_resultado(_payload({"a": "DEGRADADA"}))
        self.assertEqual((r.codigo, r.nivel), (0, "AVISO"))

    def test_ciclo_sin_fuentes_es_error(self):
        r = ci.evaluar_resultado({"resumen_fuentes": {"total": 0}})
        self.assertEqual(r.codigo, 1)

    def test_ciclo_omitido_no_es_error(self):
        r = ci.evaluar_resultado({"ciclo": {"omitido": True, "motivo": "otro ciclo"}})
        self.assertEqual((r.codigo, r.nivel), (0, "AVISO"))

    def test_avisa_si_faltan_tablas_y_si_excede_ventana(self):
        p = _payload({"a": "OK"})
        p["ciclo"] = {"disponible": False}
        p["ventanas_excedidas"] = {"gva": 90}
        r = ci.evaluar_resultado(p)
        self.assertEqual(r.codigo, 0)
        self.assertEqual(len(r.mensajes), 2)


class Erroneo(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class CursorFalso:
    def __init__(self, fallo_insert=None, fallo_update=None):
        self.fallo_insert, self.fallo_update = fallo_insert, fallo_update
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO ciclos") and self.fallo_insert:
            raise Erroneo(self.fallo_insert)
        if sql.startswith("UPDATE ciclos") and self.fallo_update:
            raise Erroneo(self.fallo_update)

    def fetchone(self):
        return {"id": 42}


def _abrir(cursor):
    @contextmanager
    def ctx():
        yield cursor
    return ctx


class ReservaTest(unittest.TestCase):
    def test_reserva_correcta_y_limpia_ciclos_abandonados_antes(self):
        cur = CursorFalso()
        self.assertEqual(ci.iniciar_ciclo(abrir=_abrir(cur)), (42, "INICIADO"))
        self.assertTrue(cur.sql[0].startswith("UPDATE ciclos_periodicos SET estado='ABANDONADO'"))
        self.assertTrue(cur.sql[1].startswith("INSERT INTO ciclos_periodicos"))

    def test_otro_ciclo_en_curso(self):
        cur = CursorFalso(fallo_insert="23505")
        self.assertEqual(ci.iniciar_ciclo(abrir=_abrir(cur)), (None, "OMITIDO_CICLO_EN_CURSO"))

    def test_sin_tablas_degrada(self):
        cur = CursorFalso(fallo_update="42P01")
        self.assertEqual(ci.iniciar_ciclo(abrir=_abrir(cur)), (None, "SIN_TABLAS"))

    def test_otros_errores_se_propagan(self):
        cur = CursorFalso(fallo_insert="08006")
        with self.assertRaises(Erroneo):
            ci.iniciar_ciclo(abrir=_abrir(cur))

    def test_finalizar_y_registrar_sin_id_no_hacen_nada(self):
        cur = CursorFalso()
        ci.finalizar_ciclo(None, "OK", {}, abrir=_abrir(cur))
        ci.registrar_ejecuciones(None, [{"fuente": "a", "estado": "OK"}], abrir=_abrir(cur))
        self.assertEqual(cur.sql, [])


if __name__ == "__main__":
    unittest.main()
