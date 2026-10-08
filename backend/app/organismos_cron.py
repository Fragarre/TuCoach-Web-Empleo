from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from typing import Any, Callable

from .alicante_otras_entidades import bootstrap_otras_entidades_alicante
from .bop_alicante import importar_bop_alicante
from .bop_castellon import importar_bop_castellon
from .bop_valencia_municipios import importar_municipales_bop
from .bop_valencia_patch import importar_bop_valencia
from .gva_adc import persistir_adc_gva
from .gva_bolsas_complementarias import persistir_bolsas_gva_complementarias
from .gva_cesion_datos import persistir_cesiones_gva
from .gva_estatal_service import importar_gva_estatal


DIAS_SOLAPE_DEFECTO = 7

ORGANISMOS = (
    "gva",
    "diputacion_valencia",
    "ayuntamientos_valencia",
    "diputacion_alicante",
    "ayuntamientos_alicante",
    "diputacion_castellon",
    "ayuntamientos_castellon",
)


def _rango(*, hoy: date, dias: int) -> tuple[date, date]:
    if dias < 1:
        raise ValueError("dias debe ser >= 1")
    return hoy - timedelta(days=dias - 1), hoy


def ejecutar_gva(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    desde, hasta = _rango(hoy=hoy, dias=dias)
    resultado: dict[str, Any] = {
        "organismo": "gva",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "componentes": {},
    }
    resultado["componentes"]["oportunidades"] = importar_gva_estatal(
        desde=desde,
        hasta=hasta,
        aplicar=aplicar,
    )
    resultado["componentes"]["bolsas"] = persistir_bolsas_gva_complementarias(
        aplicar=aplicar,
    )
    resultado["componentes"]["adc"] = persistir_adc_gva(
        aplicar=aplicar,
    )
    resultado["componentes"]["cesiones"] = persistir_cesiones_gva(
        aplicar=aplicar,
    )
    if aplicar and os.getenv("EMPLOYMENT_CLASSIFICATION_ENRICHMENT", "false").lower() == "true":
        from .clasificacion_auditoria import revisar_clasificacion_puestos

        resultado["componentes"]["clasificacion_puestos"] = revisar_clasificacion_puestos(
            aplicar=True,
            limite=15,
            ordenar_por_reciente=True,
        )
    return resultado


def ejecutar_diputacion_valencia(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    if not aplicar:
        from .bop_valencia_patch import diagnosticar_bop
        import httpx

        headers = {
            "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
            "Accept-Language": "es-ES,es;q=0.9",
        }
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
            return {
                "organismo": "diputacion_valencia",
                "modo": "SOLO_DIAGNOSTICO",
                "resultado": diagnosticar_bop(client, fecha=hoy.isoformat()),
            }
    return {
        "organismo": "diputacion_valencia",
        "resultado": importar_bop_valencia(historico=True, dias=dias),
    }


def ejecutar_ayuntamientos_valencia(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    return {
        "organismo": "ayuntamientos_valencia",
        "resultado": importar_municipales_bop(
            hasta=hoy,
            dias=dias,
            aplicar=aplicar,
        ),
    }


def ejecutar_diputacion_alicante(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    return {
        "organismo": "diputacion_alicante",
        "resultado": bootstrap_otras_entidades_alicante(
            max_items=200,
            aplicar=aplicar,
        ),
    }


def ejecutar_ayuntamientos_alicante(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    return {
        "organismo": "ayuntamientos_alicante",
        "resultado": importar_bop_alicante(
            dias_solape=dias,
            hasta=hoy,
            aplicar=aplicar,
        ),
    }


def ejecutar_diputacion_castellon(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    desde, hasta = _rango(hoy=hoy, dias=dias)
    return {
        "organismo": "diputacion_castellon",
        "resultado": importar_bop_castellon(
            desde=desde,
            hasta=hasta,
            aplicar=aplicar,
            tipo_organismo="DIPUTACION",
        ),
    }


def ejecutar_ayuntamientos_castellon(*, hoy: date, dias: int, aplicar: bool) -> dict[str, Any]:
    desde, hasta = _rango(hoy=hoy, dias=dias)
    return {
        "organismo": "ayuntamientos_castellon",
        "resultado": importar_bop_castellon(
            desde=desde,
            hasta=hasta,
            aplicar=aplicar,
            tipo_organismo="AYUNTAMIENTO",
        ),
    }


EJECUTORES: dict[str, Callable[..., dict[str, Any]]] = {
    "gva": ejecutar_gva,
    "diputacion_valencia": ejecutar_diputacion_valencia,
    "ayuntamientos_valencia": ejecutar_ayuntamientos_valencia,
    "diputacion_alicante": ejecutar_diputacion_alicante,
    "ayuntamientos_alicante": ejecutar_ayuntamientos_alicante,
    "diputacion_castellon": ejecutar_diputacion_castellon,
    "ayuntamientos_castellon": ejecutar_ayuntamientos_castellon,
}


def ejecutar_organismo(
    organismo: str,
    *,
    hoy: date | None = None,
    dias: int = DIAS_SOLAPE_DEFECTO,
    aplicar: bool = False,
) -> dict[str, Any]:
    if organismo not in EJECUTORES:
        raise ValueError(f"Organismo no válido: {organismo}. Opciones: {', '.join(ORGANISMOS)}")
    fecha = hoy or date.today()
    return EJECUTORES[organismo](hoy=fecha, dias=dias, aplicar=aplicar)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta un organismo de TuCoach Empleo de forma independiente.")
    parser.add_argument("--organismo", choices=ORGANISMOS, required=True)
    parser.add_argument("--dias", type=int, default=DIAS_SOLAPE_DEFECTO)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()

    resultado = ejecutar_organismo(
        args.organismo,
        dias=args.dias,
        aplicar=args.aplicar,
    )
    import json

    print(json.dumps(resultado, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
