from app.gva_dogv_seguimiento import _guardar_estado


class CursorFalso:
    def __init__(self):
        self.rowcount = 0
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        self.rowcount = 1


def test_guardar_checkpoint_dogv_no_marca_proceso_como_actualizado():
    cursor = CursorFalso()

    _guardar_estado(
        cursor,
        proceso_id=26,
        referencia_estatal=110135,
        identidad=["CODIGO:C2-01"],
        vistos=["2026_12345"],
        revisado_hasta="2026-10-04",
    )

    sql_normalizado = " ".join(cursor.sql.lower().split())
    assert "datos_json" in sql_normalizado
    assert "updated_at" not in sql_normalizado
    assert cursor.params[1] == 26
