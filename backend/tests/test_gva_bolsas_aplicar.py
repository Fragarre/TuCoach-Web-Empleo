"""Regresión: persistir_bolsas_gva_complementarias(aplicar=True) fallaba al final con
UnboundLocalError ('plan' solo existe en modo revisión) tras haber escrito en la BD,
con lo que la fuente 'gva' terminaba en ERROR en todos los ciclos."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import gva_bolsas_complementarias as mod


class PersistirBolsasAplicarTest(unittest.TestCase):
    def test_aplicar_devuelve_resultado_y_errores_de_descubrimiento(self):
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        cliente = MagicMock()
        cliente.__enter__.return_value = cliente
        with ExitStack() as pila:
            pila.enter_context(patch.object(mod, "nuevo_cliente", return_value=cliente))
            pila.enter_context(patch.object(mod, "_descubrir_por_codigo", side_effect=RuntimeError("caído")))
            pila.enter_context(patch.object(mod, "_cargar_coincidencias", return_value={}))
            pila.enter_context(patch.object(mod, "get_connection", return_value=conexion))
            pila.enter_context(patch.object(mod, "_resolver_identidad_gva_directa", return_value=(1, 1)))
            resultado = mod.persistir_bolsas_gva_complementarias(aplicar=True)
        self.assertEqual(resultado["modo"], "APLICADO")
        self.assertTrue(resultado["errores"])
        self.assertEqual(resultado["errores"][0]["fase"], "descubrimiento")


if __name__ == "__main__":
    unittest.main()
