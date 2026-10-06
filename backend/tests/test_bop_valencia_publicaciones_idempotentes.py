from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "bop_valencia.py"
)


class BopValenciaPublicacionesIdempotentesTest(unittest.TestCase):
    def test_consulta_publicacion_con_la_misma_clave_que_la_restriccion(self):
        texto = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "WHERE fuente_id=%s AND referencia=%s AND url=%s",
            texto,
        )

    def test_insercion_tolera_colision_historica(self):
        texto = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "ON CONFLICT (fuente_id,referencia,url) DO NOTHING RETURNING id",
            texto,
        )
        self.assertIn("publicaciones_duplicadas_omitidas", texto)
