from __future__ import annotations

import os
import sys
import time

import httpx


# EMPLOYMENT_API_URL es la configuración vigente. Se conserva el nombre
# histórico como alternativa temporal para no invalidar automatizaciones aún
# no migradas.
DEFAULT_API = "https://opocoach-web-staging-backend.onrender.com/empleo"
BASE_URL = (
    os.getenv("EMPLOYMENT_API_URL")
    or os.getenv("NETRETO_EMPLEO_API_URL")
    or DEFAULT_API
).rstrip("/")
SECRET = os.getenv("EMPLOYMENT_CRON_SECRET")

REINTENTOS = 3
ESPERAS_REINTENTO = (10, 30)


def _post_periodico(client: httpx.Client) -> None:
    path = "/admin/gestion/periodic?aplicar=true&dias_solape=7"
    ultima_excepcion: Exception | None = None

    for intento in range(1, REINTENTOS + 1):
        try:
            response = client.post(
                f"{BASE_URL}{path}",
                headers={"X-Cron-Secret": SECRET or ""},
            )

            if 400 <= response.status_code < 500:
                response.raise_for_status()

            if response.status_code >= 500:
                detalle = response.text[:1000].replace("\n", " ")
                print(
                    f"RESPUESTA {response.status_code} en {path}: {detalle}",
                    file=sys.stderr,
                )
                response.raise_for_status()

            data = response.json()
            print(path, data)
            return

        except httpx.HTTPStatusError as exc:
            ultima_excepcion = exc
            if exc.response.status_code < 500 or intento == REINTENTOS:
                raise
        except httpx.RequestError as exc:
            ultima_excepcion = exc
            if intento == REINTENTOS:
                raise

        espera = ESPERAS_REINTENTO[intento - 1]
        print(
            f"AVISO: fallo transitorio en {path}; reintento {intento + 1}/{REINTENTOS} "
            f"en {espera}s: {ultima_excepcion}",
            file=sys.stderr,
        )
        time.sleep(espera)

    if ultima_excepcion:
        raise ultima_excepcion


def main() -> int:
    if not SECRET:
        print("Falta EMPLOYMENT_CRON_SECRET", file=sys.stderr)
        return 2

    try:
        with httpx.Client(timeout=300.0) as client:
            _post_periodico(client)
    except httpx.HTTPError as exc:
        print(f"ERROR en ciclo periódico: {exc}", file=sys.stderr)
        return 1

    print("Actualización completa: ciclo periódico finalizado correctamente")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
