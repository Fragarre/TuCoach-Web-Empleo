"""Ciclo periódico: exclusión mutua, ventanas por fuente y trazabilidad."""
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.periodic import ejecutar_periodico


ORGANISMOS_FUNCIONES = {
    "importar_bop_valencia", "importar_municipales_bop", "importar_bop_castellon",
    "bootstrap_otras_entidades_alicante", "importar_bop_alicante", "importar_gva_estatal",
    "persistir_bolsas_gva_complementarias", "persistir_adc_gva", "persistir_cesiones_gva",
}


@contextmanager
def parchear(parches):
    """Las funciones de cada organismo viven ahora en app.organismos_cron; el
    resto (ciclos, BOE, notificaciones) siguen en app.periodic."""
    with ExitStack() as pila:
        pila.enter_context(patch.multiple(
            "app.periodic", **{k: v for k, v in parches.items() if k not in ORGANISMOS_FUNCIONES}))
        pila.enter_context(patch.multiple(
            "app.organismos_cron", **{k: v for k, v in parches.items() if k in ORGANISMOS_FUNCIONES}))
        yield

HOY = date(2026, 10, 10)
FUENTES = (
    "importar_bop_valencia", "importar_municipales_bop", "importar_bop_castellon",
    "bootstrap_otras_entidades_alicante", "importar_bop_alicante",
    "_recuperar_boe_pendientes_activos", "previsualizar_importacion_boe_local",
    "importar_gva_estatal", "persistir_bolsas_gva_complementarias", "persistir_adc_gva",
    "persistir_cesiones_gva", "reconciliar_cierres",
)


def _parches(**extra):
    base = {nombre: MagicMock(return_value={"insertados": 1}) for nombre in FUENTES}
    base.update(
        iniciar_ciclo=MagicMock(return_value=(7, "INICIADO")),
        finalizar_ciclo=MagicMock(),
        registrar_ejecuciones=MagicMock(),
        ultimos_exitos=MagicMock(return_value={}),
        ids_oportunidades_visibles=MagicMock(return_value=set()),
        ids_novedades_seguimiento=MagicMock(return_value=set()),
        filtrar_nuevas_oportunidades_notificables=MagicMock(return_value=set()),
        registrar_nuevas_oportunidades=MagicMock(return_value=[]),
        preparar_envios_eventos=MagicMock(return_value=0),
        enviar_envios_pendientes=MagicMock(return_value={}),
        enviar_avisos_novedades=MagicMock(return_value={}),
    )
    base.update(extra)
    return base


class CicloPeriodicoTest(unittest.TestCase):
    def test_ciclo_en_curso_omite_sin_tocar_fuentes(self):
        p = _parches(iniciar_ciclo=MagicMock(return_value=(None, "OMITIDO_CICLO_EN_CURSO")))
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY)
        self.assertTrue(r["ciclo"]["omitido"])
        self.assertEqual(r["resumen_fuentes"]["total"], 0)
        p["importar_bop_castellon"].assert_not_called()
        p["finalizar_ciclo"].assert_not_called()

    def test_solo_revision_no_reserva_ciclo(self):
        p = _parches()
        with parchear(p):
            r = ejecutar_periodico(aplicar=False, hoy=HOY)
        p["iniciar_ciclo"].assert_not_called()
        p["registrar_ejecuciones"].assert_not_called()
        self.assertEqual(r["modo"], "SOLO_REVISION")

    def test_ventana_se_amplia_desde_el_ultimo_exito(self):
        p = _parches(ultimos_exitos=MagicMock(return_value={"bop_castellon": HOY - timedelta(days=10)}))
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY)
        self.assertEqual(r["ventanas"]["bop_castellon"], 11)
        self.assertEqual(r["ventanas"]["bop_alicante"], 7)  # sin historial: solape mínimo
        self.assertEqual(p["importar_bop_castellon"].call_args_list[0].kwargs["desde"], HOY - timedelta(days=10))

    def test_ventana_excedida_se_limita_y_se_avisa(self):
        p = _parches(ultimos_exitos=MagicMock(return_value={"gva": HOY - timedelta(days=90)}))
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY)
        self.assertEqual(r["ventanas"]["gva"], 30)
        self.assertEqual(r["ventanas_excedidas"], {"gva": 90})

    def test_historico_ignora_ultimos_exitos(self):
        p = _parches(ultimos_exitos=MagicMock(return_value={"gva": HOY - timedelta(days=3)}))
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY, dias_solape=400, historico=True)
        self.assertEqual(r["ventanas"]["gva"], 400)
        p["ultimos_exitos"].assert_not_called()

    def test_error_de_fuente_cierra_el_ciclo_con_errores_y_lo_registra(self):
        p = _parches(importar_bop_castellon=MagicMock(side_effect=RuntimeError("caído")))
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY)
        # El mock de castellón lo usan la Diputación y los ayuntamientos.
        self.assertEqual(r["resumen_fuentes"]["errores"], 2)
        self.assertEqual(p["finalizar_ciclo"].call_args.args[1], "CON_ERRORES")
        ejecuciones = {e["fuente"]: e for e in p["registrar_ejecuciones"].call_args.args[1]}
        self.assertEqual(ejecuciones["bop_castellon"]["estado"], "ERROR")
        self.assertIn("caído", ejecuciones["bop_castellon"]["error"])
        self.assertEqual(ejecuciones["bop_alicante"]["hasta_fecha"], HOY)

    def test_excepcion_inesperada_libera_el_ciclo(self):
        p = _parches(ids_oportunidades_visibles=MagicMock(side_effect=RuntimeError("bd")))
        with parchear(p), self.assertRaises(RuntimeError):
            ejecutar_periodico(aplicar=True, hoy=HOY)
        self.assertEqual(p["finalizar_ciclo"].call_args.args[1], "CON_ERRORES")

    def test_reconciliacion_se_ejecuta_la_ultima_y_con_el_modo_del_ciclo(self):
        p = _parches()
        with parchear(p):
            r = ejecutar_periodico(aplicar=True, hoy=HOY)
        self.assertEqual(list(r["estado_fuentes"])[-1], "reconciliacion_cierres")
        p["reconciliar_cierres"].assert_called_once_with(aplicar=True, historico=False)


if __name__ == "__main__":
    unittest.main()
