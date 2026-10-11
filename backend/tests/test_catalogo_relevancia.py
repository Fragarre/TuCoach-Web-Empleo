"""Catálogo: las convocatorias locales sin actividad en un año se ocultan."""
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.procesos import (  # noqa: E402
    MESES_SIN_ACTIVIDAD_LOCAL,
    TIPOS_ORGANISMO_LOCALES,
    _condiciones_catalogo,
)


class RelevanciaLocalTest(unittest.TestCase):
    def test_regla_presente_y_solo_para_organismos_locales(self):
        sql, params = _condiciones_catalogo()
        self.assertIn("o.tipo NOT IN ('AYUNTAMIENTO', 'DIPUTACION')", sql)
        self.assertIn(f"INTERVAL '{MESES_SIN_ACTIVIDAD_LOCAL} months'", sql)
        self.assertEqual(TIPOS_ORGANISMO_LOCALES, ("AYUNTAMIENTO", "DIPUTACION"))

    def test_marcadores_y_parametros_coinciden(self):
        sql, params = _condiciones_catalogo()
        self.assertEqual(sql.count("%s"), len(params))

    def test_se_pondera_toda_la_actividad_conocida(self):
        sql, _ = _condiciones_catalogo()
        for campo in ("p.fecha_convocatoria", "p.fecha_apertura", "p.fecha_cierre",
                      "p.ultima_publicacion_at", "pub_act.fecha_publicacion"):
            self.assertIn(campo, sql)
        # Sin ninguna fecha conocida no se oculta (COALESCE a hoy).
        self.assertIn("), CURRENT_DATE) >=", sql)

    def test_el_ciclo_de_vida_sigue_mandando(self):
        sql, _ = _condiciones_catalogo()
        self.assertIn("NOT IN", sql)
        self.assertIn("p.estado", sql)


if __name__ == "__main__":
    unittest.main()
