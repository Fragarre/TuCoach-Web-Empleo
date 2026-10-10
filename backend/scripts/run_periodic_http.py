"""Ejecuta el ciclo periódico de TuCoach Empleo directamente (sin HTTP).

Es el entrypoint recomendado para el cron de Render: no depende de una URL ni
de un timeout de petición, y el código de salida refleja el resultado real:

    0  ciclo correcto (o omitido porque hay otro en curso)
    1  alguna fuente terminó en ERROR (por encima de EMPLOYMENT_CRON_ERRORES_TOLERADOS)
       o el ciclo lanzó una excepción
    2  uso incorrecto

Ordinario (cron diario):      python scripts/run_periodic_http.py --aplicar
Carga histórica inicial:      python scripts/run_periodic_http.py --aplicar --historico --dias 730
Solo revisar (no escribe):    python scripts/run_periodic_http.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Render ejecuta este archivo desde el directorio backend. Añadimos ese
# directorio al path para importar la aplicación sin depender del servidor web.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ciclos import errores_tolerados_env, evaluar_resultado
from app.periodic import ejecutar_periodico


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta directamente el ciclo periódico de TuCoach Empleo")
    parser.add_argument("--aplicar", action="store_true", help="Aplica cambios; sin esta opción solo revisa")
    parser.add_argument(
        "--dias",
        type=int,
        default=7,
        help="Solape mínimo en días (1-30 en modo ordinario; en ordinario la ventana de cada "
             "fuente se amplía sola desde su último éxito)",
    )
    parser.add_argument(
        "--historico",
        action="store_true",
        help="Autoriza una ventana superior a 30 días (carga inicial); desactiva notificaciones",
    )
    args = parser.parse_args()

    try:
        payload = ejecutar_periodico(
            aplicar=args.aplicar,
            dias_solape=args.dias,
            historico=args.historico,
        )
    except ValueError as exc:
        print(f"Parámetros no válidos: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error ejecutando ciclo periódico: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    evaluacion = evaluar_resultado(payload, errores_tolerados=errores_tolerados_env())
    for mensaje in evaluacion.mensajes:
        print(f"{evaluacion.nivel}: {mensaje}", file=sys.stderr)
    return evaluacion.codigo


if __name__ == "__main__":
    raise SystemExit(main())
