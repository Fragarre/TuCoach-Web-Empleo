from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import Header, HTTPException, Query

from .empleo_admin_catalogo import router
from .clasificacion_auditoria import revisar_clasificacion_puestos
from .periodic import ejecutar_periodico

logger = logging.getLogger(__name__)


def _autorizado(x_import_secret: str | None, x_cron_secret: str | None) -> bool:
    import_secret = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    cron_secret = os.getenv("EMPLOYMENT_CRON_SECRET")
    if import_secret and x_import_secret and hmac.compare_digest(x_import_secret, import_secret):
        return True
    if cron_secret and x_cron_secret and hmac.compare_digest(x_cron_secret, cron_secret):
        return True
    return False


def _autorizado_clasificacion(
    x_import_secret: str | None,
    x_cron_secret: str | None,
    x_classification_secret: str | None,
) -> bool:
    """Permite una credencial acotada al backfill de clasificación."""
    if _autorizado(x_import_secret, x_cron_secret):
        return True
    secreto = os.getenv("EMPLOYMENT_CLASSIFICATION_SECRET")
    return bool(secreto and x_classification_secret and hmac.compare_digest(x_classification_secret, secreto))


@router.post("/periodic")
def periodic_empleo(
    aplicar: bool = Query(default=False),
    dias_solape: int = Query(default=7, ge=1, le=30),
    x_import_secret: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ejecuta o previsualiza el ciclo periódico de fuentes validadas."""
    if not _autorizado(x_import_secret, x_cron_secret):
        raise HTTPException(status_code=403, detail="No autorizado")
    try:
        return ejecutar_periodico(aplicar=aplicar, dias_solape=dias_solape)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error en ciclo periódico de Empleo")
        raise HTTPException(status_code=502, detail=f"Error en ciclo periódico de Empleo: {exc}") from exc


@router.post("/clasificacion-puestos")
def clasificacion_puestos(
    aplicar: bool = Query(default=False),
    limite: int = Query(default=25, ge=1, le=50),
    desde_id: int = Query(default=0, ge=0),
    x_import_secret: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
    x_classification_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Audita o completa una tanda acotada de clasificaciones documentadas.

    El valor por defecto es solo lectura. El límite evita que una fuente lenta
    afecte a la disponibilidad pública.
    """
    if not _autorizado_clasificacion(x_import_secret, x_cron_secret, x_classification_secret):
        raise HTTPException(status_code=403, detail="No autorizado")
    try:
        return revisar_clasificacion_puestos(
            aplicar=aplicar,
            limite=limite,
            desde_id=desde_id,
        )
    except Exception as exc:
        logger.exception("Error auditando clasificación de puestos")
        raise HTTPException(status_code=502, detail=f"Error auditando clasificación: {exc}") from exc


@router.post("/periodic-diagnostico-fuente")
def periodic_diagnostico_fuente(
    x_import_secret: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Diagnóstico temporal, de solo lectura, del nuevo buscador estatal."""
    if not _autorizado(x_import_secret, x_cron_secret):
        raise HTTPException(status_code=403, detail="No autorizado")

    url = "https://administracion.gob.es/empleopublico/resultadosEmpleo"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(45.0, connect=15.0),
            headers={
                "User-Agent": "NetReto-Empleo/1.0 (https://netexamenes.com)",
                "Accept-Language": "es-ES,es;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            respuesta = client.get(url)

        muestra = respuesta.text[:12000]
        logger.warning(
            "DIAGNOSTICO_FUENTE_ESTATAL status=%s url_final=%s content_type=%s longitud=%s muestra=%r",
            respuesta.status_code,
            str(respuesta.url),
            respuesta.headers.get("content-type"),
            len(respuesta.content),
            muestra,
        )
        return {
            "status": respuesta.status_code,
            "url_final": str(respuesta.url),
            "content_type": respuesta.headers.get("content-type"),
            "longitud": len(respuesta.content),
        }
    except Exception as exc:
        logger.exception("DIAGNOSTICO_FUENTE_ESTATAL error")
        raise HTTPException(status_code=502, detail=f"Error diagnóstico fuente estatal: {exc}") from exc
