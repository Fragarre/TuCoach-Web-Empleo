"""Cron de actualización de Empleo, modo HTTP (llama al endpoint del backend).

Preferible el modo directo (``scripts/run_periodic_http.py --aplicar``): no
depende de una URL, ni de un timeout HTTP, ni mantiene una petición abierta
durante todo el ciclo. Este modo se conserva para despliegues donde el cron no
tiene acceso a la base de datos.

Códigos de salida: 0 correcto (o ciclo omitido por otro en curso), 1 el ciclo
terminó con fuentes en error o la llamada falló, 2 configuración incompleta.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import httpx


def _cargar_ciclos():
    """ciclos.py solo usa la librería estándar al importarse: se carga por ruta
    para que este fichero funcione igual como módulo que como script suelto."""
    ruta = Path(__file__).resolve().parent / "ciclos.py"
    spec = importlib.util.spec_from_file_location("_ciclos_cron", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_ciclos = _cargar_ciclos()

# EMPLOYMENT_API_URL es la configuración vigente (el nombre histórico se
# acepta temporalmente). NO hay URL por defecto: antes apuntaba al backend de
# STAGING y un cron mal configurado actualizaba la base equivocada sin avisar.
BASE_URL = (
    os.getenv("EMPLOYMENT_API_URL") or os.getenv("NETRETO_EMPLEO_API_URL") or ""
).rstrip("/")
SECRET = os.getenv("EMPLOYMENT_CRON_SECRET")
TIMEOUT_SEGUNDOS = float(os.getenv("EMPLOYMENT_CRON_TIMEOUT", "1500"))

REINTENTOS = 3
ESPERAS_REINTENTO = (10, 30)
PATH = "/admin/gestion/periodic?aplicar=true&dias_solape=7"


def _post_periodico(client: httpx.Client) -> dict:
    """Lanza el ciclo. Solo reintenta cuando la petición no llegó al servidor
    (fallo de conexión) o este devolvió un 5xx. Un timeout de LECTURA no se
    reintenta: el servidor sigue ejecutando el ciclo y relanzarlo solo lo
    duplicaría (el lock lo omitiría, pero se perdería el resultado)."""
    ultima: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            response = client.post(f"{BASE_URL}{PATH}", headers={"X-Cron-Secret": SECRET or ""})
            if response.status_code >= 500:
                print(f"RESPUESTA {response.status_code}: {response.text[:1000]!r}", file=sys.stderr)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            ultima = exc
            if exc.response.status_code < 500 or intento == REINTENTOS:
                raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            ultima = exc
            if intento == REINTENTOS:
                raise
        espera = ESPERAS_REINTENTO[intento - 1]
        print(f"AVISO: fallo transitorio; reintento {intento + 1}/{REINTENTOS} en {espera}s: {ultima}", file=sys.stderr)
        time.sleep(espera)
    raise ultima  # pragma: no cover


def main() -> int:
    if not SECRET:
        print("Falta EMPLOYMENT_CRON_SECRET", file=sys.stderr)
        return 2
    if not BASE_URL:
        print("Falta EMPLOYMENT_API_URL (no hay valor por defecto a propósito)", file=sys.stderr)
        return 2

    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT_SEGUNDOS, connect=15.0)) as client:
            payload = _post_periodico(client)
    except httpx.ReadTimeout:
        print(
            "ERROR: el servidor no respondió a tiempo. El ciclo puede seguir en "
            "ejecución: revisa la tabla ciclos_periodicos antes de relanzar.",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"ERROR en ciclo periódico: {exc}", file=sys.stderr)
        return 1

    evaluacion = _ciclos.evaluar_resultado(payload, errores_tolerados=_ciclos.errores_tolerados_env())
    for mensaje in evaluacion.mensajes:
        print(f"{evaluacion.nivel}: {mensaje}", file=sys.stderr)
    print(PATH, payload.get("resumen_fuentes"), payload.get("ciclo"))
    if evaluacion.codigo == 0:
        print("Actualización completa: ciclo periódico finalizado correctamente")
    return evaluacion.codigo


if __name__ == "__main__":
    raise SystemExit(main())
