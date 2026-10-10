"""Los cron deben fallar de verdad: el código de salida refleja el resultado."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


directo = _cargar("run_periodic_http", BACKEND / "scripts" / "run_periodic_http.py")
http = _cargar("cron_actualizacion", BACKEND / "app" / "cron_actualizacion.py")


def payload(*estados):
    nombres = {f"f{i}": e for i, e in enumerate(estados)}
    return {
        "estado_fuentes": {n: ({"estado": e, "error": "x"} if e == "ERROR" else {"estado": e}) for n, e in nombres.items()},
        "resumen_fuentes": {"total": len(nombres), "errores": sum(e == "ERROR" for e in nombres.values())},
        "ciclo": {"disponible": True},
    }


class CronDirectoTest(unittest.TestCase):
    def _main(self, retorno=None, lado=None, argv=("--aplicar",)):
        with patch.object(directo, "ejecutar_periodico", return_value=retorno, side_effect=lado), \
             patch.object(sys, "argv", ["x", *argv]), patch("builtins.print"):
            return directo.main()

    def test_correcto(self):
        self.assertEqual(self._main(payload("OK", "SIN_NOVEDADES")), 0)

    def test_fuente_en_error_falla(self):
        self.assertEqual(self._main(payload("OK", "ERROR")), 1)

    def test_excepcion_falla(self):
        self.assertEqual(self._main(lado=RuntimeError("bd caída")), 1)

    def test_parametros_invalidos(self):
        self.assertEqual(self._main(lado=ValueError("dias_solape")), 2)

    def test_ciclo_omitido_no_falla(self):
        self.assertEqual(self._main({"ciclo": {"omitido": True}}), 0)

    def test_pasa_los_argumentos(self):
        with patch.object(directo, "ejecutar_periodico", return_value=payload("OK")) as ejecutar, \
             patch.object(sys, "argv", ["x", "--aplicar", "--historico", "--dias", "730"]), patch("builtins.print"):
            directo.main()
        ejecutar.assert_called_once_with(aplicar=True, dias_solape=730, historico=True)


class CronHttpTest(unittest.TestCase):
    def test_sin_secreto(self):
        with patch.object(http, "SECRET", None), patch("builtins.print"):
            self.assertEqual(http.main(), 2)

    def test_sin_url_no_cae_en_staging(self):
        with patch.object(http, "SECRET", "s"), patch.object(http, "BASE_URL", ""), patch("builtins.print"):
            self.assertEqual(http.main(), 2)
        self.assertNotIn("staging", http.BASE_URL or "")


if __name__ == "__main__":
    unittest.main()
