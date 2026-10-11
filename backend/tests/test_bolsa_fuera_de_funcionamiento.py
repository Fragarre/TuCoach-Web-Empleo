"""Una bolsa GVA está activa mientras su ficha diga «Bolsa en funcionamiento»."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import gva_bolsas_seguimiento as mod


def _estado(fase, funciona=None):
    return {
        "fase_gva": fase,
        "bolsa_en_funcionamiento": (
            " ".join(str(fase or "").lower().split()) == "bolsa en funcionamiento"
            if funciona is None else funciona
        ),
        "etapa_actual_gva": "Listado definitivo",
        "url_detalle": "https://sede.gva.es/x",
    }


class BolsaFueraDeFuncionamientoTest(unittest.TestCase):
    def test_en_funcionamiento_no_se_cierra(self):
        self.assertFalse(mod.bolsa_ya_no_funciona(_estado("Bolsa en funcionamiento")))
        self.assertFalse(mod.bolsa_ya_no_funciona(_estado("  Bolsa   en  Funcionamiento ")))

    def test_otra_fase_la_cierra(self):
        self.assertTrue(mod.bolsa_ya_no_funciona(_estado("Bolsa extinguida")))

    def test_sin_fase_legible_no_se_cierra(self):
        for e in (_estado(None), _estado(""), _estado("   "), None, {}):
            self.assertFalse(mod.bolsa_ya_no_funciona(e))

    def _cursor(self, estado_actual="EN_CURSO", rowcount=1):
        c = MagicMock()
        c.fetchone.return_value = {"estado": estado_actual}
        c.rowcount = rowcount
        return c

    def test_cierra_y_registra_el_cambio(self):
        c = self._cursor()
        accion = {"proceso_id": 7, "accion": "CAMBIO_GVA_DIRECTO", "estado_actual": _estado("Bolsa extinguida")}
        self.assertTrue(mod._cerrar_bolsa_por_fase(c, accion, notificable=True))
        update_sql, update_params = c.execute.call_args_list[1].args
        self.assertIn("UPDATE procesos", update_sql)
        self.assertEqual(update_params[0], "FINALIZADO")
        self.assertEqual(update_params[2], 7)
        insert_params = c.execute.call_args_list[2].args[1]
        self.assertEqual(insert_params[:3], (7, "EN_CURSO", "FINALIZADO"))
        self.assertTrue(insert_params[4])  # significativo

    def test_linea_base_es_silenciosa(self):
        c = self._cursor()
        accion = {"proceso_id": 7, "accion": "BASELINE_GVA_DIRECTO", "estado_actual": _estado("Bolsa extinguida")}
        self.assertTrue(mod._cerrar_bolsa_por_fase(c, accion, notificable=False))
        self.assertFalse(c.execute.call_args_list[2].args[1][4])

    def test_si_ya_estaba_cerrada_no_hace_nada_mas(self):
        c = self._cursor(estado_actual="FINALIZADO", rowcount=0)
        accion = {"proceso_id": 7, "accion": "CAMBIO_GVA_DIRECTO", "estado_actual": _estado("Bolsa extinguida")}
        self.assertFalse(mod._cerrar_bolsa_por_fase(c, accion, notificable=True))
        self.assertEqual(len(c.execute.call_args_list), 2)  # SELECT + UPDATE, sin INSERT


if __name__ == "__main__":
    unittest.main()
