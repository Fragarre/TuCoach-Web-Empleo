"""Una bolsa GVA está activa mientras su ficha diga que está en funcionamiento.

Regresión real: la primera versión comparaba con la frase exacta en castellano y,
al leer las fichas en valenciano (con texto de plazos detrás), cerró las 31 bolsas
vigentes a la primera lectura."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import gva_bolsas_seguimiento as mod

# Texto REAL devuelto por la ficha de una bolsa vigente (valenciano, con cola).
FASE_REAL_VA = (
    "Borsa en funcionament Termini Des de 11-09-2026 a 16-09-2026 Termini per a modificar "
    "l'o els àmbits provincials de 5 dies naturals fins al 16/09/26 a les 23.59 hores. "
    "Publicació Web de 11-09-2026 Forma de presentació Per a qualsevol consulta o acció "
    "tramitar a través de l'aplicació GVbolsas"
)


def _estado(fase):
    return {
        "fase_gva": fase,
        "bolsa_en_funcionamiento": mod.fase_en_funcionamiento(fase),
        "etapa_actual_gva": "Listado definitivo",
        "url_detalle": "https://sede.gva.es/x",
    }


class FaseTest(unittest.TestCase):
    def test_reconoce_castellano_y_valenciano(self):
        for fase in ("Bolsa en funcionamiento", "  Bolsa   en  Funcionamiento ", "Borsa en funcionament",
                     "BORSA EN FUNCIONAMENT", FASE_REAL_VA,
                     "Bolsa en funcionamiento Plazo desde 11-09-2026"):
            self.assertTrue(mod.fase_en_funcionamiento(fase), fase)

    def test_otras_fases_no_son_funcionamiento(self):
        for fase in ("Bolsa extinguida", "Borsa extingida", "Bolsa cerrada", "En constitución"):
            self.assertFalse(mod.fase_en_funcionamiento(fase), fase)

    def test_ficha_real_no_se_da_por_inactiva(self):
        self.assertFalse(mod.bolsa_ya_no_funciona(_estado(FASE_REAL_VA)))

    def test_fase_ilegible_no_cierra(self):
        for e in (_estado(None), _estado(""), _estado("   "), None, {}):
            self.assertFalse(mod.bolsa_ya_no_funciona(e))


class DosLecturasTest(unittest.TestCase):
    def _accion(self, tipo, actual, anterior):
        return {"proceso_id": 7, "accion": tipo, "estado_actual": actual, "estado_anterior": anterior}

    def test_primera_lectura_nunca_cierra(self):
        a = self._accion("BASELINE_GVA_DIRECTO", _estado("Bolsa extinguida"), None)
        self.assertFalse(mod.debe_cerrar_bolsa(a))

    def test_una_sola_lectura_inactiva_no_cierra(self):
        a = self._accion("CAMBIO_GVA_DIRECTO", _estado("Bolsa extinguida"), _estado(FASE_REAL_VA))
        self.assertFalse(mod.debe_cerrar_bolsa(a))

    def test_dos_lecturas_inactivas_cierran(self):
        for tipo in ("CAMBIO_GVA_DIRECTO", "SIN_CAMBIOS_GVA_DIRECTO", "ACTUALIZAR_BASELINE_GVA_DIRECTO"):
            a = self._accion(tipo, _estado("Bolsa extinguida"), _estado("Bolsa extinguida"))
            self.assertTrue(mod.debe_cerrar_bolsa(a), tipo)

    def test_acciones_que_no_son_de_bolsa_directa_no_cierran(self):
        a = self._accion("PUBLICAR", _estado("Bolsa extinguida"), _estado("Bolsa extinguida"))
        self.assertFalse(mod.debe_cerrar_bolsa(a))

    def test_planificar_sin_cambios_conserva_estado_anterior(self):
        e = _estado("Bolsa extinguida")
        e["contenido_hash"] = "h"
        proceso = {"id": 7, "identificador_estable": "GVA:7", "id_emp": 7,
                   "datos_json": {"seguimiento_gva_directo": dict(e)}}
        accion = mod._planificar_gva_directa(proceso, dict(e))
        self.assertEqual(accion["accion"], "SIN_CAMBIOS_GVA_DIRECTO")
        self.assertTrue(mod.debe_cerrar_bolsa(accion))


class CierreTest(unittest.TestCase):
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

    def test_si_ya_estaba_cerrada_no_hace_nada_mas(self):
        c = self._cursor(estado_actual="FINALIZADO", rowcount=0)
        accion = {"proceso_id": 7, "accion": "CAMBIO_GVA_DIRECTO", "estado_actual": _estado("Bolsa extinguida")}
        self.assertFalse(mod._cerrar_bolsa_por_fase(c, accion, notificable=True))
        self.assertEqual(len(c.execute.call_args_list), 2)


if __name__ == "__main__":
    unittest.main()
