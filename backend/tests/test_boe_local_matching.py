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
_filtrar_extraccion_boe_desde = _MODULE._filtrar_extraccion_boe_desde
_nombres_entidad = _MODULE._nombres_entidad
_buscar_proceso_evento_documental = _MODULE._buscar_proceso_evento_documental
_estado_despues_evento_documental = _MODULE._estado_despues_evento_documental

# El extractor se carga aparte para probar la clasificación documental BOE.
_EXTRACTOR_PATH = APP_DIR / "boe_local_extractor.py"
_EXTRACTOR_SPEC = importlib.util.spec_from_file_location("app.boe_local_extractor", _EXTRACTOR_PATH)
assert _EXTRACTOR_SPEC and _EXTRACTOR_SPEC.loader
_EXTRACTOR = importlib.util.module_from_spec(_EXTRACTOR_SPEC)
_EXTRACTOR_SPEC.loader.exec_module(_EXTRACTOR)
_tipo_documento_boe = _EXTRACTOR._tipo_documento_boe
_extraer_plaza = _EXTRACTOR._extraer_plaza
_extraer_resolucion_anterior = _EXTRACTOR._extraer_resolucion_anterior


class ExtractorBoeLocalRegressionTest(unittest.TestCase):
    def test_errata_boe_adminstrativo_no_se_descarta(self) -> None:
        plaza = _extraer_plaza("Dos plazas de Auxiliar Adminstrativo/a, por turno libre.")
        self.assertIsNotNone(plaza)
        self.assertEqual(plaza["ambito_administrativo"], "SI")
        self.assertEqual(plaza["plazas"], 2)


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


class TipoDocumentoBoeRegressionTest(unittest.TestCase):
    def test_detecta_dejar_sin_efecto_como_anulacion(self) -> None:
        self.assertEqual(
            _tipo_documento_boe(
                "Resolución referente a la convocatoria",
                "Se deja sin efecto el anuncio publicado anteriormente.",
            ),
            "ANULACION",
        )

    def test_detecta_rectificacion(self) -> None:
        self.assertEqual(
            _tipo_documento_boe(
                "Corrección de errores de la convocatoria",
                "Corrección de errores de la Resolución anterior.",
            ),
            "RECTIFICACION",
        )

    def test_convocatoria_ordinaria_no_cambia(self) -> None:
        self.assertEqual(
            _tipo_documento_boe(
                "Resolución referente a la convocatoria",
                "Se convocan dos plazas de Auxiliar Administrativo.",
            ),
            "CONVOCATORIA",
        )


class ResolucionAnteriorBoeRegressionTest(unittest.TestCase):
    def test_extrae_fecha_de_resolucion_anulada(self) -> None:
        self.assertEqual(
            _extraer_resolucion_anterior(
                "Resolución referente a la convocatoria",
                "Se deja sin efecto la de 21 de enero de 2026, referente a la convocatoria.",
            ),
            {"fecha_resolucion": "2026-01-21"},
        )

    def test_no_inventa_referencia_si_no_esta_expresada(self) -> None:
        self.assertIsNone(
            _extraer_resolucion_anterior(
                "Resolución referente a la convocatoria",
                "Se deja sin efecto el anuncio publicado anteriormente.",
            )
        )


class _CursorEventoFake:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class EstadoEventoDocumentalRegressionTest(unittest.TestCase):
    def test_anulacion_pasa_a_anulado(self) -> None:
        self.assertEqual(_estado_despues_evento_documental("ANULACION", "ABIERTO"), "ANULADO")

    def test_rectificacion_conserva_estado(self) -> None:
        self.assertEqual(_estado_despues_evento_documental("RECTIFICACION", "ABIERTO"), "ABIERTO")

class EventoDocumentalImportacionRegressionTest(unittest.TestCase):
    def test_un_candidato_se_identifica(self) -> None:
        cursor = _CursorEventoFake(
            [{
                "id": 123,
                "identificador_estable": "BOP:123",
                "organismo_nombre": "Ayuntamiento de Orihuela",
                "organismo_provincia": "Alicante",
            }]
        )
        candidatos = _buscar_proceso_evento_documental(
            cursor,
            organismo_nombre="Ayuntamiento de Orihuela",
            provincia="Alicante",
            resolucion_anterior={"fecha_resolucion": "2026-01-21"},
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]["id"], 123)
        self.assertEqual(cursor.params, ("2026-01-21",))

    def test_busca_por_fecha_resolucion_y_no_fecha_publicacion(self) -> None:
        cursor = _CursorEventoFake([])
        _buscar_proceso_evento_documental(
            cursor,
            organismo_nombre="Ayuntamiento de Orihuela",
            provincia="Alicante",
            resolucion_anterior={"fecha_resolucion": "2026-01-21"},
        )
        self.assertIn(
            "pub.datos_json->>'fecha_resolucion'=%s",
            cursor.sql,
        )
        self.assertNotIn(
            "pub.fecha_publicacion=%s",
            cursor.sql,
        )
        self.assertEqual(cursor.params, ("2026-01-21",))

    def test_matching_provincia_con_acento(self) -> None:
        cursor = _CursorEventoFake(
            [{
                "id": 456,
                "identificador_estable": "BOP:456",
                "organismo_nombre": "Ayuntamiento de Castelló",
                "organismo_provincia": "Castellón",
            }]
        )
        candidatos = _buscar_proceso_evento_documental(
            cursor,
            organismo_nombre="Ayuntamiento de Castelló",
            provincia="Castellon",
            resolucion_anterior={"fecha_resolucion": "2026-01-21"},
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]["id"], 456)

    def test_cero_candidatos_queda_en_revision(self) -> None:
        candidatos = []
        self.assertNotEqual(len(candidatos), 1)

    def test_varios_candidatos_quedan_en_revision(self) -> None:
        candidatos = [{"id": 123}, {"id": 456}]
        self.assertNotEqual(len(candidatos), 1)


class EstadoEventoHelperIntegrationTest(unittest.TestCase):
    def test_anulacion_y_rectificacion_se_distinguen(self) -> None:
        self.assertEqual(_estado_despues_evento_documental("ANULACION", "ABIERTO"), "ANULADO")
        self.assertEqual(_estado_despues_evento_documental("RECTIFICACION", "ABIERTO"), "ABIERTO")
