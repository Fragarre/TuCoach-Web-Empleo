from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import bop_valencia as _MODULE
from app import bop_valencia_integrity as _INTEGRITY
from app import bop_valencia_rules as _RULES


TEXTO_MIXTO = (
    "05/26.- OPOSICIÓN LIBRE 21 PLAZAS TÉCNICO/A DE ADMINISTRACIÓN GENERAL "
    "(15 PLAZAS POR TURNO LIBRE Y 6 POR PROMOCIÓN INTERNA). CONVOCATORIA 05/26."
)


class BopValenciaTurnoMixtoTest(unittest.TestCase):
    def test_mixta_con_cuota_libre_es_oportunidad_publica(self):
        turno = _MODULE._turno(TEXTO_MIXTO)
        self.assertEqual(turno, "TURNO_LIBRE")
        self.assertTrue(_MODULE._es_oportunidad_administrativa("SI", turno))

    def test_mixta_muestra_solo_plazas_libres_y_conserva_desglose(self):
        self.assertEqual(_MODULE._plazas_turno_libre(TEXTO_MIXTO), 15)
        self.assertEqual(_MODULE._plazas_catalogo(TEXTO_MIXTO, "TURNO_LIBRE"), 15)
        self.assertEqual(
            _MODULE._desglose_plazas(TEXTO_MIXTO),
            {"plazas_turno_libre": 15, "plazas_promocion_interna": 6},
        )

    def test_extrae_convocatoria_con_dos_puntos(self):
        self.assertEqual(
            _MODULE._convocatoria("Designación del tribunal. Convocatoria: 06/25."),
            "06/25",
        )

    def test_seguimiento_no_es_convocatoria_base(self):
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Relación definitiva de admitidos. Convocatoria 53/21.",
                "Fecha de examen del proceso selectivo.",
            )
        )
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Designación de miembros del órgano técnico. Convocatoria: 06/25.",
                "",
            )
        )
        self.assertFalse(
            _MODULE._es_convocatoria_base(
                "Nombramiento como funcionario de carrera. Convocatoria 36/20.",
                "",
            )
        )

    def test_convocatoria_base_en_valenciano(self):
        self.assertTrue(
            _MODULE._es_convocatoria_base(
                "Aprovació de les bases de la convocatòria de l'oposició lliure. Convocatòria 04/25.",
                "",
            )
        )

    def test_seguimientos_en_valenciano_no_son_base(self):
        casos = (
            ("Aprovació de la relació provisional de persones admeses i excloses. Convocatòria 09/25.", ""),
            ("Designació de membres de l'òrgan tècnic de selecció. Convocatòria 06/25.", ""),
            ("Nomenament com a personal funcionari de carrera. Convocatòria 36/20.", ""),
            ("Constitució d'una borsa de treball derivada de l'oposició lliure. Convocatòria 49/23.", ""),
        )
        for titulo, texto in casos:
            with self.subTest(titulo=titulo):
                self.assertFalse(_MODULE._es_convocatoria_base(titulo, texto))

    def test_bases_no_se_invalidan_por_fases_descritas_en_el_pdf(self):
        casos = (
            (
                "Aprovació de les bases de la convocatòria de l'oposició lliure per a la selecció d'una plaça d'enginyeria industrial. Convocatòria 31/24.",
                "La relació provisional de persones admeses es publicarà posteriorment. Data d'examen i resultat segons les bases.",
            ),
            (
                "Aprovació de les bases de la convocatòria de l'oposició lliure per a la selecció de 66 places d'administratiu/va. Convocatòria 03/26.",
                "El tribunal publicarà la relació definitiva i les qualificacions del procés.",
            ),
        )
        for titulo, texto in casos:
            with self.subTest(titulo=titulo):
                self.assertTrue(_MODULE._es_convocatoria_base(titulo, texto))

    def test_integridad_no_invalida_bases_por_contenido_del_pdf(self):
        titulo = (
            "Anunci de la Diputació Provincial de València sobre l'aprovació de les bases "
            "de la convocatòria de l'oposició lliure per a la selecció de 66 places "
            "d'administratiu/va. Convocatòria 03/26."
        )
        texto = (
            "La relació provisional de persones admeses es publicarà posteriorment. "
            "La data d'examen i els resultats es publicaran segons les bases."
        )
        self.assertTrue(_INTEGRITY._es_convocatoria_base(titulo, texto))

    def test_integridad_mantiene_seguimiento_como_no_base(self):
        titulo = (
            "Anunci de la Diputació Provincial de València sobre l'aprovació de la relació "
            "definitiva i data d'examen de l'oposició lliure. Convocatòria 04/26."
        )
        texto = "El document cita les bases de la convocatòria i el procés selectiu."
        self.assertFalse(_INTEGRITY._es_convocatoria_base(titulo, texto))

    def test_provision_por_meritos_no_entra_como_empleo_objetivo(self):
        casos = (
            "Aprovació de les bases del concurs de mèrits per a la provisió del lloc de cap de secció. Convocatòria 13/26.",
            "Nomenament per concurs de mèrits del lloc de cap de taller d'impremta. Convocatòria 03/26.",
            "Anunci de la Diputació Provincial de València sobre el nomenament per concurs de mèrits del lloc de treball de cap de taller d'impremta, incardinat en el centre d'Impremta i Butlletí Oficial de la Província. Convocatòria 03/26.",
            "Nombramiento por concurso de méritos para la provisión del puesto de Secretaría. Convocatoria 90/25.",
        )
        for titulo in casos:
            with self.subTest(titulo=titulo):
                self.assertFalse(_RULES.incluido_empleo_bop(titulo))

    def test_terminal_de_oposicion_si_se_conserva(self):
        titulo = (
            "Nomenament com a personal funcionari de carrera de les persones aprovades "
            "en l'oposició lliure. Convocatòria 36/20."
        )
        self.assertTrue(_RULES.incluido_empleo_bop(titulo))

    def test_solo_promocion_interna_sigue_excluida(self):
        turno = _MODULE._turno("Convocatoria de 6 plazas por promoción interna.")
        self.assertEqual(turno, "PROMOCION_INTERNA")
        self.assertFalse(_MODULE._es_oportunidad_administrativa("SI", turno))
