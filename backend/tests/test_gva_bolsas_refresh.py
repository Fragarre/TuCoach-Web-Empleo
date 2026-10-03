from __future__ import annotations

import unittest

from app.gva_bolsas_complementarias import (
    _bolsa_directa_actualizable,
    _bolsa_directa_cambia,
)


class BolsaGvaRefreshTests(unittest.TestCase):
    def _existente(self) -> dict:
        return {
            "id": 10,
            "identificador_estable": "GVA:123",
            "denominacion": "Bolsa administrativa",
            "cuerpo_escala": "C1-01",
            "grupo": "C1",
            "turno": None,
            "anio_convocatoria": 2026,
            "fecha_apertura": "2026-09-01",
            "fecha_cierre": "2026-09-10",
            "ultima_publicacion_at": "2026-09-01",
            "datos_json": {
                "fuente_descubrimiento": "sede.gva.es",
                "categoria_gva": "BOLSA",
                "fase_gva": "Bolsa en funcionamiento",
                "etapa_actual_gva": "Constitución de bolsa",
                "contenido_hash": "abc",
            },
        }

    def _proceso(self) -> dict:
        existente = self._existente()
        return {
            "denominacion": existente["denominacion"],
            "cuerpo_escala": existente["cuerpo_escala"],
            "grupo": existente["grupo"],
            "turno": existente["turno"],
            "anio_convocatoria": existente["anio_convocatoria"],
            "fecha_apertura": existente["fecha_apertura"],
            "fecha_cierre": existente["fecha_cierre"],
            "ultima_publicacion_at": existente["ultima_publicacion_at"],
            "datos_json": dict(existente["datos_json"]),
        }

    def test_bolsa_directa_sin_cambios_es_idempotente(self):
        existente = self._existente()
        proceso = self._proceso()
        self.assertTrue(_bolsa_directa_actualizable(existente, 123))
        self.assertFalse(_bolsa_directa_cambia(existente, proceso))

    def test_cambio_de_etapa_exige_actualizacion(self):
        existente = self._existente()
        proceso = self._proceso()
        proceso["datos_json"]["etapa_actual_gva"] = "Nueva etapa oficial"
        self.assertTrue(_bolsa_directa_cambia(existente, proceso))

    def test_fecha_nueva_exige_actualizacion(self):
        existente = self._existente()
        proceso = self._proceso()
        proceso["fecha_cierre"] = "2026-09-15"
        self.assertTrue(_bolsa_directa_cambia(existente, proceso))

    def test_fecha_ausente_en_fuente_no_borra_fecha_existente(self):
        existente = self._existente()
        proceso = self._proceso()
        proceso["fecha_cierre"] = None
        self.assertFalse(_bolsa_directa_cambia(existente, proceso))

    def test_bolsa_ajena_al_modulo_no_es_actualizable(self):
        existente = self._existente()
        existente["datos_json"]["fuente_descubrimiento"] = "otra_fuente"
        self.assertFalse(_bolsa_directa_actualizable(existente, 123))

    def test_identificador_distinto_no_es_actualizable(self):
        existente = self._existente()
        existente["identificador_estable"] = "GVA:999"
        self.assertFalse(_bolsa_directa_actualizable(existente, 123))


if __name__ == "__main__":
    unittest.main()
