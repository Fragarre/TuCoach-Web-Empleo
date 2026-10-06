from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
import unittest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "estado_proceso.py"
_SPEC = importlib.util.spec_from_file_location("estado_proceso", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
estado_inscripcion = _MODULE.estado_inscripcion
clasificar_evento_terminal = _MODULE.clasificar_evento_terminal


class EstadoInscripcionTest(unittest.TestCase):
    def test_bases_municipales_alicante_sin_boe_indican_pendiente(self) -> None:
        self.assertEqual(
            estado_inscripcion(
                {"datos_json": {"origen": "DIPUTACION_ALICANTE_OTRAS"}},
                hoy=date(2026, 9, 27),
            ),
            {"codigo": "PENDIENTE_BOE"},
        )

    def test_fechas_oficiales_siguen_teniendo_precedencia(self) -> None:
        resultado = estado_inscripcion(
            {
                "fecha_apertura": date(2026, 9, 20),
                "fecha_cierre": date(2026, 10, 2),
                "datos_json": {"origen": "DIPUTACION_ALICANTE_OTRAS"},
            },
            hoy=date(2026, 9, 27),
        )
        self.assertEqual(resultado["codigo"], "ABIERTO")
        self.assertEqual(resultado["fecha_cierre"], date(2026, 10, 2))

    def test_boe_agregado_no_queda_pendiente(self) -> None:
        resultado = estado_inscripcion(
            {
                "datos_json": {
                    "origen": "BOP_VALENCIA_MUNICIPAL",
                    "boe_local_agregados": [
                        {
                            "boe_id": "BOE-A-2026-12345",
                            "codigo_externo": "BOE-2026-12345",
                            "fecha_boe": "2026-09-20",
                            "plazo_solicitudes_literal": "20 días hábiles",
                        }
                    ],
                }
            },
            hoy=date(2026, 9, 27),
        )
        self.assertNotEqual(resultado["codigo"], "PENDIENTE_BOE")

    def test_plazo_boe_sin_tipo_de_dia_se_calcula_como_habil(self) -> None:
        resultado = estado_inscripcion(
            {
                "fecha_boe_publicacion": date(2026, 9, 1),
                "datos_json": {"plazo_solicitudes_literal": "20 días"},
            },
            hoy=date(2026, 9, 2),
        )
        self.assertEqual(resultado["codigo"], "ABIERTO")
        self.assertEqual(resultado["fecha_cierre"], date(2026, 9, 29))
        self.assertEqual(resultado["dias_habiles"], 20)

    def test_plazo_boe_expresamente_natural_no_se_recalcula_como_habil(self) -> None:
        resultado = estado_inscripcion(
            {
                "fecha_boe_publicacion": date(2026, 9, 1),
                "datos_json": {"plazo_solicitudes_literal": "20 días naturales"},
            },
            hoy=date(2026, 9, 2),
        )
        self.assertEqual(resultado["codigo"], "PLAZO_LITERAL")
        self.assertIsNone(resultado["dias_habiles"])

    def test_boe_publicado_sin_plazo_conserva_la_fecha_de_publicacion(self) -> None:
        resultado = estado_inscripcion(
            {
                "fecha_boe_publicacion": date(2026, 9, 1),
                "datos_json": {"boe_local": {"boe_id": "BOE-A-2026-1"}},
            },
            hoy=date(2026, 9, 2),
        )
        self.assertEqual(resultado, {
            "codigo": "BOE_PUBLICADO_SIN_PLAZO",
            "fecha_boe": date(2026, 9, 1),
        })

    def test_boe_agregado_sin_plazo_conserva_su_fecha(self) -> None:
        resultado = estado_inscripcion({
            "datos_json": {"boe_local_agregados": [{
                "boe_id": "BOE-A-2026-2",
                "fecha_boe": "2026-09-02",
            }]},
        })
        self.assertEqual(resultado["codigo"], "BOE_PUBLICADO_SIN_PLAZO")
        self.assertEqual(resultado["boe_publicaciones"][0]["fecha_boe"], date(2026, 9, 2))

    def test_bop_sin_boe_sigue_pendiente(self) -> None:
        self.assertEqual(
            estado_inscripcion({"datos_json": {"origen": "BOP_VALENCIA_MUNICIPAL"}}),
            {"codigo": "PENDIENTE_BOE"},
        )


class EstadoProcesoBolsaTest(unittest.TestCase):
    def test_constitucion_bolsa_no_finaliza_su_vigencia(self) -> None:
        self.assertIsNone(
            clasificar_evento_terminal(
                "Bolsa de trabajo",
                "Constitución de bolsa de trabajo de administrativos",
            )
        )

    def test_aprobacion_definitiva_bolsa_no_finaliza_su_vigencia(self) -> None:
        self.assertIsNone(
            clasificar_evento_terminal(
                "Bolsa de trabajo",
                "Aprobación definitiva de la bolsa de auxiliares administrativos",
            )
        )

    def test_anulacion_sigue_siendo_terminal_para_bolsa(self) -> None:
        self.assertEqual(
            clasificar_evento_terminal("Bolsa de trabajo", "Anulación de la bolsa"),
            "ANULADO",
        )


    def test_publicacion_de_seguimiento_ordinaria_no_finaliza(self) -> None:
        self.assertIsNone(
            clasificar_evento_terminal(
                "Oposición",
                "Relación definitiva de personas admitidas y fecha de examen",
            )
        )

    def test_finalizacion_explicita_finaliza_proceso(self) -> None:
        self.assertEqual(
            clasificar_evento_terminal(
                "Oposición",
                "Anuncio sobre finalización del proceso selectivo",
            ),
            "FINALIZADO",
        )

    def test_nombramiento_finaliza_proceso(self) -> None:
        self.assertEqual(
            clasificar_evento_terminal(
                "Oposición",
                "Nombramiento como funcionario de carrera",
            ),
            "FINALIZADO",
        )

    def test_anulacion_finaliza_proceso_como_anulado(self) -> None:
        self.assertEqual(
            clasificar_evento_terminal(
                "Oposición",
                "Anulación de la convocatoria",
            ),
            "ANULADO",
        )
