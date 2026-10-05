from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.boe_local_import import previsualizar_importacion_boe_local
from app.bop_alicante import importar_bop_alicante
from app.bop_castellon import importar_bop_castellon
from app.bop_valencia_municipios import importar_municipales_bop
from app.gva_adc import persistir_adc_gva
from app.gva_bolsas_complementarias import persistir_bolsas_gva_complementarias
from app.gva_estatal_service import importar_gva_estatal


def _ejecutar(nombre: str, funcion: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"fuente": nombre, "estado": "OK", "resultado": funcion()}
    except Exception as exc:
        return {
            "fuente": nombre,
            "estado": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }


def revisar_activos(*, hasta: date, dias: int = 180, aplicar: bool = False) -> dict[str, Any]:
    """Revisión histórica extraordinaria e idempotente de oportunidades activas.

    No sustituye al cron ordinario. Recorre de nuevo las fuentes capaces de
    descubrir altas administrativas y, para bolsas/ADC GVA, consulta el
    inventario oficial actualmente visible. Por defecto no escribe en BD.
    """
    if dias < 1:
        raise ValueError("dias debe ser >= 1")

    desde = hasta - timedelta(days=dias - 1)
    fuentes: list[dict[str, Any]] = []

    # BOE es deliberadamente independiente del BOP: así puede recuperar una
    # convocatoria aunque las bases BOP también hubieran faltado en una carga
    # histórica anterior. Esta es la laguna que el reconciliador BOE histórico
    # (orientado a procesos BOP ya existentes) no cubre.
    fuentes.append(_ejecutar(
        "boe_local_historico",
        lambda: previsualizar_importacion_boe_local(
            hasta=hasta,
            dias=dias,
            aplicar=aplicar,
        ),
    ))

    fuentes.append(_ejecutar(
        "bop_valencia_municipios",
        lambda: importar_municipales_bop(
            hasta=hasta,
            dias=dias,
            aplicar=aplicar,
        ),
    ))
    fuentes.append(_ejecutar(
        "bop_castellon",
        lambda: importar_bop_castellon(
            desde=desde,
            hasta=hasta,
            aplicar=aplicar,
        ),
    ))
    fuentes.append(_ejecutar(
        "bop_alicante",
        lambda: importar_bop_alicante(
            dias_solape=dias,
            hasta=hasta,
            max_items=5000,
            aplicar=aplicar,
        ),
    ))

    # El descubrimiento estatal sí usa intervalo. El seguimiento de fichas GVA
    # ya persistidas se refresca dentro del propio servicio.
    fuentes.append(_ejecutar(
        "gva_estatal",
        lambda: importar_gva_estatal(
            desde=desde,
            hasta=hasta,
            aplicar=aplicar,
        ),
    ))

    # Bolsas y ADC se descubren por el catálogo oficial vigente, no por fecha.
    # Esto es preferible para una revisión de "activos": recupera elementos
    # todavía publicados aunque su alta sea anterior a la ventana histórica.
    fuentes.append(_ejecutar(
        "gva_bolsas_administrativas_activas",
        lambda: persistir_bolsas_gva_complementarias(aplicar=aplicar),
    ))
    fuentes.append(_ejecutar(
        "gva_adc_activos",
        lambda: persistir_adc_gva(aplicar=aplicar),
    ))

    return {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "escrituras_bd": aplicar,
        "notificaciones": False,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "dias": dias,
        "fuentes": fuentes,
        "resumen": {
            "total": len(fuentes),
            "ok": sum(x["estado"] == "OK" for x in fuentes),
            "errores": sum(x["estado"] == "ERROR" for x in fuentes),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Revisión extraordinaria de oportunidades, bolsas y ADC activos. "
            "Por defecto solo diagnostica; --aplicar habilita escrituras idempotentes."
        )
    )
    parser.add_argument("--hasta", default=date.today().isoformat())
    parser.add_argument("--dias", type=int, default=180)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    resultado = revisar_activos(
        hasta=date.fromisoformat(args.hasta),
        dias=args.dias,
        aplicar=args.aplicar,
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 1 if resultado["resumen"]["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
