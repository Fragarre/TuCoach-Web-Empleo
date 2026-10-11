"""Correo resumen, reintentos y filtro de pago de las notificaciones de Empleo."""
from contextlib import contextmanager
from pathlib import Path
import importlib.util
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
U1 = UUID("00000000-0000-0000-0000-000000000001")
U2 = UUID("00000000-0000-0000-0000-000000000002")


def _cargar():
    """Carga los módulos reales con un paquete `app` y dependencias simuladas."""
    paquete = types.ModuleType("app")
    paquete.__path__ = [str(BACKEND / "app")]
    acceso = types.ModuleType("access")
    acceso.PRIVATE_EMPLOYMENT_USER_IDS = {U1}
    acceso.es_categoria_empleo_privada = lambda *a, **k: False
    acceso.puede_acceder_categoria_privada = lambda *a, **k: True
    base = types.ModuleType("app.database")
    base.get_connection = MagicMock()
    procesos = types.ModuleType("app.procesos")
    procesos._condiciones_catalogo = lambda: ("TRUE", [])
    psycopg = types.ModuleType("psycopg")
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = None
    psycopg.rows = rows
    httpx = types.ModuleType("httpx")
    previos = {n: sys.modules.get(n) for n in
               ("app", "access", "app.database", "app.procesos", "psycopg", "psycopg.rows", "httpx",
                "app.email_sender", "app.notificaciones_generales", "app.seguimiento")}
    sys.modules.update({"app": paquete, "access": acceso, "app.database": base, "app.procesos": procesos,
                        "psycopg": psycopg, "psycopg.rows": rows, "httpx": httpx})
    for n in ("app.email_sender", "app.notificaciones_generales", "app.seguimiento"):
        sys.modules.pop(n, None)
    try:
        mods = {}
        for n in ("email_sender", "notificaciones_generales", "seguimiento"):
            spec = importlib.util.spec_from_file_location(f"app.{n}", BACKEND / "app" / f"{n}.py")
            m = importlib.util.module_from_spec(spec)
            sys.modules[f"app.{n}"] = m
            spec.loader.exec_module(m)
            mods[n] = m
        return mods
    finally:
        for n, v in previos.items():
            if v is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = v
        for n in ("app.email_sender", "app.notificaciones_generales", "app.seguimiento"):
            sys.modules.pop(n, None)


MODS = _cargar()
GEN = MODS["notificaciones_generales"]
SEG = MODS["seguimiento"]


class CursorFalso:
    def __init__(self, filas, registro):
        self.filas, self.registro = filas, registro

    def execute(self, sql, params=None):
        self.registro.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.filas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def conexion_falsa(filas, registro):
    @contextmanager
    def _conexion():
        c = MagicMock()
        c.cursor = lambda **k: CursorFalso(filas, registro)
        yield c
    return _conexion


def fila(i, user=U1, email="a@x.es", **extra):
    base = {"id": i, "user_id": user, "email": email, "proceso_id": 100 + i,
            "denominacion": f"Plaza {i}", "cuerpo_escala": None, "plazas": 2,
            "sistema_selectivo": "Oposición", "fecha_convocatoria": None, "fecha_apertura": None,
            "fecha_cierre": None, "tipo_proceso": "Oposición", "datos_json": {},
            "organismo": "Ayuntamiento X", "provincia": "Valencia"}
    base.update(extra)
    return base


class ResumenGeneral(unittest.TestCase):
    def test_una_oportunidad_mantiene_asunto_individual(self):
        asunto, texto, html = GEN.componer_resumen([fila(1)], "https://w.es")
        self.assertEqual(asunto, "Nueva oportunidad de empleo: Plaza 1")
        self.assertIn("https://w.es/empleo/proceso/101", texto)
        self.assertIn("Plaza 1", html)

    def test_varias_oportunidades_en_un_solo_correo(self):
        asunto, texto, _ = GEN.componer_resumen([fila(1), fila(2), fila(3)], "https://w.es")
        self.assertEqual(asunto, "3 nuevas oportunidades de empleo")
        for i in (1, 2, 3):
            self.assertIn(f"Plaza {i}", texto)

    def test_resumen_limita_y_enlaza_el_resto(self):
        filas = [fila(i) for i in range(1, GEN.MAX_ITEMS_RESUMEN + 6)]
        _, texto, _ = GEN.componer_resumen(filas, "https://w.es")
        self.assertIn("... y 5 más", texto)
        self.assertNotIn(f"Plaza {GEN.MAX_ITEMS_RESUMEN + 1}\n", texto)

    def test_adc_incluye_acceso_oficial(self):
        adc = fila(1, tipo_proceso="Anuncio difícil cobertura (ADC)",
                   datos_json={"numero_adc": "204/26", "url_detalle": "https://gva.es/adc"})
        _, texto, html = GEN.componer_resumen([adc], "https://w.es")
        self.assertIn("ADC: 204/26", texto)
        self.assertIn("https://gva.es/adc", html)


class EnvioGeneral(unittest.TestCase):
    def _enviar(self, filas, enviar):
        registro = []
        with patch.object(GEN, "get_connection", conexion_falsa(filas, registro)), \
                patch.object(GEN, "enviar_email", enviar):
            return GEN.enviar_envios_pendientes(), registro

    def test_un_correo_por_usuario(self):
        enviar = MagicMock(return_value=MagicMock(message_id="m1"))
        r, registro = self._enviar([fila(1), fila(2), fila(3, user=U2, email="b@x.es")], enviar)
        self.assertEqual(enviar.call_count, 2)
        self.assertEqual((r["correos"], r["enviadas"], r["errores"]), (2, 3, 0))
        marcados = [p[1][1] for p in registro if "ENVIADA" in p[0]]
        self.assertEqual(sorted(map(sorted, marcados)), [[1, 2], [3]])

    def test_error_deja_pendiente_para_reintentar(self):
        enviar = MagicMock(side_effect=RuntimeError("Resend caído"))
        r, registro = self._enviar([fila(1)], enviar)
        self.assertEqual(r["errores"], 1)
        updates = [p[0] for p in registro if p[0].startswith("UPDATE")]
        self.assertTrue(updates)
        self.assertTrue(all("'ERROR'" not in u and "'ENVIADA'" not in u for u in updates))

    def test_la_consulta_solo_reintenta_ventana_reciente(self):
        _, registro = self._enviar([], MagicMock())
        self.assertIn("INTERVAL '3 days'", registro[0][0])


class AvisosSeguimiento(unittest.TestCase):
    def _ejecutar(self, *, por_usuario, pendientes, de_pago, enviar):
        marcados = []
        with patch.object(SEG, "novedades_por_usuario", return_value=por_usuario), \
                patch.object(SEG, "registrar_avisos_seguimiento", return_value=sum(map(len, por_usuario.values()))), \
                patch.object(SEG, "_usuarios_con_avisos_pendientes", return_value=pendientes), \
                patch.object(SEG, "usuarios_de_pago", return_value=de_pago), \
                patch.object(SEG, "emails_usuarios", return_value={U1: "a@x.es", U2: "b@x.es"}), \
                patch.object(SEG, "_marcar_avisos", side_effect=lambda ids, estado, error=None: marcados.append((estado, ids))), \
                patch.object(SEG, "enviar_email", enviar), \
                patch.object(SEG, "get_connection", conexion_falsa([], [])):
            return SEG.enviar_avisos_novedades(set()), marcados

    def test_envia_solo_a_seguidores_con_novedad_y_de_pago(self):
        enviar = MagicMock()
        r, marcados = self._ejecutar(por_usuario={U1: {("CAMBIO", 1)}, U2: {("CAMBIO", 1)}},
                                     pendientes={U1: [10], U2: [11]}, de_pago={U1}, enviar=enviar)
        self.assertEqual(enviar.call_count, 1)
        self.assertEqual(enviar.call_args.kwargs["destinatario"], "a@x.es")
        self.assertIn(("DESCARTADA", [11]), marcados)
        self.assertIn(("ENVIADA", [10]), marcados)
        self.assertEqual((r["enviados"], r["sin_pago"]), (1, 1))

    def test_fallo_de_envio_no_marca_enviada_y_se_reintenta(self):
        enviar = MagicMock(side_effect=RuntimeError("fallo"))
        r, marcados = self._ejecutar(por_usuario={U1: {("PUBLICACION", 5)}},
                                     pendientes={U1: [10]}, de_pago={U1}, enviar=enviar)
        self.assertEqual((r["enviados"], r["errores"]), (0, 1))
        self.assertEqual(marcados, [])

    def test_reintenta_pendientes_aunque_no_haya_novedades_nuevas(self):
        enviar = MagicMock()
        r, marcados = self._ejecutar(por_usuario={}, pendientes={U1: [10]}, de_pago={U1}, enviar=enviar)
        self.assertEqual(r["enviados"], 1)
        self.assertEqual(marcados, [("ENVIADA", [10])])

    def test_sin_pendientes_no_hace_nada(self):
        enviar = MagicMock()
        r, _ = self._ejecutar(por_usuario={}, pendientes={}, de_pago=set(), enviar=enviar)
        enviar.assert_not_called()
        self.assertEqual(r["enviados"], 0)


if __name__ == "__main__":
    unittest.main()
