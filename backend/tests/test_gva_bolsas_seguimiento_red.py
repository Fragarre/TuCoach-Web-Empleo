from __future__ import annotations

from contextlib import contextmanager
import unittest
from unittest.mock import MagicMock, patch

import httpx

from app import gva_bolsas_seguimiento as seg


@contextmanager
def _cliente_falso():
    yield MagicMock()


class SeguimientoBolsasRedTests(unittest.TestCase):
    def _ejecutar(self, error):
        procesos = [
            {"id": 1, "referencia_estatal": 11},
            {"id": 2, "referencia_estatal": 22},
        ]
        respuestas = [error, "<html></html>"]

        def obtener(_client, _referencia):
            valor = respuestas.pop(0)
            if isinstance(valor, Exception):
                raise valor
            return valor

        with patch.object(seg, "_cargar_bolsas_activas", return_value=procesos), \
                patch.object(seg, "nuevo_cliente", _cliente_falso), \
                patch.object(seg, "_obtener_html", side_effect=obtener), \
                patch.object(seg, "extraer_seguimientos_validos", return_value={}), \
                patch.object(seg, "_planificar", return_value={"acciones": [], "resumen": {}}):
            return seg.actualizar_bolsas_gva_simplificadas(aplicar=False)

    def test_servidor_desconectado_no_aborta_el_seguimiento(self):
        resultado = self._ejecutar(httpx.RemoteProtocolError("Server disconnected without sending a response."))
        self.assertEqual(len(resultado["errores"]), 1)
        self.assertEqual(resultado["errores"][0]["proceso_id"], 1)
        self.assertIn("RemoteProtocolError", resultado["errores"][0]["error"])

    def test_tiempo_de_espera_sigue_tolerado(self):
        resultado = self._ejecutar(httpx.ReadTimeout("lento"))
        self.assertEqual(len(resultado["errores"]), 1)
