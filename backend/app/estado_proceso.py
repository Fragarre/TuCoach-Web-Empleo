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


def _dias_literal(literal: str, clase: str) -> int | None:
    normalizado = _sin(literal)
    m = re.search(rf"\b(\d+)\s+dias?\s+{clase}\b", normalizado, re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"\b([a-z]+)\s+dias?\s+{clase}\b", normalizado, re.I)
    if m:
        return _NUMEROS_PLAZO.get(m.group(1))
    return None


def _dias_habiles_literal(literal: str) -> int | None:
    return _dias_literal(literal, "habiles")


def _dias_naturales_literal(literal: str) -> int | None:
    return _dias_literal(literal, "naturales")


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
    dias_naturales = _dias_naturales_literal(literal)
    if dias_naturales:
        apertura = fecha_boe + timedelta(days=1)
        cierre = fecha_boe + timedelta(days=dias_naturales)
        codigo = "PENDIENTE_APERTURA" if hoy < apertura else ("ABIERTO" if hoy <= cierre else "CERRADO")
        return {
            "codigo": codigo,
            "fecha_apertura": apertura,
            "fecha_cierre": cierre,
            "fecha_cierre_calculada": True,
            "dias_naturales": dias_naturales,
            "literal": literal,
        }

    dias = _dias_habiles_literal(literal)
    if dias:
        # Sin el calendario oficial completo aplicable al organismo no se
        # persiste ni se muestra una fecha exacta de cierre. Un festivo local
        # puede alterar el cómputo y producir un estado ABIERTO/CERRADO falso.
        return {
            "codigo": "PLAZO_LITERAL",
            "fecha_referencia": fecha_boe + timedelta(days=1),
            "dias_habiles": dias,
            "literal": literal,
        }

    return {
        "codigo": "PLAZO_LITERAL",
        "fecha_referencia": fecha_boe + timedelta(days=1),
        "dias_habiles": None,
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
        for boe in agregados:
            if not isinstance(boe, dict):
                continue
            literal_agregado = str(boe.get("plazo_solicitudes_literal") or "").strip()
            fecha_agregada = _fecha_iso(boe.get("fecha_boe"))
            if not literal_agregado or not fecha_agregada:
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

    boe_local = datos.get("boe_local") if isinstance(datos.get("boe_local"), dict) else {}
    literal = str(
        datos.get("plazo_solicitudes_literal")
        or boe_local.get("plazo_solicitudes_literal")
        or ""
    ).strip()
    fecha_boe = proceso.get("fecha_boe_publicacion") or proceso.get("fecha_convocatoria")
    if literal and fecha_boe:
        return _estado_plazo_boe(
            fecha_boe=fecha_boe,
            literal=literal,
            hoy=hoy,
            organismo=proceso.get("organismo_nombre"),
        )

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
