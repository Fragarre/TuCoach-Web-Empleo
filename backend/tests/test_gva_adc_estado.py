from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gva_adc import (
    _cierre_desde_texto_anuncio,
    _es_promocion_interna,
    _estado_accionable,
    _estado_proceso_adc,
    _extraer_bolsas_texto_oficial,
    _numero_adc,
    _persistir_relaciones_adc_bolsas,
    _publicacion_adc_en_bolsa,
    _relacion_bolsas_efectiva,
)


class EstadoProcesoAdcTest(unittest.TestCase):
    def test_promocion_interna_se_detecta_en_castellano_y_valenciano(self) -> None:
        self.assertTrue(_es_promocion_interna("Promoción interna"))
        self.assertTrue(_es_promocion_interna("Promoció interna"))

    def test_anulacion_castellano_es_terminal(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Anulación de anuncio difícil cobertura"),
            "ANULADO",
        )

    def test_anulacion_valenciano_es_terminal(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Anul·lació de l'anunci de difícil cobertura"),
            "ANULADO",
        )

    def test_etapa_de_adjudicacion_finaliza_el_adc(self) -> None:
        self.assertEqual(_estado_proceso_adc("Adjudicación difícil cobertura"), "FINALIZADO")
        self.assertEqual(_estado_proceso_adc("Adjudicació de difícil cobertura"), "FINALIZADO")

    def test_correccion_adjudicacion_tambien_finalizada(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Corrección errores de la adjudicación difícil cobertura"),
            "FINALIZADO",
        )

    def test_etapa_de_anuncio_sin_plazo_acreditado_sigue_en_curso(self) -> None:
        self.assertEqual(_estado_proceso_adc("Anuncio difícil cobertura"), "EN_CURSO")

    def test_plazo_cerrado_finaliza_el_adc(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Adjudicación difícil cobertura", "PLAZO_CERRADO"),
            "FINALIZADO",
        )

    def test_plazo_abierto_o_pendiente_sigue_en_curso(self) -> None:
        for motivo in ("PLAZO_ABIERTO", "PLAZO_PENDIENTE", "PLAZO_NO_ACREDITADO", None):
            self.assertEqual(_estado_proceso_adc("Anuncio", motivo), "EN_CURSO")

    def test_anulacion_prevalece_sobre_plazo_cerrado(self) -> None:
        self.assertEqual(
            _estado_proceso_adc("Anulación de anuncio difícil cobertura", "PLAZO_CERRADO"),
            "ANULADO",
        )

    def test_extrae_bolsa_clonada_del_anuncio_de_servicios_sociales(self) -> None:
        texto = (
            "Les persones que poden participar són les integrants de la Borsa\n"
            "CLONADA - 1013-B. Cos C2-01. Auxiliar. Modalitat Estabilització\n"
            "Las personas que pueden participar son las integrantes Bolsa\n"
            "CLONADA - 1013-B. Cuerpo C2-01. Auxiliar."
        )
        self.assertEqual(_extraer_bolsas_texto_oficial(texto), ["1013-B"])

    def test_cierre_desde_anuncio_en_castellano_y_valenciano(self) -> None:
        self.assertEqual(
            _cierre_desde_texto_anuncio("PLAZO PRESENTACIÓN: Hasta el 17 de abril de 2026 a las 23:59 horas"),
            "2026-04-17",
        )
        self.assertEqual(
            _cierre_desde_texto_anuncio("TERMINI PRESENTACIÓ: Fins al 17 d'abril de 2026 a les 23:59 hores"),
            "2026-04-17",
        )
        self.assertEqual(
            _cierre_desde_texto_anuncio("TERMINI PRESENTACIÓ: Fins al 3 de març de 2026"),
            "2026-03-03",
        )
        self.assertIsNone(_cierre_desde_texto_anuncio("Sin plazo declarado"))

    def test_cierre_futuro_sin_apertura_es_accionable_y_pasado_no(self) -> None:
        from datetime import date, timedelta
        manana = (date.today() + timedelta(days=3)).isoformat()
        ayer = (date.today() - timedelta(days=1)).isoformat()
        self.assertTrue(_estado_accionable(None, manana, None)["accionable"])
        self.assertEqual(_estado_accionable(None, ayer, None)["motivo_accionabilidad"], "PLAZO_CERRADO")
        self.assertFalse(_estado_accionable(None, None, None)["accionable"])

    def test_extrae_bolsa_singular_de_adjudicacion(self) -> None:
        texto = "A1-01. Cuerpo Superior de Administración (Val, Al, Cas). Bolsa 444"
        self.assertEqual(_extraer_bolsas_texto_oficial(texto), ["444"])

    def test_extrae_borsa_singular_en_valenciano(self) -> None:
        texto = "A1-01. Cos Superior d'Administració (Val, Al, Cas). Borsa 444"
        self.assertEqual(_extraer_bolsas_texto_oficial(texto), ["444"])

    def test_extrae_lista_plural_de_bolsas(self) -> None:
        texto = "Bolsas: 241, 332, 435, 677, 679, 804, 890, 891, 913, 914\nTexto posterior"
        self.assertEqual(
            _extraer_bolsas_texto_oficial(texto),
            ["241", "332", "435", "677", "679", "804", "890", "891", "913", "914"],
        )
    def test_extrae_listado_estructurado_de_bolsas_adc(self) -> None:
        texto = """
        Únicamente podrán participar las personas integrantes de las siguientes bolsas que
        figuren en algún ámbito como disponibles:

        804-B. C1-01, Administrativo. Modalidad Estabilización.
        679-B. Cuerpo C1-01. Administrativo.
        435-B. Cuerpo C1-01 administrativos.
        332-B. Cuerpo C1-01, administrativos PI.
        241-B. Cuerpo Administrativo C1-01.
        913-L. Cuerpo C1-01. Administrativos.
        914-L. Cuerpo C1-01. Administrativos.
        891-L. Cuerpo C1-01. Administrativos.
        890-L. Cuerpo C1-01. Administrativos.
        677-L. Cuerpo C1-01 administrativos.

        El plazo de presentación de solicitudes será el establecido.
        """
        self.assertEqual(
            _extraer_bolsas_texto_oficial(texto),
            [
                "241-B",
                "332-B",
                "435-B",
                "677-L",
                "679-B",
                "804-B",
                "890-L",
                "891-L",
                "913-L",
                "914-L",
            ],
        )

    def test_numero_adc_formato_estandar(self) -> None:
        self.assertEqual(_numero_adc("ADC 172/26 A1-01"), "172/26")

    def test_numero_adc_formato_edu_pas(self) -> None:
        self.assertEqual(
            _numero_adc("ADC-EDU-PAS 1/26 C1-01 Administrativo/a"),
            "1/26",
        )

    def test_listado_adc_204_26_sin_cierre_plazo_y_lineas_desordenadas(self) -> None:
        texto = """
        ¡¡IMPORTANTE!! SÓLO PUEDEN SOLICITAR LOS PUESTOS, LOS
        INTEGRANTES DE LAS SIGUIENTES BOLSAS (por orden):

        790-B. A2-01. Gestión. Modalidad Estabilización (OOP 2021)
        691-B. Cuerpo A2 -01. Gestión. Modalidad de Estabilización (OOP
        2017 y 2018)
        426-B. Cuerpo A2-01 gestión de administración general (OEP-15). [A2-
        01.]
        413-B. Cuerpo A2-01 Admón. General participantes en OEP-15 i LIBRE
        restringida. [A2-01.]
        832-L. C uerpo A2-01. Técnico Gestión (OOP-24). Listado aprobados
        2ej. 13 y 14/24
        834-L. Cuerpo A2-01. Técnico Gestión (OOP-23). Listado aprobados
        2ej. 8 y 28/23
        836-L. C uerpo A2-01. Técnico Gestión (OOP-22). Listado aprobados
        2ej. 1 y 2/22
        833-L. Cuerpo A2-01. Técnico Gestión (OOP-24). Listado aprobados
        1ej. 13 y 14/24

        NO PODRAN SOL·LICITAR  aquests llocs aquelles persones.
        TERMINI PRESENTACIÓ: Fins al 15 d’octubre de 2026 a les 23:59 hores
        València, 8 d’octubre de 2026
        835-L. Cuerpo A2-01. Técnico Gestión (OOP-23). Listado aprobados
        1ej. 8 y 28/23
        837-L. Cuerpo A2-01. Técnico Gestión (OOP-22). Listado aprobados
        1ej. 1 y 2/22
        NO PODRÁN SOLICITAR  estos puestos aquellas personas.
        """
        self.assertEqual(
            _extraer_bolsas_texto_oficial(texto),
            ["413-B", "426-B", "691-B", "790-B", "832-L", "833-L",
             "834-L", "835-L", "836-L", "837-L"],
        )

    def test_conserva_bolsas_previas_si_etapa_actual_no_aporta_bolsas(self) -> None:
        bolsas, evidencia = _relacion_bolsas_efectiva(
            [],
            None,
            {
                "bolsas_relacionadas": ["241-B", "804-B", "913-L"],
                "evidencia_relacion": "PDF_OFICIAL",
            },
        )
        self.assertEqual(bolsas, ["241-B", "804-B", "913-L"])
        self.assertEqual(evidencia, "PDF_OFICIAL")

    def test_persistencia_relacion_resuelta_guarda_proceso_bolsa(self) -> None:
        cursor = MagicMock()

        _persistir_relaciones_adc_bolsas(
            cursor,
            adc_proceso_id=100,
            referencias=["804-B"],
            relaciones={
                "resueltas": {"804-B": 200},
                "ambiguas": {},
                "no_resueltas": [],
            },
            evidencia="PDF_OFICIAL",
        )

        self.assertEqual(cursor.execute.call_count, 1)
        parametros = cursor.execute.call_args.args[1]
        self.assertEqual(parametros, (100, "804-B", 200, "PDF_OFICIAL"))
        self.assertIn("ON CONFLICT", cursor.execute.call_args.args[0])

    def test_persistencia_relacion_no_resuelta_conserva_referencia(self) -> None:
        cursor = MagicMock()

        _persistir_relaciones_adc_bolsas(
            cursor,
            adc_proceso_id=100,
            referencias=["241-B"],
            relaciones={
                "resueltas": {},
                "ambiguas": {},
                "no_resueltas": ["241-B"],
            },
            evidencia="PDF_OFICIAL",
        )

        self.assertEqual(cursor.execute.call_count, 1)
        parametros = cursor.execute.call_args.args[1]
        self.assertEqual(parametros, (100, "241-B", None, "PDF_OFICIAL"))


class _CursorPublicaciones:
    """Tabla publicaciones simulada con la restricción real: (fuente, referencia,
    url) único en toda la tabla."""

    def __init__(self) -> None:
        self.filas: list[tuple] = []
        self._ultimo = None

    def execute(self, sql, params=()):
        if "SELECT" in sql:
            proceso_id, fuente_id, referencia = params
            self._ultimo = next(
                ({"id": 1} for f in self.filas
                 if (f[0], f[1], f[2]) == (proceso_id, fuente_id, referencia)),
                None,
            )
        else:
            proceso_id, fuente_id, referencia, _titulo, _fecha, url = params[:6]
            if any((f[1], f[2], f[5]) == (fuente_id, referencia, url) for f in self.filas):
                raise RuntimeError("uq_publicaciones_fuente_ref_url")
            self.filas.append((proceso_id, fuente_id, referencia, _titulo, _fecha, url))
            self._ultimo = {"id": len(self.filas)}

    def fetchone(self):
        return self._ultimo


class PublicacionAdcEnVariasBolsasTest(unittest.TestCase):
    ADC = {"id_emp": 114552, "numero_adc": "204/26", "url": "https://sede.gva.es/x?id_emp=114552",
           "etapa_actual_gva": "Anuncio", "fechas_publicacion": ["2026-10-08"],
           "evidencia_relacion": "PDF_OFICIAL", "bolsas_relacionadas": ["790-B", "691-B"]}

    def test_cada_bolsa_relacionada_recibe_su_novedad_sin_chocar(self) -> None:
        cur = _CursorPublicaciones()
        resultados = [
            _publicacion_adc_en_bolsa(cur, bolsa_id=b, fuente_id=11, adc=self.ADC, huella="h")
            for b in (66, 56, 45)
        ]
        self.assertEqual(resultados, [True, True, True])
        self.assertEqual(len({f[2] for f in cur.filas}), 3)

    def test_repetir_la_ejecucion_no_duplica(self) -> None:
        cur = _CursorPublicaciones()
        for _ in range(2):
            _publicacion_adc_en_bolsa(cur, bolsa_id=66, fuente_id=11, adc=self.ADC, huella="h")
        self.assertEqual(len(cur.filas), 1)


if __name__ == "__main__":
    unittest.main()
