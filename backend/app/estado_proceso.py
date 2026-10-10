from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any


def _desde_ruta(nombre: str):
    """Carga un módulo hermano por ruta (para cuando este fichero se carga
    aislado con importlib, como hacen los tests, y no hay paquete)."""
    import importlib.util
    import sys
    from pathlib import Path
    clave = f"_hermano_{nombre}"
    if clave in sys.modules:
        return sys.modules[clave]
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(clave, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[clave] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _cargar_hermano(nombre: str):
    if __package__:
        try:
            return __import__(f"{__package__}.{nombre}", fromlist=[nombre])
        except ImportError:
            pass
    return _desde_ruta(nombre)


_festivos = _cargar_hermano("festivos")
_ciclo = _cargar_hermano("ciclo_vida")


def _sin(texto: str | None) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def es_bolsa(tipo_proceso: str | None) -> bool:
    return _ciclo.es_bolsa(tipo_proceso)


def clasificar_evento_terminal(tipo_proceso: str | None, titulo: str | None) -> str | None:
    """Estado terminal acreditado por un título oficial (ver ciclo_vida)."""
    return _ciclo.clasificar_evento_terminal(tipo_proceso, titulo)


def _dias_habiles_literal(literal: str) -> int | None:
    normalizado = _sin(literal)
    # Cuando la convocatoria no precisa el tipo de día, el criterio del
    # catálogo es tratarlo como hábil. Si indica "naturales", se conserva
    # literalmente para no recalcularlo como hábil.
    if re.search(r"\bdias?\s+naturales?\b", normalizado, re.I):
        return None
    m = re.search(r"\b(\d+)\s+dias?(?:\s+habiles)?\b", normalizado, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([a-z]+)\s+dias?(?:\s+habiles)?\b", normalizado, re.I)
    if m:
        return _NUMEROS_PLAZO.get(m.group(1))
    return None


def _calcular_cierre_habiles(fecha_boe: date, dias: int, organismo: str | None) -> date | None:
    """Último día del plazo en días hábiles (sábados, domingos y festivos
    estatales/autonómicos CV; cada día se consulta con el calendario de su año).

    No incorpora festivos locales: el resultado debe mostrarse con advertencia
    para que el usuario confirme posibles días inhábiles del municipio.
    """
    if dias <= 0:
        return None
    return _festivos.sumar_dias_habiles(fecha_boe, dias)


def _fecha_iso(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor[:10])
        except ValueError:
            return None
    return None


def _estado_plazo_boe(
    *,
    fecha_boe: date,
    literal: str,
    hoy: date,
    organismo: str | None,
) -> dict[str, Any]:
    dias = _dias_habiles_literal(literal)
    cierre = _calcular_cierre_habiles(fecha_boe, dias, organismo) if dias else None
    if not cierre:
        return {
            "codigo": "PLAZO_LITERAL",
            "fecha_referencia": fecha_boe + timedelta(days=1),
            "dias_habiles": dias,
            "literal": literal,
        }

    apertura = fecha_boe + timedelta(days=1)
    if hoy < apertura:
        codigo = "PENDIENTE_APERTURA"
    elif hoy <= cierre:
        codigo = "ABIERTO"
    else:
        codigo = "CERRADO"
    return {
        "codigo": codigo,
        "fecha_apertura": apertura,
        "fecha_cierre": cierre,
        "fecha_cierre_calculada": True,
        "calendario_aplicado": "COMUNITAT_VALENCIANA",
        "calendario_verificado": all(
            _festivos.calendario_verificado(y)
            for y in _festivos.anios_tocados(fecha_boe, cierre)
        ),
        "dias_habiles": dias,
        "literal": literal,
    }


def esta_en_plazo(inscripcion: dict[str, Any]) -> bool:
    """True si hay al menos un plazo de inscripción abierto hoy (incluye los
    procesos con varios plazos, p. ej. varias publicaciones BOE agregadas)."""
    if inscripcion.get("codigo") == "ABIERTO":
        return True
    return any((p or {}).get("codigo") == "ABIERTO" for p in inscripcion.get("plazos") or [])


def estado_inscripcion(proceso: dict[str, Any], *, hoy: date | None = None) -> dict[str, Any]:
    """Deriva la situación de inscripción sin mezclarla con el ciclo selectivo."""
    hoy = hoy or _festivos.hoy_es()
    apertura = proceso.get("fecha_apertura")
    cierre = proceso.get("fecha_cierre")

    if apertura and cierre:
        if hoy < apertura:
            return {"codigo": "PENDIENTE_APERTURA", "fecha_apertura": apertura, "fecha_cierre": cierre}
        if hoy <= cierre:
            return {"codigo": "ABIERTO", "fecha_apertura": apertura, "fecha_cierre": cierre}
        return {"codigo": "CERRADO", "fecha_apertura": apertura, "fecha_cierre": cierre}

    datos = proceso.get("datos_json") or {}
    agregados = datos.get("boe_local_agregados")
    if isinstance(agregados, list) and agregados:
        plazos = []
        publicaciones_sin_plazo = []
        for boe in agregados:
            if not isinstance(boe, dict):
                continue
            literal_agregado = str(boe.get("plazo_solicitudes_literal") or "").strip()
            fecha_agregada = _fecha_iso(boe.get("fecha_boe"))
            if not fecha_agregada:
                continue
            if not literal_agregado:
                publicaciones_sin_plazo.append({
                    "codigo_externo": boe.get("codigo_externo"),
                    "boe_id": boe.get("boe_id"),
                    "fecha_boe": fecha_agregada,
                    "denominacion": boe.get("denominacion"),
                    "plazas": boe.get("plazas"),
                })
                continue
            plazo = _estado_plazo_boe(
                fecha_boe=fecha_agregada,
                literal=literal_agregado,
                hoy=hoy,
                organismo=proceso.get("organismo_nombre"),
            )
            plazo.update({
                "codigo_externo": boe.get("codigo_externo"),
                "boe_id": boe.get("boe_id"),
                "fecha_boe": fecha_agregada,
                "denominacion": boe.get("denominacion"),
                "plazas": boe.get("plazas"),
            })
            plazos.append(plazo)

        if plazos:
            codigos = {p["codigo"] for p in plazos}
            codigo = next(iter(codigos)) if len(codigos) == 1 else "PLAZOS_MULTIPLES"
            return {
                "codigo": codigo,
                "plazos_multiples": True,
                "plazos": plazos,
            }
        if publicaciones_sin_plazo:
            return {
                "codigo": "BOE_PUBLICADO_SIN_PLAZO",
                "boe_publicaciones": publicaciones_sin_plazo,
            }

    boe_local = datos.get("boe_local") if isinstance(datos.get("boe_local"), dict) else {}
    literal = str(
        datos.get("plazo_solicitudes_literal")
        or boe_local.get("plazo_solicitudes_literal")
        or ""
    ).strip()
    fecha_boe_publicacion = _fecha_iso(
        proceso.get("fecha_boe_publicacion") or boe_local.get("fecha_boe")
    )
    fecha_boe = fecha_boe_publicacion or proceso.get("fecha_convocatoria")
    if literal and fecha_boe:
        return _estado_plazo_boe(
            fecha_boe=fecha_boe,
            literal=literal,
            hoy=hoy,
            organismo=proceso.get("organismo_nombre"),
        )
    if fecha_boe_publicacion:
        return {
            "codigo": "BOE_PUBLICADO_SIN_PLAZO",
            "fecha_boe": fecha_boe_publicacion,
        }

    origen = str(datos.get("origen") or "").upper()
    # Las oportunidades nacidas de un BOP cuyas bases remiten la apertura del
    # plazo a una publicación posterior en BOE deben permanecer explícitamente
    # pendientes de BOE mientras no exista esa publicación. No es un plazo
    # desconocido: conocemos el hito oficial que falta.
    fuente_principal_tipo = str(proceso.get("fuente_principal_tipo") or "").upper()
    # El catálogo de "otras entidades" de la Diputación de Alicante publica
    # las bases municipales, pero no constituye la publicación de apertura.
    # Si no aporta fechas de presentación ni hay BOE asociado, mantiene el
    # mismo hito pendiente que una base publicada en BOP.
    origenes_pendientes_boe = {
        "BOP_VALENCIA",
        "BOP_VALENCIA_MUNICIPAL",
        "BOP_CASTELLON",
        "BOP_ALICANTE",
        "DIPUTACION_ALICANTE_OTRAS",
    }
    if (origen in origenes_pendientes_boe or fuente_principal_tipo == "BOP") and not boe_local:
        return {"codigo": "PENDIENTE_BOE"}

    return {"codigo": "NO_DETERMINADO"}
