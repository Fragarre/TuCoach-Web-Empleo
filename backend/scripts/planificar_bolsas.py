"""Revisión (solo lectura de la Sede y de la BD; NO escribe) de qué bolsas GVA se cargarían.

Uso:  $env:PYTHONPATH="."; python scripts/planificar_bolsas.py
Necesita DATABASE_URL (base de pruebas). Muestra las bolsas NUEVAS y el motivo de las excluidas.
"""
from __future__ import annotations

from collections import Counter

from app.gva_bolsas_complementarias import planificar_bolsas_gva_complementarias


def main() -> None:
    plan = planificar_bolsas_gva_complementarias()
    print(f"descubiertas={plan['descubiertas']} candidatas={plan['candidatas']} "
          f"nuevas={plan['nuevas']} actualizar={plan['actualizar']} ya_existentes={plan['ya_existentes']} "
          f"errores={len(plan['errores'])}")
    print("\nNUEVAS:")
    for a in plan["acciones"]:
        if a["accion"] == "NUEVA":
            print(f"  id_emp={a['id_emp']} fase={str(a['fase_gva'])[:30]!r} :: {str(a['denominacion'])[:100]}")
    print("\nEXCLUIDAS por motivo:", dict(Counter(e.get("motivo") for e in plan["excluidos"])))
    for e in plan["excluidos"]:
        if e.get("motivo") in ("promocion interna", "acto unico", "error_detalle"):
            print(f"  {e.get('motivo')}: id_emp={e['id_emp']}")


if __name__ == "__main__":
    main()
