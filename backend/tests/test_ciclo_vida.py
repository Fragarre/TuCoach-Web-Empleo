"""Pruebas de la definición única de ciclo de vida (módulo puro, solo stdlib)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "app" / "ciclo_vida.py"
_SPEC = importlib.util.spec_from_file_location("ciclo_vida", _PATH)
assert _SPEC and _SPEC.loader
cv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cv)

F, A, D = "FINALIZADO", "ANULADO", "DESISTIDO"

# (título, tipo_proceso, estado esperado | None)
CASOS = [
    # --- Resultado final: BAJA -------------------------------------------
    ("Resolución por la que se aprueba la relación definitiva de aspirantes que han superado el proceso selectivo de Administrativo, turno libre", "Oposición", F),
    ("Relació d'aspirants aprovats del procés selectiu", "Oposició", F),
    ("Relación definitiva de personas aprobadas en el proceso selectivo para ingreso en el Cuerpo Administrativo", "Oposición", F),
    ("Propuesta de nombramiento de funcionarios de carrera, subgrupo C1, Administrativo", "Oposición", F),
    ("Llista definitiva d'aprovats", None, F),
    ("Resolución por la que se publica la relación de aspirantes que han superado las pruebas selectivas para ingreso en el Cuerpo Administrativo", None, F),
    ("Propuesta del tribunal calificador con la relación de aspirantes aprobados", "Concurso-oposición", F),
    ("Resultado final del proceso selectivo", None, F),
    ("Nombramiento como funcionario de carrera", "Oposición", F),
    ("Nomenament de funcionaris de carrera", "Oposició", F),
    ("Anuncio sobre finalización del proceso selectivo", "Oposición", F),
    ("Toma de posesión de las personas nombradas", "Oposición", F),
    ("Adjudicación definitiva de destinos", "Oposición", F),
    ("Resolución por la que se declara desierto el proceso selectivo", None, F),
    ("Resolució per la qual es declara desert el procés selectiu", None, F),
    ("RESOLUCIÓN de 15 de septiembre de 2026, de la Conselleria, por la que se publica la relación definitiva de personas aspirantes que han superado el proceso selectivo, por el sistema de acceso libre, para ingreso en el Cuerpo Superior Administrativo C1-01", None, F),
    ("Resolución de la Alcaldía por la que se aprueba la lista definitiva de aprobados en la oposición de Auxiliar Administrativo", None, F),
    # --- Anulación / desistimiento DEL PROCESO: BAJA -----------------------
    ("Anulación de la convocatoria", "Oposición", A),
    ("Resolución por la que se anula la convocatoria de pruebas selectivas", None, A),
    ("Anul·lació de la convocatòria", None, A),
    ("Resolución que deja sin efecto la convocatoria", None, A),
    ("Anulación de la bolsa", "Bolsa de trabajo", A),
    ("Desistimiento de la convocatoria", "Oposición", D),
    ("Desistiment del procés selectiu", "Oposició", D),
    ("Resolución por la que se desiste de la convocatoria de Administrativo, turno libre", None, D),
    # --- NO dan de baja ---------------------------------------------------
    ("Anulación de la pregunta 45 del primer ejercicio", "Oposición", None),
    ("Anulación de la plaza 3 de la convocatoria de Administrativo", None, None),
    ("Corrección de errores de la resolución sobre anulación de un tribunal calificador", None, None),
    ("Resolución de admisión de desistimiento de un aspirante en el proceso selectivo", None, None),
    ("Relación provisional de aspirantes aprobados", "Oposición", None),
    ("Relación de aspirantes que han superado el primer ejercicio", "Oposición", None),
    ("Resultados del primer ejercicio de la oposición", "Oposición", None),
    ("Resolución por la que se publican las calificaciones definitivas del segundo ejercicio", "Oposición", None),
    ("Resolución por la que se hace pública la relación de aprobados del primer ejercicio", None, None),
    ("Resolución por la que se aprueba la lista de aspirantes aprobados en el concurso-oposición, fase de concurso", None, None),
    ("Relación de aprobados de la fase de oposición", "Concurso-oposición", None),
    ("Relación definitiva de personas admitidas y fecha de examen", "Oposición", None),
    ("Lista provisional de admitidos y excluidos", None, None),
    ("Designación del tribunal calificador", None, None),
    ("Se anula la lista de aprobados publicada el día anterior", None, None),
    ("Se declara desierta una plaza de la convocatoria de Administrativo", None, None),
    ("Acuerdo de aprobación de la oferta de empleo público ejercicio 2025 con relación de plazas", None, None),
    ("Nombramiento como funcionario interino", None, None),
    ("Propuesta de nombramiento de personal funcionario interino", None, None),
    ("Constitución de bolsa de trabajo de administrativos", "Bolsa de trabajo", None),
    ("Aprobación definitiva de la bolsa de auxiliares administrativos", "Bolsa de trabajo", None),
    ("Relación de aprobados para la bolsa de trabajo", "Bolsa de trabajo", None),
    ("Finalización del plazo de presentación de solicitudes", None, None),
]


class ClasificadorCierreTest(unittest.TestCase):
    def test_tabla_de_casos(self) -> None:
        for titulo, tipo, esperado in CASOS:
            with self.subTest(titulo=titulo):
                self.assertEqual(cv.clasificar_evento_terminal(tipo, titulo), esperado)

    def test_compatibilidad_con_funcion_antigua(self) -> None:
        self.assertEqual(cv.clasificar_evento_terminal("Oposición", "Anulación de la convocatoria"), A)
        self.assertIsNone(cv.clasificar_evento_terminal("Oposición", None))
        self.assertIsNone(cv.clasificar_evento_terminal(None, ""))

    def test_evento_incluye_regla_para_trazabilidad(self) -> None:
        ev = cv.clasificar_evento("Oposición", "Relación definitiva de aprobados del proceso selectivo")
        self.assertEqual(ev.estado, F)
        self.assertTrue(ev.regla.startswith("RESULTADO_FINAL"))


class EtiquetaEstadoTest(unittest.TestCase):
    def test_etiquetas_de_estado(self) -> None:
        casos = {"Anulada": A, "ANULADO": A, "Anul·lada": A, "Desistido": D, "Desistit": D,
                 "Finalizado": F, "Finalitzat": F, "Resuelto": F, "Desierto": F, "Proceso finalizado": F}
        for etiqueta, esperado in casos.items():
            with self.subTest(etiqueta=etiqueta):
                self.assertEqual(cv.clasificar_etiqueta_estado(etiqueta), esperado)

    def test_texto_libre_no_es_etiqueta(self) -> None:
        for texto in ("Pendiente de la anulación de una pregunta", "En curso", "", None,
                      "Finalizado el plazo de presentación de instancias"):
            with self.subTest(texto=texto):
                self.assertIsNone(cv.clasificar_etiqueta_estado(texto))


class EstadosTest(unittest.TestCase):
    def test_terminales_y_activos(self) -> None:
        for e in ("FINALIZADO", "finalitzat", " Anulado ", "DESISTIDO", "CANCELADO"):
            self.assertTrue(cv.es_estado_terminal(e), e)
            self.assertFalse(cv.es_activo(e), e)
        for e in ("EN_CURSO", "ABIERTO", "EN_SEGUIMIENTO", "", None):
            self.assertTrue(cv.es_activo(e), e)

    def test_literal_sql_es_seguro_y_completo(self) -> None:
        self.assertTrue(cv.ESTADOS_TERMINALES_SQL_IN.startswith("('"))
        self.assertNotIn(";", cv.ESTADOS_TERMINALES_SQL_IN)
        for e in cv.ESTADOS_TERMINALES:
            self.assertIn(f"'{e}'", cv.ESTADOS_TERMINALES_SQL_IN)
        self.assertEqual(len(cv.ESTADOS_TERMINALES_SQL), len(cv.ESTADOS_TERMINALES))


class EvidenciaTest(unittest.TestCase):
    def test_sin_evidencia(self) -> None:
        pubs = [{"id": 1, "titulo": "Lista provisional de admitidos", "fecha_publicacion": "2026-01-01"}]
        self.assertIsNone(cv.elegir_evidencia(pubs, "Oposición"))

    def test_anulacion_prevalece_sobre_resultado(self) -> None:
        pubs = [
            {"id": 1, "titulo": "Relación definitiva de aprobados del proceso selectivo", "fecha_publicacion": "2026-03-01"},
            {"id": 2, "titulo": "Anulación de la convocatoria", "fecha_publicacion": "2026-02-01"},
        ]
        ev = cv.elegir_evidencia(pubs, "Oposición")
        self.assertEqual((ev["estado"], ev["publicacion_id"]), (A, 2))

    def test_gana_la_mas_reciente_a_igualdad(self) -> None:
        pubs = [
            {"id": 1, "titulo": "Nombramiento como funcionario de carrera", "fecha_publicacion": "2026-03-01"},
            {"id": 2, "titulo": "Toma de posesión", "fecha_publicacion": "2026-04-10"},
            {"id": 3, "titulo": "Propuesta de nombramiento", "fecha_publicacion": None},
        ]
        ev = cv.elegir_evidencia(pubs, "Oposición")
        self.assertEqual(ev["publicacion_id"], 2)
        self.assertEqual(ev["fecha_publicacion"], "2026-04-10")


if __name__ == "__main__":
    unittest.main()
