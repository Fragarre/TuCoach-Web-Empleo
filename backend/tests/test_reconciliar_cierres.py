from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
_PKG = sys.modules.get("app")
if _PKG is None or not hasattr(_PKG, "__path__"):
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


_cargar("ciclo_vida")
rc = _cargar("reconciliar_cierres")
rc._jsonb = lambda valor: valor  # sin psycopg en el entorno de pruebas


class CursorFalso:
    """Simula las 2 lecturas y registra las escrituras."""

    def __init__(self, procesos, publicaciones):
        self.procesos, self.publicaciones = procesos, publicaciones
        self.escrituras: list[tuple[str, tuple]] = []
        self.rowcount = 1
        self._ultimo = ""

    def execute(self, sql, params=()):
        self._ultimo = sql
        if sql.lstrip().upper().startswith(("UPDATE", "INSERT")):
            self.escrituras.append((sql, params))

    def fetchall(self):
        if "FROM procesos" in self._ultimo:
            return self.procesos
        return self.publicaciones


def _abrir(cursor):
    @contextmanager
    def ctx():
        yield cursor
    return ctx


def pub(i, pid, titulo, fecha="2026-05-01"):
    return {"id": i, "proceso_id": pid, "titulo": titulo, "fecha_publicacion": fecha}


RESULTADO = "Relación definitiva de aspirantes que han superado el proceso selectivo"


class DecidirTest(unittest.TestCase):
    def test_activo_con_resultado_se_cierra(self) -> None:
        d = rc.decidir({"estado": "EN_CURSO", "tipo_proceso": "Oposición"}, [pub(7, 1, RESULTADO)])
        self.assertEqual((d["accion"], d["estado"], d["publicacion_id"]), ("CERRAR", "FINALIZADO", 7))

    def test_activo_sin_resultado_no_se_toca(self) -> None:
        d = rc.decidir({"estado": "EN_CURSO", "tipo_proceso": "Oposición"},
                       [pub(1, 1, "Lista provisional de admitidos")])
        self.assertIsNone(d)

    def test_terminal_de_otra_fuente_no_se_reabre(self) -> None:
        d = rc.decidir({"estado": "FINALIZADO", "tipo_proceso": "Oposición", "datos_json": {}}, [])
        self.assertIsNone(d)

    def test_cierre_propio_sin_evidencia_se_reabre(self) -> None:
        proceso = {"estado": "FINALIZADO", "tipo_proceso": "Oposición",
                   "datos_json": {"cierre": {"origen": rc.ORIGEN}}}
        self.assertEqual(rc.decidir(proceso, [pub(1, 1, "Lista provisional")])["accion"], "REABRIR")

    def test_cierre_propio_con_evidencia_se_mantiene(self) -> None:
        proceso = {"estado": "FINALIZADO", "tipo_proceso": "Oposición",
                   "datos_json": {"cierre": {"origen": rc.ORIGEN}}}
        self.assertIsNone(rc.decidir(proceso, [pub(1, 1, RESULTADO)]))

    def test_bolsa_no_se_cierra_por_relacion_de_aprobados(self) -> None:
        d = rc.decidir({"estado": "EN_CURSO", "tipo_proceso": "Bolsa de trabajo"},
                       [pub(1, 1, "Relación definitiva de aprobados del proceso selectivo")])
        self.assertIsNone(d)


class ReconciliarTest(unittest.TestCase):
    def _escenario(self):
        procesos = [
            {"id": 1, "estado": "EN_CURSO", "tipo_proceso": "Oposición", "datos_json": {}},
            {"id": 2, "estado": "EN_CURSO", "tipo_proceso": "Oposición", "datos_json": {}},
            {"id": 3, "estado": "FINALIZADO", "tipo_proceso": "Oposición", "datos_json": {}},
        ]
        publicaciones = [
            pub(10, 1, RESULTADO, "2026-06-01"),
            pub(11, 2, "Anulación de la pregunta 45 del primer ejercicio"),
            pub(12, 3, RESULTADO),
        ]
        return CursorFalso(procesos, publicaciones)

    def test_solo_revision_no_escribe(self) -> None:
        cur = self._escenario()
        r = rc.reconciliar_cierres(aplicar=False, abrir=_abrir(cur))
        self.assertEqual(r["modo"], "SOLO_REVISION")
        self.assertEqual((r["cierres"], r["aplicadas"], cur.escrituras), (1, 0, []))
        self.assertEqual(r["detalle"][0]["proceso_id"], 1)

    def test_aplicar_cierra_solo_el_proceso_con_resultado(self) -> None:
        cur = self._escenario()
        r = rc.reconciliar_cierres(aplicar=True, abrir=_abrir(cur))
        self.assertEqual((r["cierres"], r["aplicadas"], r["cierres_por_estado"]), (1, 1, {"FINALIZADO": 1}))
        update, insert = cur.escrituras
        self.assertIn("UPDATE procesos", update[0])
        self.assertEqual(update[1][0], "FINALIZADO")
        self.assertEqual(update[1][2], 1)
        self.assertEqual(update[1][1]["cierre"]["publicacion_id"], 10)
        self.assertIn("INSERT INTO cambios", insert[0])
        self.assertIs(insert[1][-1], True)  # significativo fuera del modo histórico

    def test_historico_no_genera_cambios_significativos(self) -> None:
        cur = self._escenario()
        rc.reconciliar_cierres(aplicar=True, historico=True, abrir=_abrir(cur))
        self.assertIs(cur.escrituras[1][1][-1], False)

    def test_update_protege_contra_carrera(self) -> None:
        cur = self._escenario()
        rc.reconciliar_cierres(aplicar=True, abrir=_abrir(cur))
        self.assertIn("NOT IN ('", cur.escrituras[0][0])

    def test_sin_filas_afectadas_no_cuenta_ni_inserta_cambio(self) -> None:
        cur = self._escenario()
        cur.rowcount = 0
        r = rc.reconciliar_cierres(aplicar=True, abrir=_abrir(cur))
        self.assertEqual(r["aplicadas"], 0)
        self.assertEqual(len(cur.escrituras), 1)

    def test_sin_candidatos(self) -> None:
        cur = CursorFalso([{"id": 3, "estado": "FINALIZADO", "tipo_proceso": "X", "datos_json": {}}], [])
        self.assertEqual(rc.reconciliar_cierres(abrir=_abrir(cur))["cierres"], 0)


if __name__ == "__main__":
    unittest.main()
