"""Conector de oposiciones GVA desde la Sede (cercador-ocupacio-publica).

Usa HTML real guardado de la Sede; no hay red ni base de datos.
"""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import gva_oposiciones_sede as mod

FIX = Path(__file__).parent / "fixtures" / "gva_sede"


def leer(nombre: str) -> str:
    return (FIX / nombre).read_text(encoding="utf-8")


class ListadoTest(unittest.TestCase):
    def test_lee_total_y_tarjetas(self):
        r = mod.parsear_listado(leer("listado_oposiciones.html"))
        self.assertEqual(r["total"], 10)
        self.assertEqual(len(r["tarjetas"]), 10)
        t = r["tarjetas"][0]
        self.assertEqual(t["id_emp"], 78071)
        self.assertEqual(t["proceso_selectivo"], "Oposición")
        self.assertEqual(t["plazas"], 105)
        self.assertEqual(t["plazo_etapa_desde"], "2026-05-08")
        self.assertEqual(t["plazo_etapa_hasta"], "2026-05-14")

    def test_pagina_irreconocible_falla_en_alto(self):
        with self.assertRaises(mod.EstructuraInesperada):
            mod.parsear_listado("<html><body><p>Texto de búsqueda incorrecto.</p></body></html>")

    def test_total_sin_tarjetas_falla_en_alto(self):
        with self.assertRaises(mod.EstructuraInesperada):
            mod.parsear_listado('<div class="total-results">5 resultados</div><div class="search-results"></div>')

    def test_cero_resultados_legitimo_no_falla(self):
        r = mod.parsear_listado('<div class="total-results">0 resultados</div><div class="search-results"></div>')
        self.assertEqual(r["tarjetas"], [])


class FichaTest(unittest.TestCase):
    def test_ficha_cerrada_196_21(self):
        f = mod.parsear_ficha(leer("ficha_196_21_cerrada.html"), 87283)
        self.assertEqual(f["fase"], "Adjudicación y nombramiento")
        self.assertEqual(f["convocatoria"], "Promoción interna")
        self.assertEqual(len(f["etapas"]), 18)
        cierre = mod.evaluar_cierre(f)
        self.assertEqual(cierre["estado"], "FINALIZADO")

    def test_ficha_activa_no_se_cierra(self):
        f = mod.parsear_ficha(leer("ficha_activa_convocatoria.html"), 99999)
        self.assertEqual(f["fase"], "Convocatoria")
        self.assertEqual(f["etapa_actual"], "Bases y apertura de plazo")
        self.assertEqual((f["plazo_desde"], f["plazo_hasta"]), ("2026-04-21", "2026-05-06"))
        self.assertEqual((f["dogv_numero"], f["dogv_fecha"]), ("10345", "2026-04-20"))
        self.assertIsNone(mod.evaluar_cierre(f))
        p = mod.construir_proceso(None, f)
        self.assertEqual(p["fecha_apertura"], "2026-04-21")
        self.assertEqual(p["fecha_cierre"], "2026-05-06")
        self.assertTrue(p["ambito"]["administrativo"])
        self.assertEqual(p["identificador_estable"], "GVA:99999")

    def test_ficha_irreconocible_falla_en_alto(self):
        with self.assertRaises(mod.EstructuraInesperada):
            mod.parsear_ficha("<html><body>Error</body></html>", 1)


class CierreTest(unittest.TestCase):
    def ficha(self, etapa, fase="Oposición", etapas=()):
        return {"etapa_actual": etapa, "fase": fase, "ultima_publicacion": "2026-06-01",
                "etapas": [{"nombre": n, "fecha_publicacion": "2026-06-01"} for n in etapas]}

    def test_resultados_parciales_no_cierran(self):
        for titulo in (
            "Lista provisional de personas aspirantes aprobadas del primer ejercicio",
            "Lista de aprobados (fase de oposición). Plazo presentación documentos para valoración de méritos",
            "Nombramiento del tribunal",
            "Lista provisional admitidos/excluidos. Plazo de alegaciones",
            "Resultado del segundo ejercicio",
        ):
            self.assertIsNone(mod.evaluar_cierre(self.ficha(titulo, etapas=[titulo])), titulo)

    def test_relacion_definitiva_cierra(self):
        c = mod.evaluar_cierre(self.ficha("Lista definitiva de aprobados. Plazo de presentación de documentos"))
        self.assertEqual(c["estado"], "FINALIZADO")

    def test_anulacion_del_proceso_cierra(self):
        c = mod.evaluar_cierre(self.ficha("Anulación de la convocatoria"))
        self.assertEqual(c["estado"], "ANULADO")

    def test_etapa_nombramiento_cierra(self):
        c = mod.evaluar_cierre(self.ficha("Plazo de recurso", etapas=["Nombramiento"]))
        self.assertEqual(c["regla"], "ETAPA_NOMBRAMIENTO")

    def test_fase_nombramiento_cierra(self):
        c = mod.evaluar_cierre(self.ficha("Toma de posesión", fase="Nombramiento"))
        self.assertEqual(c["estado"], "FINALIZADO")


class AmbitoTest(unittest.TestCase):
    def test_titulos_del_listado(self):
        r = mod.parsear_listado(leer("listado_oposiciones.html"))
        esperado = {
            78071: True,   # Administrativo/a de la función administrativa (Sanidad)
            86180: True,   # C1-01 Administrativo
            87244: False,  # C1-03 especialidad
            87291: True,   # C2-01-01 Auxiliar administrativo
            74974: False,  # especialistas técnicos
            107740: False,  # Maestros
            111540: False,
            77670: False,
            105609: False,
            78347: False,
        }
        for t in r["tarjetas"]:
            self.assertEqual(mod.clasificar_ambito(t["titulo"])["administrativo"], esperado[t["id_emp"]], t["titulo"])

    def test_promocion_interna_se_excluye(self):
        a = mod.clasificar_ambito("Convocatoria 196/21. Cuerpo C1-01 Administrativo, turno de promoción interna")
        self.assertFalse(a["administrativo"])

    def test_fondos_europeos_del_cuerpo_superior_si_entra(self):
        a = mod.clasificar_ambito("Convocatoria 1/26. Proceso selectivo 3. Pruebas selectivas de acceso al cuerpo Superior de Administración. APT-A1-01-02, de fondos europeos. TURNO LIBRE")
        self.assertTrue(a["administrativo"])

    def test_otras_apt_siguen_excluidas(self):
        a = mod.clasificar_ambito("Convocatoria 60/26. Pruebas selectivas de acceso al cuerpo Especialista técnico. APT-C1-03-01-04, de comunicación y emergencias. TURNO LIBRE")
        self.assertFalse(a["administrativo"])

    def test_especialidades_excluidas(self):
        self.assertFalse(mod.clasificar_ambito("Cuerpo C1-01 Administrativo, especialidad APT-01")["administrativo"])


class PersistenciaTest(unittest.TestCase):
    def _analisis(self, **extra):
        f = mod.parsear_ficha(leer("ficha_activa_convocatoria.html"), 99999)
        activa = {"proceso": mod.construir_proceso(None, f), "cierre": None}
        f2 = mod.parsear_ficha(leer("ficha_196_21_cerrada.html"), 87283)
        p2 = mod.construir_proceso(None, f2)
        p2["ambito"] = {"administrativo": True, "motivo": None, "codigos": ["C1-01"]}
        cerrada = {"proceso": p2, "cierre": mod.evaluar_cierre(f2)}
        return {"desde": "2026-01-01", "hasta": "2026-04-30", "en_listado": 2, "excluidas": [],
                "analizadas": [activa, cerrada], "sin_revisar_por_limite": [], "errores": [], **extra}

    def _ejecutar(self, existentes, aplicar=True):
        conexion = MagicMock()
        conexion.__enter__.return_value = conexion
        with ExitStack() as pila:
            pila.enter_context(patch.object(mod, "get_connection", return_value=conexion))
            pila.enter_context(patch.object(mod, "_resolver_identidad_gva_directa", return_value=(1, 1)))
            pila.enter_context(patch.object(mod, "_cargar_en_curso_propias", return_value=[]))
            pila.enter_context(patch.object(mod, "analizar_oposiciones_gva_sede", return_value=self._analisis()))
            pila.enter_context(patch.object(mod, "_cargar_existentes", return_value=existentes))
            ins = pila.enter_context(patch.object(mod, "_insertar", return_value=555))
            pub = pila.enter_context(patch.object(mod, "_registrar_publicaciones"))
            cer = pila.enter_context(patch.object(mod, "_cerrar", return_value=True))
            ref = pila.enter_context(patch.object(mod, "_refrescar"))
            r = mod.persistir_oposiciones_gva_sede(dias=30, aplicar=aplicar)
        return r, ins, cer, ref

    def test_inserta_activa_y_no_inserta_resuelta(self):
        r, ins, cer, _ = self._ejecutar({})
        self.assertEqual([i["id_emp"] for i in r["insertados"]], [99999])
        self.assertEqual(ins.call_count, 1)
        cer.assert_not_called()
        self.assertEqual(r["resueltas_no_insertadas"], [87283])

    def test_cierra_la_en_curso_que_ya_esta_resuelta(self):
        existentes = {87283: {"id": 7, "estado": "EN_CURSO", "identificador_estable": "GVA:87283"}}
        r, ins, cer, _ = self._ejecutar(existentes)
        self.assertEqual(cer.call_count, 1)
        self.assertEqual(r["cerrados"][0]["id_emp"], 87283)

    def test_no_reabre_ni_toca_las_ya_cerradas(self):
        existentes = {99999: {"id": 8, "estado": "FINALIZADO", "identificador_estable": "GVA:99999"}}
        r, ins, cer, ref = self._ejecutar(existentes)
        ins.assert_not_called()
        ref.assert_not_called()

    def test_revision_no_escribe(self):
        r, ins, cer, _ = self._ejecutar({}, aplicar=False)
        self.assertEqual(r["modo"], "SOLO_REVISION")
        ins.assert_not_called()
        self.assertEqual([a["id_emp"] for a in r["activas"]], [99999])


if __name__ == "__main__":
    unittest.main()
