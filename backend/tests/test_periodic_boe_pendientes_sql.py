from pathlib import Path
import unittest


PERIODIC = Path(__file__).resolve().parents[1] / "app" / "periodic.py"


class PeriodicBoePendientesSqlTest(unittest.TestCase):
    def test_dval_sin_fecha_convocatoria_tiene_fallback_bop(self):
        texto = PERIODIC.read_text(encoding="utf-8")
        self.assertIn("AS fecha_bases", texto)
        self.assertIn("p.identificador_estable LIKE 'DVAL:%'", texto)
        self.assertIn('fecha_bases=proceso["fecha_bases"]', texto)
        self.assertNotIn("AND p.fecha_convocatoria IS NOT NULL", texto)
        self.assertIn("LOWER(COALESCE(pub.titulo,'')) LIKE '%bases%'", texto)
        self.assertIn("COALESCE(pub.datos_json->>'es_convocatoria_base','false')='true'", texto)


if __name__ == "__main__":
    unittest.main()
