from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


# El servicio web independiente histórico fue retirado. Empleo se sirve ahora
# desde el backend unificado, bajo el prefijo /empleo.
DEFAULT_API = "https://opocoach-web-staging-backend.onrender.com/empleo"


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta el ciclo periódico de TuCoach Empleo vía API segura")
    parser.add_argument("--aplicar", action="store_true", help="Aplica cambios; sin esta opción solo revisa")
    parser.add_argument("--dias", type=int, default=7, help="Días de solape (1-30)")
    args = parser.parse_args()

    secreto = os.getenv("EMPLOYMENT_CRON_SECRET")
    if not secreto:
        print("EMPLOYMENT_CRON_SECRET no está configurada", file=sys.stderr)
        return 2

    api = os.getenv("EMPLOYMENT_API_URL", DEFAULT_API).rstrip("/")
    url = f"{api}/admin/gestion/periodic"
    params = {
        "aplicar": "true" if args.aplicar else "false",
        "dias_solape": str(args.dias),
    }
    headers = {"X-Cron-Secret": secreto}

    try:
        with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=True) as client:
            respuesta = client.post(url, params=params, headers=headers)
            respuesta.raise_for_status()
    except Exception as exc:
        print(f"Error ejecutando ciclo periódico: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        payload = respuesta.json()
    except ValueError:
        print(respuesta.text)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
