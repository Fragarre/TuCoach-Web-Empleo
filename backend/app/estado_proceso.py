from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any


def _sin(texto: str | None) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


_TERMINALES_COMUNES = (
    "finalizacion del proceso",
    "finalizacion del proceso selectivo",
    "finalitzacio del proces",
    "finalitzacio del proces selectiu",
    "desistimiento",
    "desistiment",
    "anulacion",
    "anul·lacio",
    "anullacio",
    "nombramiento como funcionario",
    "nombramiento como funcionaria",
    "nombramiento de funcionario",
    "nomenament com a funcionari",
    "nomenament com a funcionaria",
    "nomenament de funcionari",
    "toma de posesion",
    "presa de possessio",
    "adjudicacion definitiva",
    "adjudicacio definitiva",
    "adjudicacion de destinos",
    "adjudicacio de destinacions",
)

_TERMINALES_PROVISION = (
    "nombramiento mediante concurso",
    "nomenament mitjancant concurs",
)

_NUMEROS_PLAZO = {
    "cinco": 5,
    "diez": 10,
    "quince": 15,
    "veinte": 20,
    "treinta": 30,
}

# Calendario administrativo de la Comunitat Valenciana. Se mantiene por año
# para no calcular fechas exactas con un calendario que no haya sido verificado.
_FESTIVOS_CV: dict[int, set[date]] = {
    2026: {
        date(2026, 1, 1), date(2026, 1, 6), date(2026, 3, 19),
        date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1),
        date(2026, 6, 24), date(2026, 8, 15), date(2026, 10, 9),
        date(2026, 10, 12), date(2026, 12, 8), date(2026, 12, 25),
    }
}


def es_bolsa(tipo_proceso: str | None) -> bool:
    return "bolsa" in _sin(tipo_proceso) or "borsa" in _sin(tipo_proceso)


def clasificar_evento_terminal(tipo_proceso: str | None, titulo: str | None) -> str | None:
    """Clasifica solo evidencias oficiales inequívocamente terminales."""
    n = _sin(titulo)
    if not n:
        return None
    if "desistimiento" in n or "desistiment" in n:
        return "DESISTIDO"
    if any(x in n for x in ("anulacion", "anullacio", "anul·lacio")):
        return "ANULADO"
    if any(_sin(x) in n for x in _TERMINALES_COMUNES + _TERMINALES_PROVISION):
        return "FINALIZADO"
    return None


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
    """Calcula el último día con fines de semana y festivos estatales/autonómicos CV.

    No incorpora festivos locales; el resultado debe mostrarse con advertencia
    para que el usuario confirme posibles días inhábiles del municipio.
    """
    if dias <= 0 or fecha_boe.year not in _FESTIVOS_CV:
        return None
    festivos = _FESTIVOS_CV[fecha_boe.year]
    actual = fecha_boe
    contados = 0
    while contados < dias:
        actual += timedelta(days=1)
        if actual.weekday() >= 5 or actual in festivos:
            continue
        contados += 1
    return actual


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
        "dias_habiles": dias,
        "literal": literal,
    }


def estado_inscripcion(proceso: dict[str, Any], *, hoy: date | None = None) -> dict[str, Any]:
    """Deriva la situación de inscripción sin mezclarla con el ciclo selectivo."""
    hoy = hoy or date.today()
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
