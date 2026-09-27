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

    def test_bop_sin_boe_sigue_pendiente(self) -> None:
        self.assertEqual(
            estado_inscripcion({"datos_json": {"origen": "BOP_VALENCIA_MUNICIPAL"}}),
            {"codigo": "PENDIENTE_BOE"},
        )
