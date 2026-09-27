from typing import Any
import hmac
import os
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from access import exigir_employment_access
from auth import UsuarioAutenticado, usuario_actual
from .bop_valencia_patch import diagnosticar_bop, importar_bop_valencia
from .gva_enhanced import importar_gva_robusto, limpiar_gva_navegacion
from .gva_cleanup import limpiar_gva_stale, corregir_turnos_gva
from .gva_estatal_service import diagnosticar_filtros_plazo_gva, importar_gva_estatal
from .diputacion_alicante import diagnosticar_diputacion_alicante, importar_diputacion_alicante
from .ayuntamiento_alicante import diagnosticar_ayuntamiento_alicante, importar_ayuntamiento_alicante
from .alicante_otras_entidades import bootstrap_otras_entidades_alicante
from .boe_local_import import recuperar_boe_para_proceso_bop
from .empleo_admin import (
    listar_pendientes_revision,
    actualizar_revision,
    guardar_temario,
    obtener_temario,
    guardar_temas,
    router as empleo_admin_router,
)
from .empleo_admin_catalogo import router as empleo_admin_catalogo_router
from . import periodic_endpoint
from .historial import listar_publicaciones, listar_cambios
from .organismos import listar_fuentes, listar_organismos, obtener_organismo
from .procesos import listar_procesos, obtener_proceso
from .seguimiento import (
    suscripciones_usuario,
    suscripcion_usuario_proceso,
    suscribirse,
    cancelar_suscripcion,
    cambios_usuario,
    estado_novedades_usuario,
    marcar_novedades_vistas,
)

app = FastAPI(title="TuCoach Empleo API", version="0.1.0")
app.include_router(empleo_admin_router)
app.include_router(empleo_admin_catalogo_router)

class RevisionRequest(BaseModel):
    estado: str
    observaciones: str | None = None

class TemarioRequest(BaseModel):
    contenido_texto: str = Field(min_length=1)
    origen: str = "MANUAL"
    estado: str = "PENDIENTE_REVISION"
    fuente_url: str | None = None
    fuente_publicacion_id: int | None = None
    observaciones: str | None = None

class TemaRequest(BaseModel):
    numero: int | None = None
    titulo: str | None = None
    contenido_texto: str = Field(min_length=1)

class TemasRequest(BaseModel):
    temas: list[TemaRequest]

def _usuario_con_empleo(usuario: UsuarioAutenticado = Depends(usuario_actual)) -> UsuarioAutenticado:
    exigir_employment_access(usuario.id, usuario.access_token)
    return usuario


def _usuario_con_seguimiento(
    usuario: UsuarioAutenticado = Depends(usuario_actual),
) -> UsuarioAutenticado:
    acceso = exigir_employment_access(usuario.id, usuario.access_token)
    if not acceso.subscribed:
        raise HTTPException(
            status_code=403,
            detail="El seguimiento de oportunidades requiere una suscripción activa.",
        )
    return usuario

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "netreto-empleo"}

@app.get("/me")
def me(usuario: UsuarioAutenticado = Depends(usuario_actual)) -> dict[str, Any]:
    acceso = exigir_employment_access(usuario.id, usuario.access_token)
    return {"id": str(usuario.id), "email": usuario.email, "employment_access": acceso.employment_access, "subscribed": acceso.subscribed}

@app.get("/organismos")
def organismos(solo_activos: bool = Query(True), _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> list[dict[str, Any]]:
    return listar_organismos(solo_activos=solo_activos)

@app.get("/organismos/{organismo_id}")
def organismo(organismo_id: int, _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> dict[str, Any]:
    resultado = obtener_organismo(organismo_id)
    if resultado is None: raise HTTPException(status_code=404, detail="Organismo no encontrado")
    return resultado

@app.get("/fuentes")
def fuentes(organismo_id: int | None = Query(None), solo_activas: bool = Query(True), _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> list[dict[str, Any]]:
    return listar_fuentes(organismo_id=organismo_id, solo_activas=solo_activas)

@app.get("/procesos")
def procesos(organismo_id: int | None = Query(default=None), estado: str | None = Query(default=None), limite: int = Query(default=100, ge=1, le=200), _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> list[dict[str, Any]]:
    return listar_procesos(organismo_id=organismo_id, estado=estado, limite=limite)

@app.get("/procesos/{proceso_id}")
def proceso(proceso_id: int, _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> dict[str, Any]:
    resultado = obtener_proceso(proceso_id)
    if resultado is None: raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return resultado

@app.get("/procesos/{proceso_id}/publicaciones")
def publicaciones_proceso(proceso_id: int, limite: int = Query(default=100, ge=1, le=200), _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> list[dict[str, Any]]:
    if obtener_proceso(proceso_id) is None: raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return listar_publicaciones(proceso_id=proceso_id, limite=limite)

@app.get("/procesos/{proceso_id}/cambios")
def cambios_proceso(proceso_id: int, limite: int = Query(default=100, ge=1, le=200), _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> list[dict[str, Any]]:
    if obtener_proceso(proceso_id) is None: raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return listar_cambios(proceso_id=proceso_id, limite=limite)

@app.get("/procesos/{proceso_id}/temario")
def temario_proceso(proceso_id: int, _: UsuarioAutenticado = Depends(_usuario_con_empleo)) -> dict[str, Any]:
    if obtener_proceso(proceso_id) is None: raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return obtener_temario(proceso_id) or {"proceso_id": proceso_id, "temario": None}

@app.get("/suscripciones")
def suscripciones(usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> list[dict[str, Any]]:
    return suscripciones_usuario(usuario.id)

@app.get("/suscripciones/{proceso_id}")
def suscripcion_proceso(proceso_id: int, usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> dict[str, Any]:
    return suscripcion_usuario_proceso(usuario.id, proceso_id) or {"activa": False, "proceso_id": proceso_id}

@app.post("/suscripciones/{proceso_id}")
def alta_suscripcion(proceso_id: int, usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> dict[str, Any]:
    try: return suscribirse(usuario.id, proceso_id)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.delete("/suscripciones/{proceso_id}")
def baja_suscripcion(proceso_id: int, usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> dict[str, Any]:
    return {"proceso_id": proceso_id, "activa": False, "cancelada": cancelar_suscripcion(usuario.id, proceso_id)}

@app.get("/seguimiento/cambios")
def seguimiento_cambios(limite: int = Query(default=100, ge=1, le=200), usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> list[dict[str, Any]]:
    return cambios_usuario(usuario.id, limite=limite)

@app.get("/seguimiento/estado")
def seguimiento_estado(usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> dict[str, Any]:
    return estado_novedades_usuario(usuario.id)

@app.post("/seguimiento/estado/visto")
def seguimiento_estado_visto(hasta: str | None = Query(default=None), usuario: UsuarioAutenticado = Depends(_usuario_con_seguimiento)) -> dict[str, Any]:
    from datetime import datetime
    fecha = None
    if hasta:
        try: fecha = datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        except ValueError as exc: raise HTTPException(status_code=400, detail="Fecha 'hasta' no válida") from exc
    return marcar_novedades_vistas(usuario.id, fecha)

def _validar_import_secret(x_import_secret: str | None) -> None:
    secreto = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    if not secreto or not x_import_secret or not hmac.compare_digest(x_import_secret, secreto): raise HTTPException(status_code=403, detail="No autorizado")

@app.post("/admin/import/gva")
def importar_gva_endpoint(x_import_secret: str | None = Header(default=None), max_paginas: int = Query(default=3, ge=1, le=10), max_detalles: int | None = Query(default=None, ge=1, le=300)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        resultado = importar_gva_robusto(max_paginas=max_paginas, max_detalles=max_detalles)
        if resultado.get("estado_importacion") == "COMPLETA":
            resultado["limpieza_stale"] = limpiar_gva_stale()
            resultado["correccion_turnos"] = corregir_turnos_gva()
        else:
            resultado["limpieza_stale"] = {"omitida": True, "motivo": "fuente_gva_incompleta"}
            resultado["correccion_turnos"] = {"omitida": True, "motivo": "fuente_gva_incompleta"}
        return resultado
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en importación GVA: {exc}") from exc

@app.post("/admin/import/gva-estatal")
def importar_gva_estatal_endpoint(
    desde: date = Query(...),
    hasta: date = Query(...),
    aplicar: bool = Query(default=False),
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        return importar_gva_estatal(desde=desde, hasta=hasta, aplicar=aplicar)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en importación GVA estatal: {exc}") from exc

@app.get("/admin/debug/gva-plazos")
def debug_gva_plazos(
    x_import_secret: str | None = Header(default=None),
    id_emp_objetivo: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        return diagnosticar_filtros_plazo_gva(id_emp_objetivo=id_emp_objetivo)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en diagnóstico de plazos GVA: {exc}") from exc

@app.get("/admin/debug/gva-conectividad")
def debug_gva_conectividad(
    x_import_secret: str | None = Header(default=None),
    id_emp: int = Query(default=110595, ge=1),
) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    import socket
    import httpx

    host = "sede.gva.es"
    resultado: dict[str, Any] = {
        "modo": "SOLO_DIAGNOSTICO",
        "host": host,
        "id_emp": id_emp,
        "dns": {"ok": False, "direcciones": []},
        "pruebas_http": [],
    }

    try:
        info = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        direcciones = sorted({item[4][0] for item in info})
        resultado["dns"] = {"ok": True, "direcciones": direcciones}
    except Exception as exc:
        resultado["dns"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    objetivos = [
        ("portada", "https://sede.gva.es/es/"),
        ("ficha", f"https://sede.gva.es/es/detall-ocupacio-publica?id_emp={id_emp}&id_info=info_basica"),
        ("buscador", "https://sede.gva.es/es/cercador-ocupacio-publica?pagina=1&tipoOrganismo=1&plazos=A&tamanyoPagina=30"),
    ]
    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for nombre, url in objetivos:
            try:
                respuesta = client.get(url)
                resultado["pruebas_http"].append({
                    "nombre": nombre,
                    "ok": True,
                    "status_code": respuesta.status_code,
                    "url_final": str(respuesta.url),
                    "bytes": len(respuesta.content),
                })
            except Exception as exc:
                request_url = str(getattr(getattr(exc, "request", None), "url", url))
                resultado["pruebas_http"].append({
                    "nombre": nombre,
                    "ok": False,
                    "url": request_url,
                    "error": f"{type(exc).__name__}: {exc}",
                })

    return resultado

@app.post("/admin/cleanup/gva-stale")
def cleanup_gva_stale(x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return limpiar_gva_stale()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en limpieza GVA: {exc}") from exc

@app.post("/admin/cleanup/gva-turnos")
def cleanup_gva_turnos(x_import_secret: str | None = Header(default=None)) -> dict[str, int]:
    _validar_import_secret(x_import_secret)
    try: return corregir_turnos_gva()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en corrección de turnos: {exc}") from exc

@app.post("/admin/cleanup/gva-navegacion")
def cleanup_gva_navegacion(x_import_secret: str | None = Header(default=None)) -> dict[str, int]:
    _validar_import_secret(x_import_secret)
    try: return limpiar_gva_navegacion()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en limpieza GVA: {exc}") from exc

@app.get("/admin/debug/bop")
def debug_bop(x_import_secret: str | None = Header(default=None), fecha: str | None = Query(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        import httpx
        headers={"User-Agent":"NetReto-Empleo/0.1 (https://netexamenes.com)","Accept-Language":"es-ES,es;q=0.9"}
        with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client: return diagnosticar_bop(client, fecha=fecha)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en diagnóstico BOP: {exc}") from exc

@app.post("/admin/import/bop-valencia")
def importar_bop_valencia_endpoint(x_import_secret: str | None = Header(default=None), historico: bool = Query(default=False), dias: int = Query(default=250, ge=1, le=730)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        resultado=importar_bop_valencia(historico=historico,dias=dias); resultado["oportunidades_marcadas"]=True; return resultado
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en importación BOP Valencia: {exc}") from exc

@app.get("/admin/debug/diputacion-alicante")
def debug_diputacion_alicante(x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return diagnosticar_diputacion_alicante()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en diagnóstico Diputación Alicante: {exc}") from exc

@app.post("/admin/import/diputacion-alicante")
def importar_diputacion_alicante_endpoint(x_import_secret: str | None = Header(default=None), max_detalles: int = Query(default=100, ge=1, le=300)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return importar_diputacion_alicante(max_detalles=max_detalles)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en importación Diputación Alicante: {exc}") from exc

@app.get("/admin/debug/ayuntamiento-alicante")
def debug_ayuntamiento_alicante(x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return diagnosticar_ayuntamiento_alicante()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en diagnóstico Ayuntamiento Alicante: {exc}") from exc

@app.post("/admin/import/ayuntamiento-alicante")
def importar_ayuntamiento_alicante_endpoint(x_import_secret: str | None = Header(default=None), max_detalles: int = Query(default=150, ge=1, le=300)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return importar_ayuntamiento_alicante(max_detalles=max_detalles)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error en importación Ayuntamiento Alicante: {exc}") from exc

@app.post("/admin/import/alicante-bootstrap")
def importar_alicante_bootstrap_endpoint(
    aplicar: bool = Query(default=False),
    max_items: int = Query(default=200, ge=1, le=300),
    x_import_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try:
        return bootstrap_otras_entidades_alicante(max_items=max_items, aplicar=aplicar)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en bootstrap Alicante: {exc}") from exc

@app.post("/admin/import/castellon-boe-backfill")
def castellon_boe_backfill_endpoint(
    proceso_id: int = Query(..., ge=1),
    fecha_bases: date = Query(...),
    hasta: date | None = Query(default=None),
    aplicar: bool = Query(default=False),
    x_import_secret: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Recuperación BOE selectiva; por defecto sólo revisión."""
    import_secret = os.getenv("EMPLOYMENT_IMPORT_SECRET")
    cron_secret = os.getenv("EMPLOYMENT_CRON_SECRET")
    autorizado = (
        bool(import_secret and x_import_secret and hmac.compare_digest(x_import_secret, import_secret))
        or bool(cron_secret and x_cron_secret and hmac.compare_digest(x_cron_secret, cron_secret))
    )
    if not autorizado:
        raise HTTPException(status_code=403, detail="No autorizado")
    try:
        return recuperar_boe_para_proceso_bop(
            proceso_id=proceso_id,
            fecha_bases=fecha_bases,
            hasta=hasta,
            aplicar=aplicar,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error en recuperación BOE Castellón: {exc}") from exc

@app.get("/admin/revision/pendientes")
def revision_pendientes(x_import_secret: str | None = Header(default=None), limite: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    _validar_import_secret(x_import_secret)
    try: return listar_pendientes_revision(limite=limite)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Error listando pendientes: {exc}") from exc

@app.patch("/admin/procesos/{proceso_id}/revision")
def revision_proceso(proceso_id: int, payload: RevisionRequest, x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return actualizar_revision(proceso_id, payload.estado, observaciones=payload.observaciones)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.put("/admin/procesos/{proceso_id}/temario")
def admin_guardar_temario(proceso_id: int, payload: TemarioRequest, x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return guardar_temario(proceso_id, payload.contenido_texto, origen=payload.origen, estado=payload.estado, fuente_url=payload.fuente_url, fuente_publicacion_id=payload.fuente_publicacion_id, observaciones=payload.observaciones)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.put("/admin/procesos/{proceso_id}/temario/temas")
def admin_guardar_temas(proceso_id: int, payload: TemasRequest, x_import_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _validar_import_secret(x_import_secret)
    try: return guardar_temas(proceso_id, [x.model_dump() for x in payload.temas])
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
