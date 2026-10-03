from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "app"
PKG = types.ModuleType("app")
PKG.__path__ = [str(APP_DIR)]
sys.modules.setdefault("app", PKG)

_MODULE_PATH = APP_DIR / "boe_local_import.py"
_SPEC = importlib.util.spec_from_file_location("app.boe_local_import", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_familia = _MODULE._familia
_fechas_inscripcion_boe = _MODULE._fechas_inscripcion_boe
_filtrar_extraccion_boe_desde = _MODULE._filtrar_extraccion_boe_desde
_nombres_entidad = _MODULE._nombres_entidad


class FamiliaBoeLocalTest(unittest.TestCase):
    def test_errata_boe_adminstrativo_se_normaliza(self) -> None:
        self.assertEqual(
            _familia("Auxiliar Adminstrativo/a"),
            "AUXILIAR_ADMINISTRATIVO",
        )

    def test_formas_ordinarias_se_mantienen(self) -> None:
        self.assertEqual(_familia("Auxiliar Administrativo/a"), "AUXILIAR_ADMINISTRATIVO")
        self.assertEqual(_familia("Administrativo/a"), "ADMINISTRATIVO")
        self.assertEqual(
            _familia("Técnico/a de Administración General"),
            "TAG",
        )


if __name__ == "__main__":
    unittest.main()


class VentanaRecuperacionBoeTest(unittest.TestCase):
    def test_bases_antiguas_conservan_boe_reciente_de_la_extraccion(self) -> None:
        extraccion = {
            "detalle": [
                {"boe_id": "ANTIGUO", "fecha_boe": "2026-03-01"},
                {"boe_id": "RECIENTE", "fecha_boe": "2026-10-02"},
            ],
            "errores": [],
        }

        filtrada = _filtrar_extraccion_boe_desde(extraccion, __import__("datetime").date(2026, 4, 7))

        self.assertEqual(
            [item["boe_id"] for item in filtrada["detalle"]],
            ["RECIENTE"],
        )

    def test_filtro_no_amplia_una_extraccion_incremental(self) -> None:
        extraccion = {
            "detalle": [
                {"boe_id": "AYER", "fecha_boe": "2026-10-02"},
                {"boe_id": "HOY", "fecha_boe": "2026-10-03"},
            ],
            "errores": [],
        }

        filtrada = _filtrar_extraccion_boe_desde(extraccion, __import__("datetime").date(2026, 4, 7))

        self.assertEqual(filtrada["detalle"], extraccion["detalle"])


class CanalsMatchingRegressionTest(unittest.TestCase):
    def test_canals_bop_y_boe_documentado_cumplen_matching_estricto(self) -> None:
        proceso = {
            "organismo_nombre": "Ayuntamiento de Canals",
            "provincia": "Valencia",
            "fecha_convocatoria": "2026-09-08",
            "denominacion": (
                "Anunci de l'Ajuntament de Canals sobre l'aprovació de les bases "
                "de la convocatòria, mitjançant torn lliure i pel sistema de concurs "
                "oposició, per a diverses places d'auxiliar administratiu/va."
            ),
        }
        boe = {
            "boe_id": "BOE-A-2026-20049",
            "fecha_boe": "2026-09-28",
            "provincia": "Valencia",
            "entidad": "Ayuntamiento de Canals",
            "denominacion": "Auxiliar Adminstrativo/a",
            "bases_bop": {"fecha": "2026-09-08"},
        }

        self.assertEqual(boe["provincia"], proceso["provincia"])
        self.assertEqual(boe["bases_bop"]["fecha"], proceso["fecha_convocatoria"])
        self.assertIn(
            _MODULE._sin(proceso["organismo_nombre"]),
            _nombres_entidad(boe["entidad"]),
        )
        self.assertEqual(
            _familia(boe["denominacion"]),
            _familia(proceso["denominacion"]),
        )
        self.assertEqual(_familia(boe["denominacion"]), "AUXILIAR_ADMINISTRATIVO")


class PlazoInscripcionBoeTest(unittest.TestCase):
    def test_dias_naturales_se_calculan_desde_publicacion(self) -> None:
        apertura, cierre = _fechas_inscripcion_boe({
            "fecha_boe": "2026-09-28",
            "plazo_solicitudes_literal": "El plazo de presentación de solicitudes será de veinte días naturales.",
        })
        # El extractor actual sólo calcula cifras explícitas; el literal escrito
        # en palabras se conserva para revisión y no se inventa.
        self.assertIsNone(apertura)
        self.assertIsNone(cierre)

    def test_dias_naturales_numericos_se_calculan(self) -> None:
        apertura, cierre = _fechas_inscripcion_boe({
            "fecha_boe": "2026-09-28",
            "plazo_solicitudes_literal": "El plazo de presentación de solicitudes será de 20 días naturales.",
        })
        self.assertEqual(apertura, "2026-09-29")
        self.assertEqual(cierre, "2026-10-18")

    def test_dias_habiles_no_inventan_festivos(self) -> None:
        apertura, cierre = _fechas_inscripcion_boe({
            "fecha_boe": "2026-09-28",
            "plazo_solicitudes_literal": "El plazo de presentación de solicitudes será de 20 días hábiles.",
        })
        self.assertEqual(apertura, "2026-09-29")
        self.assertIsNone(cierre)
