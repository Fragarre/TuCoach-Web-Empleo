"""Filtro 'Convocatorias en plazo' y catálogo de activas."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("estado_proceso", BACKEND / "app" / "estado_proceso.py")
ep = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ep)


class EstaEnPlazoTest(unittest.TestCase):
    def test_abierto(self):
        self.assertTrue(ep.esta_en_plazo({"codigo": "ABIERTO"}))

    def test_cerrado_pendiente_y_sin_datos(self):
        for codigo in ("CERRADO", "PENDIENTE_APERTURA", "PENDIENTE_BOE", "PLAZO_LITERAL", "NO_DETERMINADO"):
            self.assertFalse(ep.esta_en_plazo({"codigo": codigo}), codigo)

    def test_varios_plazos_con_uno_abierto(self):
        insc = {"codigo": "PLAZOS_MULTIPLES", "plazos": [{"codigo": "CERRADO"}, {"codigo": "ABIERTO"}]}
        self.assertTrue(ep.esta_en_plazo(insc))

    def test_varios_plazos_todos_cerrados(self):
        self.assertFalse(ep.esta_en_plazo({"codigo": "PLAZOS_MULTIPLES", "plazos": [{"codigo": "CERRADO"}]}))

    def test_proceso_con_fechas_directas(self):
        proceso = {"fecha_apertura": date(2026, 10, 1), "fecha_cierre": date(2026, 10, 20)}
        self.assertTrue(ep.esta_en_plazo(ep.estado_inscripcion(proceso, hoy=date(2026, 10, 10))))
        self.assertFalse(ep.esta_en_plazo(ep.estado_inscripcion(proceso, hoy=date(2026, 10, 21))))

    def test_plazo_calculado_cruzando_de_anio(self):
        proceso = {"datos_json": {"plazo_solicitudes_literal": "20 días hábiles"},
                   "fecha_boe_publicacion": date(2026, 12, 10)}
        # cierre real 2027-01-12 (antes salía 2027-01-08 y el 11-ene ya figuraba cerrada)
        self.assertTrue(ep.esta_en_plazo(ep.estado_inscripcion(proceso, hoy=date(2027, 1, 11))))
        self.assertFalse(ep.esta_en_plazo(ep.estado_inscripcion(proceso, hoy=date(2027, 1, 13))))


class CursorFalso:
    def __init__(self, filas):
        self.filas, self.sql, self.params = filas, None, None
        self.description = [type("D", (), {"name": n})() for n in COLUMNAS]

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.filas


COLUMNAS = ("id", "organismo_id", "organismo_nombre", "estado", "tipo_proceso", "fecha_apertura",
            "fecha_cierre", "datos_json", "fuente_principal_tipo")


def fila(i, cierre):
    return (i, 1, "Org", "EN_CURSO", "Oposición", date(2026, 1, 1), cierre, {}, "BOE")


class ListarEnPlazoTest(unittest.TestCase):
    def _listar(self, filas, **kw):
        sys.path.insert(0, str(BACKEND))
        from app import procesos
        cur = CursorFalso(filas)

        @contextmanager
        def conexion():
            class C:
                @contextmanager
                def cursor(self_inner):
                    yield cur
            yield C()

        with patch.object(procesos, "get_connection", conexion), \
             patch.object(procesos.estado_inscripcion.__globals__["_festivos"], "hoy_es", lambda: date(2026, 10, 10)):
            return procesos.listar_procesos(**kw), cur

    def test_filtra_por_plazo_y_amplia_el_limite_sql(self):
        filas = [fila(1, date(2026, 12, 31)), fila(2, date(2026, 9, 1)), fila(3, date(2026, 11, 1))]
        res, cur = self._listar(filas, en_plazo=True, limite=10)
        self.assertEqual([p["id"] for p in res], [1, 3])
        self.assertEqual(cur.params[-1], 1000)

    def test_recorta_al_limite_despues_de_filtrar(self):
        filas = [fila(i, date(2026, 12, 31)) for i in range(1, 6)]
        res, _ = self._listar(filas, en_plazo=True, limite=2)
        self.assertEqual([p["id"] for p in res], [1, 2])

    def test_sin_filtro_mantiene_el_limite_sql_y_devuelve_todo(self):
        filas = [fila(1, date(2026, 12, 31)), fila(2, date(2026, 9, 1))]
        res, cur = self._listar(filas, limite=5)
        self.assertEqual(len(res), 2)
        self.assertEqual(cur.params[-1], 5)

    def test_catalogo_excluye_estados_terminales_de_la_definicion_unica(self):
        _, cur = self._listar([], limite=5)
        for estado in ("anulado", "finalizado", "desistido", "cancelado"):
            self.assertIn(estado, cur.params)


if __name__ == "__main__":
    unittest.main()
