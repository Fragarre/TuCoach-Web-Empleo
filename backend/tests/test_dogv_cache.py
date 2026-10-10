"""Cliente DOGV: caché de un ciclo, reintentos y proxy."""
from datetime import date, timedelta
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import httpx
from app import gva_dogv_diagnostico as dg
from app import gva_estatal_source as src

AYER = (date.today() - timedelta(days=3)).isoformat()


class Resp:
    def __init__(self, datos):
        self._datos = datos

    def raise_for_status(self):
        return None

    def json(self):
        return self._datos


class ClienteFalso:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def get(self, url, params=None):
        self.llamadas.append((url, params))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class CacheTest(unittest.TestCase):
    def setUp(self):
        dg.limpiar_cache_dogv()

    def test_dia_cerrado_se_pide_una_sola_vez(self):
        c = ClienteFalso(Resp({"disposiciones": []}))
        dg._leer_diario(c, AYER)
        dg._leer_diario(c, AYER)
        self.assertEqual(len(c.llamadas), 1)

    def test_hoy_no_se_cachea(self):
        hoy = date.today().isoformat()
        c = ClienteFalso(Resp({"disposiciones": []}), Resp({"disposiciones": [1]}))
        dg._leer_diario(c, hoy)
        self.assertEqual(dg._leer_diario(c, hoy), {"disposiciones": [1]})
        self.assertEqual(len(c.llamadas), 2)

    def test_detalle_se_cachea_por_id(self):
        c = ClienteFalso(Resp({"titulo": "T", "texto": "CONVOCATORIA 3/2026", "codigoInsercion": "2026/1"}))
        a = dg._detalle_disposicion(c, {"id": 9})
        b = dg._detalle_disposicion(c, {"id": 9})
        self.assertEqual(len(c.llamadas), 1)
        self.assertIs(a, b)
        self.assertIn("CONVOCATORIA:3/2026", a["tokens_dogv"])

    def test_limpiar_cache(self):
        c = ClienteFalso(Resp({"disposiciones": []}), Resp({"disposiciones": []}))
        dg._leer_diario(c, AYER)
        dg.limpiar_cache_dogv()
        dg._leer_diario(c, AYER)
        self.assertEqual(len(c.llamadas), 2)


class ReintentosTest(unittest.TestCase):
    def setUp(self):
        dg.limpiar_cache_dogv()

    def test_reintenta_ante_timeout_y_se_recupera(self):
        c = ClienteFalso(httpx.TimeoutException("t"), Resp({"ok": 1}))
        with patch.object(dg.time, "sleep") as dormir:
            self.assertEqual(dg._get_json(c, "u", {}), {"ok": 1})
        dormir.assert_called_once()

    def test_agota_reintentos_y_propaga(self):
        c = ClienteFalso(*[httpx.TimeoutException("t")] * dg.REINTENTOS_DOGV)
        with patch.object(dg.time, "sleep"), self.assertRaises(httpx.TimeoutException):
            dg._get_json(c, "u", {})
        self.assertEqual(len(c.llamadas), dg.REINTENTOS_DOGV)


class ProxyTest(unittest.TestCase):
    ENV = {"GVA_PROXY_URL": "http://gate.decodo.com:7000", "GVA_PROXY_USER": "u", "GVA_PROXY_PASSWORD": "p@ss"}

    def _proxies(self, fabrica, extra=None, *, env=None):
        """Valores de ``proxy`` con que se han creado los httpx.Client. El cliente
        GVA (gva_http) crea uno directo y, si hay proxy, otro por el proxy."""
        entorno = env if env is not None else {**self.ENV, **(extra or {})}
        with patch.dict(os.environ, entorno, clear=env is not None), patch.object(src.httpx, "Client") as cliente:
            fabrica()
        return [c.kwargs.get("proxy") for c in cliente.call_args_list]

    def test_dogv_usa_proxy_si_esta_configurado(self):
        self.assertIn("http://u:p%40ss@gate.decodo.com:7000", self._proxies(src.nuevo_cliente_dogv))

    def test_dogv_sin_proxy_si_se_desactiva(self):
        self.assertEqual(self._proxies(src.nuevo_cliente_dogv, {"DOGV_USAR_PROXY": "false"}), [None])

    def test_sin_configuracion_no_hay_proxy(self):
        for fabrica in (src.nuevo_cliente, src.nuevo_cliente_dogv):
            self.assertTrue(all(p is None for p in self._proxies(fabrica, env={})))

    def test_configuracion_incompleta_falla_en_ambos(self):
        with patch.dict(os.environ, {"GVA_PROXY_URL": "http://x"}, clear=True):
            for fabrica in (src.nuevo_cliente, src.nuevo_cliente_dogv):
                with self.assertRaises(RuntimeError):
                    fabrica()


if __name__ == "__main__":
    unittest.main()
