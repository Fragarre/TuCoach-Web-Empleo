from __future__ import annotations

from datetime import date, timedelta
import re
import time
from typing import Any

import httpx

from .gva_estatal_seguimiento import (
    _cargar_procesos_activos,
    _obtener_html,
    _tokens_identidad,
    extraer_seguimientos_validos,
)
from .festivos import hoy_es
from .gva_estatal_source import nuevo_cliente, nuevo_cliente_dogv

DOGV_API = "https://dogv.gva.es/dogv-portal"


def _codigo_insercion(signatura: str) -> str:
    anio, numero = signatura.split("_", 1)
    return f"{anio}/{numero}"


def _fecha_url_dogv(url: str | None) -> str | None:
    m = re.search(r"/datos/(\d{4})/(\d{2})/(\d{2})/", str(url or ""), re.I)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


# Caché de UN ciclo. Cada proceso activo consulta ±5 días en torno a sus fechas
# y esas ventanas se solapan casi por completo entre procesos: sin caché se
# repetían las mismas peticiones (que además viajan por el proxy de pago).
_CACHE_DIARIOS: dict[str, dict[str, Any]] = {}
_CACHE_DETALLES: dict[int, dict[str, Any]] = {}
REINTENTOS_DOGV = 3


def limpiar_cache_dogv() -> None:
    _CACHE_DIARIOS.clear()
    _CACHE_DETALLES.clear()


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET JSON con reintentos ante fallos transitorios (red, timeout, 5xx, 429).
    Los 4xx restantes se propagan de inmediato: reintentarlos no los arregla."""
    ultimo: Exception | None = None
    for intento in range(1, REINTENTOS_DOGV + 1):
        try:
            respuesta = client.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            estado = getattr(getattr(exc, "response", None), "status_code", None)
            if estado is not None and estado < 500 and estado != 429:
                raise
            if intento < REINTENTOS_DOGV:
                time.sleep(2 * intento)
    assert ultimo is not None
    raise ultimo


def _leer_diario(client: httpx.Client, fecha: str) -> dict[str, Any]:
    # El diario de hoy puede seguir ampliándose: solo se cachean días cerrados.
    cacheable = fecha < hoy_es().isoformat()
    if cacheable and fecha in _CACHE_DIARIOS:
        return _CACHE_DIARIOS[fecha]
    diario = _get_json(client, f"{DOGV_API}/dogv", {"date": fecha, "lang": "es_es"})
    if cacheable:
        _CACHE_DIARIOS[fecha] = diario
    return diario


def _detalle_disposicion(client: httpx.Client, resumen: dict[str, Any]) -> dict[str, Any]:
    dogv_id = int(resumen["id"])
    if dogv_id in _CACHE_DETALLES:
        return _CACHE_DETALLES[dogv_id]
    detalle = _get_json(client, f"{DOGV_API}/disposicion/{dogv_id}", {"lang": "es_es"})
    texto_identidad = " ".join(str(x or "") for x in (detalle.get("titulo"), detalle.get("texto")))
    salida = {
        "ok": True,
        "id_dogv": dogv_id,
        "codigo_insercion": detalle.get("codigoInsercion") or resumen.get("codigoInsercion"),
        "cve": detalle.get("cve"),
        "numero_dogv": detalle.get("numeroDogv"),
        "fecha_publicacion": detalle.get("fechaPublicacion") or resumen.get("fechaPublicacion"),
        "titulo": detalle.get("titulo") or resumen.get("titulo"),
        "organismo": detalle.get("organismo") or resumen.get("organismo"),
        "tokens_dogv": sorted(_tokens_identidad(texto_identidad)),
    }
    _CACHE_DETALLES[dogv_id] = salida
    return salida


def _obtener_disposicion_dogv(client: httpx.Client, *, signatura: str, fecha_publicacion: str | None) -> dict[str, Any]:
    if not fecha_publicacion:
        return {"ok": False, "motivo": "fecha_publicacion_ausente"}
    codigo = _codigo_insercion(signatura)
    base = date.fromisoformat(fecha_publicacion)
    fechas = [base]
    for distancia in range(1, 6):
        fechas.extend((base - timedelta(days=distancia), base + timedelta(days=distancia)))
    for indice, fecha in enumerate(fechas):
        fecha_iso = fecha.isoformat()
        diario = _leer_diario(client, fecha_iso)
        coincidencias = [x for x in (diario.get("disposiciones") or []) if str(x.get("codigoInsercion") or "").strip() == codigo]
        if len(coincidencias) == 1:
            salida = dict(_detalle_disposicion(client, coincidencias[0]))
            salida["fecha_consultada"] = fecha_iso
            salida["fecha_resuelta_por"] = "FECHA_EXACTA" if indice == 0 else "CODIGO_EXACTO_VENTANA_5_DIAS"
            return salida
        if len(coincidencias) > 1:
            return {"ok": False, "motivo": "codigo_insercion_no_unico_en_fecha", "codigo_insercion": codigo, "fecha_publicacion": fecha_iso}
    return {"ok": False, "motivo": "codigo_insercion_no_localizado_en_ventana", "codigo_insercion": codigo, "fecha_publicacion": fecha_publicacion}


def _coincide_identidad(identidad: set[str], tokens: set[str]) -> tuple[bool, list[str]]:
    comunes = sorted(identidad & tokens)
    especificos = {x for x in identidad if x.startswith("CONVOCATORIA:") or x.startswith("ORDEN:") or x.startswith("BOLSA:")}
    codigos = {x for x in identidad if x.startswith("CODIGO:")}
    ok = bool(especificos & tokens) and (not codigos or bool(codigos & tokens))
    return ok, comunes


def _descubrir_dogv_en_fechas(client: httpx.Client, *, identidad: set[str], fechas_semilla: set[str]) -> list[dict[str, Any]]:
    if not fechas_semilla:
        return []
    fechas: set[date] = set()
    for valor in fechas_semilla:
        try:
            base = date.fromisoformat(valor)
        except ValueError:
            continue
        for desplazamiento in range(-5, 6):
            fechas.add(base + timedelta(days=desplazamiento))
    encontrados: dict[str, dict[str, Any]] = {}
    for fecha in sorted(fechas):
        diario = _leer_diario(client, fecha.isoformat())
        for resumen in diario.get("disposiciones") or []:
            if not (identidad & _tokens_identidad(str(resumen.get("titulo") or ""))):
                continue
            detalle = _detalle_disposicion(client, resumen)
            ok, comunes = _coincide_identidad(identidad, set(detalle.get("tokens_dogv") or []))
            if not ok:
                continue
            codigo = str(detalle.get("codigo_insercion") or "").replace("/", "_")
            if codigo:
                encontrados[codigo] = {
                    "signatura": codigo,
                    "fecha_publicacion": detalle.get("fecha_publicacion"),
                    "titulo": detalle.get("titulo"),
                    "id_dogv": detalle.get("id_dogv"),
                    "cve": detalle.get("cve"),
                    "tokens_dogv": detalle.get("tokens_dogv") or [],
                    "coincidencias_identidad": comunes,
                }
    return [encontrados[k] for k in sorted(encontrados)]


def diagnosticar_seguimiento_dogv() -> dict[str, Any]:
    """Construye el plan de migración al DOGV directo. Siempre es solo lectura."""
    limpiar_cache_dogv()
    procesos = _cargar_procesos_activos()
    salida: list[dict[str, Any]] = []
    with nuevo_cliente() as estatal, nuevo_cliente_dogv() as dogv:
        for proceso in procesos:
            proceso_id = int(proceso["id"])
            referencia = int(proceso["referencia_estatal"])
            html = _obtener_html(estatal, referencia)
            extraido = extraer_seguimientos_validos(html)
            identidad = set(extraido.get("identidad") or []) | _tokens_identidad(str(proceso.get("denominacion") or ""))
            datos = proceso.get("datos_json") or {}
            estado = datos.get("seguimiento_gva") if isinstance(datos.get("seguimiento_gva"), dict) else {}
            vistos = {str(x) for x in (estado.get("vistos") or [])}
            fechas_semilla: set[str] = set()
            validaciones: list[dict[str, Any]] = []
            for item in (extraido.get("validos") or []) + (extraido.get("rechazados") or []):
                fecha_url = _fecha_url_dogv(item.get("url"))
                fecha = fecha_url or item.get("fecha_publicacion")
                if fecha:
                    fechas_semilla.add(str(fecha))
                if item in (extraido.get("validos") or []):
                    oficial = _obtener_disposicion_dogv(dogv, signatura=item["signatura"], fecha_publicacion=fecha)
                    ok, comunes = _coincide_identidad(identidad, set(oficial.get("tokens_dogv") or []))
                    validaciones.append({"signatura": item["signatura"], "dogv": oficial, "coincidencias_identidad_dogv": comunes, "validacion_dogv": bool(oficial.get("ok") and ok)})
            directos = _descubrir_dogv_en_fechas(dogv, identidad=identidad, fechas_semilla=fechas_semilla)
            directos_ids = {x["signatura"] for x in directos}
            es_bolsa = any(x.startswith("BOLSA:") for x in identidad)
            if es_bolsa:
                plan = {"accion": "BOLSA_SEGUIMIENTO_SIMPLIFICADO", "vistos_propuestos": sorted(vistos), "novedades_historicas": []}
            else:
                plan = {
                    "accion": "MIGRAR_BASELINE_DOGV",
                    "vistos_anteriores": sorted(vistos),
                    "vistos_propuestos": sorted(directos_ids),
                    "eliminar_del_estado": sorted(vistos - directos_ids),
                    "incorporar_al_baseline": sorted(directos_ids - vistos),
                    "novedades_historicas": [],
                }
            salida.append({
                "proceso_id": proceso_id,
                "identificador_estable": proceso.get("identificador_estable"),
                "denominacion": proceso.get("denominacion"),
                "referencia_estatal": referencia,
                "identidad": sorted(identidad),
                "validaciones": validaciones,
                "descubrimiento_dogv_directo": directos,
                "plan_migracion": plan,
            })
    return {
        "modo": "SOLO_DIAGNOSTICO",
        "escrituras_bd": False,
        "fuente_propuesta": DOGV_API,
        "regla_bolsas": "SEGUIMIENTO_SIMPLIFICADO",
        "procesos": salida,
        "resumen": {
            "procesos": len(salida),
            "convocatorias_a_migrar": sum(x["plan_migracion"]["accion"] == "MIGRAR_BASELINE_DOGV" for x in salida),
            "bolsas_simplificadas": sum(x["plan_migracion"]["accion"] == "BOLSA_SEGUIMIENTO_SIMPLIFICADO" for x in salida),
            "eliminaciones_estado_propuestas": sum(len(x["plan_migracion"].get("eliminar_del_estado") or []) for x in salida),
            "incorporaciones_baseline_propuestas": sum(len(x["plan_migracion"].get("incorporar_al_baseline") or []) for x in salida),
        },
    }
