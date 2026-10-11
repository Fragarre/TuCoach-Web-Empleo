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


HTML_TRUNCADO = (
    '<div class="alert">La búsqueda devuelve demasiados resultados, solo se muestran los 100 primeros.</div>'
    '<div class="total-results">100 resultados</div><div class="search-results">'
    '<div class="opposition card"><a href="https://sede.gva.es/detall-ocupacio-publica?id_emp=1">'
    '<div class="title">X</div></a></div></div>'
)


class TruncadoTest(unittest.TestCase):
    def test_detecta_listado_truncado(self):
        self.assertTrue(mod.parsear_listado(HTML_TRUNCADO)["truncado"])

    def test_listado_normal_no_esta_truncado(self):
        self.assertFalse(mod.parsear_listado(leer("listado_oposiciones.html"))["truncado"])

    def test_parte_el_rango_hasta_que_cabe_y_no_pierde_ninguno(self):
        from datetime import date, timedelta
        base = date(2026, 1, 1)
        # 150 procesos, uno por día: un rango de más de 60 días "se trunca".
        datos = {i: base + timedelta(days=i) for i in range(150)}

        def falso(client, d, h):
            dentro = {i: {"id_emp": i} for i, f in datos.items() if d <= f <= h}
            return dentro, (h - d).days >= 60

        with patch.object(mod, "_recorrer_paginas", side_effect=falso):
            r = mod.descubrir_oposiciones(None, desde=base, hasta=base + timedelta(days=149))
        self.assertEqual(len(r), 150)

    def test_un_solo_dia_truncado_falla_en_alto(self):
        from datetime import date
        with patch.object(mod, "_recorrer_paginas", return_value=({}, True)):
            with self.assertRaises(mod.EstructuraInesperada):
                mod.descubrir_oposiciones(None, desde=date(2026, 3, 1), hasta=date(2026, 3, 1))


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


HTML_ETAPA_BASES = """<html><body><div class="derecha">
<a href="javascript:void(0)">Volver al listado de etapas</a><h3>Bases y apertura de plazo</h3>
<div class="bloque fase"><div class="titulos">Fase</div><div class="textos"><p>Convocatoria</p></div></div>
<div class="bloque plazo"><div class="titulos">Plazo</div><div class="textos"><div class="cerrado"><span>Desde 30-03-2026 a 14-04-2026</span></div>
<p>10 días hábiles, contados a partir del día siguiente al de la publicación de esta convocatoria en el Diario Oficial.</p></div></div>
<div class="bloque publicacion"><div class="titulos">Publicación</div><div class="textos">
<a href="https://dogv.gva.es/datos/2026/03/27/pdf/2026_1234_es.pdf">DOGV núm. 10331 de 27-03-2026</a></div></div>
</div></body></html>"""


class InscripcionTest(unittest.TestCase):
    def test_parsea_el_detalle_de_la_etapa_de_bases(self):
        e = mod.parsear_etapa(HTML_ETAPA_BASES)
        self.assertEqual((e["plazo_desde"], e["plazo_hasta"]), ("2026-03-30", "2026-04-14"))
        self.assertEqual((e["dogv_numero"], e["dogv_fecha"]), ("10331", "2026-03-27"))
        self.assertTrue(e["dogv_url"].startswith("https://dogv.gva.es"))

    def test_las_etapas_traen_su_id_y_se_localiza_la_de_bases(self):
        f = mod.parsear_ficha(leer("ficha_196_21_cerrada.html"), 87283)
        ins = mod.etapas_inscripcion(f)
        self.assertEqual([e["nombre"] for e in ins], ["Bases y apertura de plazo"])
        self.assertEqual(ins[0]["id"], "1")

    def test_ampliacion_extiende_el_cierre_y_el_dogv_es_el_de_las_bases(self):
        bases = {"plazo_desde": "2026-03-30", "plazo_hasta": "2026-04-14", "dogv_numero": "10331", "dogv_fecha": "2026-03-27", "dogv_url": None}
        amplia = {"plazo_desde": "2026-04-15", "plazo_hasta": "2026-04-24", "dogv_numero": "10340", "dogv_fecha": "2026-04-10", "dogv_url": None}
        c = mod.combinar_inscripcion([bases, amplia])
        self.assertEqual((c["plazo_desde"], c["plazo_hasta"]), ("2026-03-30", "2026-04-24"))
        self.assertEqual(c["dogv_numero"], "10331")

    def test_sin_datos_no_inventa_nada(self):
        self.assertIsNone(mod.combinar_inscripcion([{"plazo_desde": None, "dogv_numero": None}]))

    def test_proceso_en_fase_de_tribunal_recibe_las_fechas_de_inscripcion(self):
        f = mod.parsear_ficha(leer("ficha_activa_convocatoria.html"), 99999)
        f["etapa_actual"] = "Modificación composición del tribunal"
        f["plazo_desde"], f["plazo_hasta"] = None, None
        f["dogv_numero"], f["dogv_fecha"] = "10452", "2026-09-16"
        f["inscripcion"] = mod.combinar_inscripcion([mod.parsear_etapa(HTML_ETAPA_BASES)])
        p = mod.construir_proceso(None, f)
        self.assertEqual((p["fecha_apertura"], p["fecha_cierre"]), ("2026-03-30", "2026-04-14"))
        # el DOGV de la convocatoria es el de las bases, no el de la última etapa
        self.assertEqual(p["datos_json"]["dogv_numero"], "10331")

    def test_sin_inscripcion_leida_no_hay_fechas_ni_dogv_falso(self):
        f = mod.parsear_ficha(leer("ficha_activa_convocatoria.html"), 99999)
        f["etapa_actual"] = "Modificación composición del tribunal"
        f["dogv_numero"], f["dogv_fecha"] = "10452", "2026-09-16"
        p = mod.construir_proceso(None, f)
        self.assertIsNone(p["fecha_cierre"])
        self.assertIsNone(p["datos_json"]["dogv_numero"])


class AnalisisConEtapasTest(unittest.TestCase):
    def _ejecutar(self, con_fechas):
        ficha = mod.parsear_ficha(leer("ficha_activa_convocatoria.html"), 99999)
        ficha["etapa_actual"] = "Modificación composición del tribunal"
        ficha["fase"] = "Oposición"
        ficha["plazo_desde"] = ficha["plazo_hasta"] = None
        ficha["etapas"] = [
            {"id": "5", "nombre": "Modificación composición del tribunal", "fecha_publicacion": "2026-09-16", "actual": True},
            {"id": "1", "nombre": "Bases y apertura de plazo", "fecha_publicacion": "2026-03-27", "actual": False},
        ]
        tarjeta = {"id_emp": 99999, "titulo": ficha["titulo"], "proceso_selectivo": "Oposición"}
        urls = []

        def falsa_get(client, url, **kw):
            urls.append(url)
            r = MagicMock()
            r.text = HTML_ETAPA_BASES if "id_etapa" in url else "<html></html>"
            return r

        cliente = MagicMock()
        cliente.__enter__.return_value = cliente
        with ExitStack() as pila:
            pila.enter_context(patch.object(mod, "nuevo_cliente", return_value=cliente))
            pila.enter_context(patch.object(mod, "descubrir_oposiciones", return_value=[tarjeta]))
            pila.enter_context(patch.object(mod, "_get_gva_con_reintentos", side_effect=falsa_get))
            pila.enter_context(patch.object(mod, "parsear_ficha", return_value=ficha))
            pila.enter_context(patch.object(mod.time, "sleep"))
            r = mod.analizar_oposiciones_gva_sede(dias=30, hoy=__import__("datetime").date(2026, 10, 11), con_fechas=con_fechas)
        return r, urls

    def test_pide_la_etapa_de_bases_y_rellena_las_fechas(self):
        r, urls = self._ejecutar(set())
        self.assertTrue(any("id_etapa=1" in u for u in urls))
        p = r["analizadas"][0]["proceso"]
        self.assertEqual((p["fecha_apertura"], p["fecha_cierre"]), ("2026-03-30", "2026-04-14"))

    def test_si_ya_tiene_fechas_no_pide_etapas(self):
        r, urls = self._ejecutar({99999})
        self.assertFalse(any("id_etapa" in u for u in urls))


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

    def test_promocion_interna_71_26_se_excluye(self):
        a = mod.clasificar_ambito("Convocatoria 71/26. Pruebas selectivas de acceso al Cuerpo auxiliar, C2-01. PROMOCIÓN INTERNA")
        self.assertFalse(a["administrativo"])
        self.assertEqual(a["motivo"], "promocion_interna")

    def test_70_26_auxiliar_turno_libre_entra(self):
        a = mod.clasificar_ambito("Convocatoria 70/26. Pruebas selectivas de acceso al cuerpo auxiliar. C2-01. TURNO LIBRE")
        self.assertTrue(a["administrativo"])

    def test_turno_de_reserva_discapacidad_se_excluye(self):
        a = mod.clasificar_ambito("Convocatoria 80/26. Pruebas selectivas de acceso al cuerpo auxiliar. C2-01. Turno de reserva para personas con discapacidad")
        self.assertFalse(a["administrativo"])
        self.assertEqual(a["motivo"], "turno_no_libre")

    def test_el_listado_no_filtra_por_turno_en_la_sede(self):
        self.assertNotIn("turnos", mod._parametros_listado(1, None, None))

    def test_el_listado_filtra_por_tipo_de_convocatoria(self):
        self.assertEqual(mod._parametros_listado(1, None, None)["convocatorias"], "507,509")

    def test_tramo_truncado_no_lee_mas_paginas(self):
        llamadas = []

        def falsa_get(client, url, params=None, **kw):
            llamadas.append(params["pagina"])
            r = MagicMock()
            r.text = HTML_TRUNCADO
            return r

        with patch.object(mod, "_get_gva_con_reintentos", side_effect=falsa_get):
            _, truncado = mod._recorrer_paginas(None, None, None)
        self.assertTrue(truncado)
        self.assertEqual(llamadas, ["1"])

    def test_turno_libre_por_defecto(self):
        a = mod.clasificar_ambito("Convocatoria 58/26. Pruebas selectivas de acceso al Cuerpo administrativo, C1-01. TURNO LIBRE")
        self.assertEqual(a["turno"], "TURNO_LIBRE")



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
