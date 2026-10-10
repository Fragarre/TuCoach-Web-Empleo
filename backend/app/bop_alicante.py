from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from xml.sax.saxutils import escape

import httpx

from .festivos import hoy_es
from .ambito_administrativo import clasificar_ambito_administrativo
from .database import get_connection
from .bop_valencia import _grupo_subgrupo, _obtener_texto
from .estado_proceso import clasificar_evento_terminal
from .organismos import resolver_fuente, resolver_organismo
from .bop_valencia_municipios import (
    _clasificar_anuncio,
    _es_seguimiento_selectivo_claro,
    _extraer_codigo_proceso,
    _familia_perfil,
    _sin as _sin_valencia,
)

ENDPOINT = (
    "https://sede.diputacionalicante.es/wp-content/themes/"
    "Desarrollo-Diputacion/webservices/wseConsultaAjax.php"
)


def _valor(registro: dict[str, Any], campo: str) -> str:
    valor = registro.get(campo)
    if isinstance(valor, list):
        return str(valor[0]).strip() if valor else ""
    return str(valor or "").strip()


def _param(desde: date, hasta: date) -> str:
    # Contrato real observado en DevTools: param es XML.
    return (
        "<raiz><entrada><registro>"
        f"<desde>{escape(desde.strftime('%d/%m/%Y'))}</desde>"
        f"<hasta>{escape(hasta.strftime('%d/%m/%Y'))}</hasta>"
        "<texto></texto>"
        "<tipoorganismo>4</tipoorganismo>"
        "<publicante></publicante>"
        "</registro></entrada></raiz>"
    )


def _normalizar(registro: dict[str, Any]) -> dict[str, Any]:
    extracto = _valor(registro, "extracto")
    definicion = _valor(registro, "definicion")
    denominacion = _valor(registro, "denominacion")
    publicante = _valor(registro, "ampliacion") or definicion
    edicto = _valor(registro, "edicto")
    anyo = _valor(registro, "anyo")
    numero_bop = _valor(registro, "nBop")
    ubicacion = _valor(registro, "ubicacion")

    texto_clasificacion = " ".join(x for x in (extracto, definicion, denominacion, publicante) if x)
    return {
        "referencia": f"BOPAL:{anyo}:{edicto}" if anyo and edicto else "",
        "anyo": anyo,
        "numero_bop": numero_bop,
        "fecha_publicacion": _valor(registro, "fechaPublica"),
        "edicto": edicto,
        "extracto": extracto,
        "organismo": publicante,
        "denominacion": denominacion,
        "seccion": _valor(registro, "desecun"),
        "url_documento": ubicacion,
        "ambito_administrativo": clasificar_ambito_administrativo(
            {"denominacion": texto_clasificacion, "cuerpo_escala": None, "grupo": None}
        ),
    }



def _grupo_subgrupo_documento(url: str | None) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    try:
        with httpx.Client(timeout=45, follow_redirects=True) as client:
            return _grupo_subgrupo(_obtener_texto(client, url))
    except Exception:
        return None, None


def _sin(texto: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(ch) != "Mn"
    )


def _es_candidato_empleo(registro: dict[str, Any]) -> bool:
    return registro.get("ambito_administrativo") == "SI"

def _tipo_organismo_alicante(texto: str | None) -> str:
    normal = _sin(texto or "")
    if "diputacion provincial de alicante" in normal or "diputacion de alicante" in normal:
        return "DIPUTACION"
    if "ayuntamiento" in normal or "ajuntament" in normal:
        return "AYUNTAMIENTO"
    return "OTRO"


def _filtrar_tipo_organismo(
    revision: dict[str, Any],
    tipo_organismo: str | None,
) -> dict[str, Any]:
    if tipo_organismo is None:
        return revision
    tipo = tipo_organismo.upper()
    if tipo not in {"DIPUTACION", "AYUNTAMIENTO"}:
        raise ValueError("tipo_organismo debe ser DIPUTACION o AYUNTAMIENTO")
    filtrada = dict(revision)
    filtrada["detalle"] = [
        item for item in revision.get("detalle", [])
        if _tipo_organismo_alicante(item.get("organismo")) == tipo
    ]
    filtrada["tipo_organismo"] = tipo
    return filtrada



def seleccionar_proceso_seguimiento(
    hallazgo: dict[str, Any],
    candidatos: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Matching conservador puro, equivalente al patrón BOP Valencia.

    No accede a BD: recibe candidatos ya obtenidos por la capa de persistencia.
    """
    familia = _familia_perfil(hallazgo.get("extracto") or "")
    if not familia:
        return None, "SIN_FAMILIA"
    if not _es_seguimiento_selectivo_claro(hallazgo.get("extracto") or ""):
        return None, "NO_ES_CONTINUIDAD_SELECTIVA"

    municipio = _sin_valencia(hallazgo.get("denominacion") or "")
    codigo = _extraer_codigo_proceso(hallazgo.get("extracto") or "")
    compatibles = [
        p for p in candidatos
        if _sin_valencia(p.get("municipio") or "") == municipio
        and _familia_perfil(p.get("denominacion") or "") == familia
    ]
    if not compatibles:
        return None, "SIN_COINCIDENCIA"

    if codigo:
        por_codigo = [
            p for p in compatibles
            if _extraer_codigo_proceso(p.get("denominacion") or "") == codigo
        ]
        if len(por_codigo) == 1:
            return por_codigo[0], "CODIGO_EXACTO"
        if len(por_codigo) > 1:
            return None, "CODIGO_AMBIGUO"
        return None, "CODIGO_SIN_COINCIDENCIA"

    if len(compatibles) == 1:
        return compatibles[0], "UNICO_HITO_SELECTIVO"
    return None, "AMBIGUO_SIN_CODIGO"


def consultar_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
) -> dict[str, Any]:
    """SOLO_REVISION del BOP Alicante: consulta, normaliza y clasifica; no usa BD."""
    hasta = hasta or hoy_es()
    desde = hasta - timedelta(days=max(dias_solape, 0))

    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION",
        "fuente": "Boletín Oficial de la Provincia de Alicante",
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "descubiertos": 0,
        "administrativos": 0,
        "revision": 0,
        "resumen_clases": {},
        "candidatas_nuevas": 0,
        "seguimientos": 0,
        "errores": [],
        "detalle": [],
    }

    try:
        with httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "TuCoach-Empleo/1.0", "Accept": "application/json"},
        ) as client:
            respuesta = client.get(ENDPOINT, params={"nemo": "BOP_EDI", "param": _param(desde, hasta), "usuario": "-"})
            respuesta.raise_for_status()
            payload = respuesta.json()
    except Exception as exc:
        resultado["errores"].append(f"{type(exc).__name__}: {exc}")
        return resultado

    registros = payload.get("bop", {}).get("registro", [])
    if not isinstance(registros, list):
        resultado["errores"].append("Respuesta BOP Alicante sin bop.registro[]")
        return resultado

    normalizados = [_normalizar(r) for r in registros[:max_items] if isinstance(r, dict)]
    administrativos = [r for r in normalizados if _es_candidato_empleo(r)]
    for r in administrativos:
        r["clase"] = _clasificar_anuncio(r["extracto"])

    conteo = Counter(r["clase"] for r in administrativos)
    resultado["descubiertos"] = len(normalizados)
    resultado["administrativos"] = len(administrativos)
    resultado["revision"] = sum(1 for r in administrativos if r["clase"] == "REVISION")
    resultado["resumen_clases"] = dict(sorted(conteo.items()))
    resultado["candidatas_nuevas"] = (
        conteo.get("NUEVA_CONVOCATORIA", 0)
        + conteo.get("ANUNCIO_DIFICIL_COBERTURA", 0)
    )
    resultado["seguimientos"] = conteo.get("SEGUIMIENTO", 0)
    resultado["detalle"] = administrativos
    return resultado


def _fecha_bop(valor: str | None) -> date | None:
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return date.fromisoformat(valor) if formato == "%Y-%m-%d" else __import__("datetime").datetime.strptime(valor, formato).date()
        except ValueError:
            pass
    return None


def preparar_importacion_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
    tipo_organismo: str | None = None,
) -> dict[str, Any]:
    """Prepara la persistencia usando solo lecturas de BD; nunca escribe."""
    revision = consultar_bop_alicante(
        dias_solape=dias_solape,
        hasta=hasta,
        max_items=max_items,
    )
    revision = _filtrar_tipo_organismo(revision, tipo_organismo)
    resultado: dict[str, Any] = {
        "modo": "SOLO_REVISION_BD",
        "fuente": revision["fuente"],
        "desde": revision["desde"],
        "hasta": revision["hasta"],
        "nuevas": 0,
        "existentes": 0,
        "seguimientos_vinculados": 0,
        "seguimientos_revision": 0,
        "excluidos": 0,
        "errores": list(revision["errores"]),
        "detalle": [],
    }
    if resultado["errores"]:
        return resultado

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        for hallazgo in revision["detalle"]:
            clase = hallazgo["clase"]
            if clase not in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA", "SEGUIMIENTO"):
                resultado["excluidos"] += 1
                continue

            if clase in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA"):
                cursor.execute(
                    "SELECT id FROM procesos WHERE identificador_estable=%s",
                    (hallazgo["referencia"],),
                )
                existente = cursor.fetchone()
                if existente:
                    resultado["existentes"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "EXISTENTE",
                        "proceso_id": existente["id"],
                    })
                else:
                    resultado["nuevas"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "NUEVO",
                        "municipio": hallazgo["denominacion"],
                    })
                continue

            fecha_publicacion = _fecha_bop(hallazgo.get("fecha_publicacion"))
            if fecha_publicacion is None:
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "vinculacion": "FECHA_INVALIDA",
                    "proceso_id": None,
                })
                continue

            cursor.execute(
                """
                SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,p.tipo_proceso,o.municipio
                FROM procesos p
                JOIN organismos o ON o.id=p.organismo_id
                WHERE o.tipo='AYUNTAMIENTO'
                  AND LOWER(COALESCE(o.provincia,''))='alicante'
                  AND p.ambito_administrativo='SI'
                  AND p.estado='EN_CURSO'
                  AND p.fecha_convocatoria IS NOT NULL
                  AND p.fecha_convocatoria <= %s
                ORDER BY p.fecha_convocatoria DESC,p.id DESC
                """,
                (fecha_publicacion,),
            )
            proceso, motivo = seleccionar_proceso_seguimiento(hallazgo, list(cursor.fetchall()))
            if proceso:
                resultado["seguimientos_vinculados"] += 1
            else:
                resultado["seguimientos_revision"] += 1
            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "vinculacion": motivo,
                "proceso_id": proceso["id"] if proceso else None,
            })

        connection.rollback()
    return resultado


def importar_bop_alicante(
    *,
    dias_solape: int = 7,
    hasta: date | None = None,
    max_items: int = 500,
    aplicar: bool = False,
    tipo_organismo: str | None = None,
) -> dict[str, Any]:
    """Importación idempotente del BOP Alicante; por defecto solo revisión."""
    if not aplicar:
        return preparar_importacion_bop_alicante(
            dias_solape=dias_solape,
            hasta=hasta,
            max_items=max_items,
            tipo_organismo=tipo_organismo,
        )

    revision = consultar_bop_alicante(
        dias_solape=dias_solape,
        hasta=hasta,
        max_items=max_items,
    )
    revision = _filtrar_tipo_organismo(revision, tipo_organismo)
    if revision["errores"]:
        return {
            "modo": "APLICAR",
            "errores": revision["errores"],
            "nuevas": 0,
            "existentes": 0,
            "publicaciones": 0,
            "seguimientos_vinculados": 0,
            "seguimientos_revision": 0,
            "organismos_creados": 0,
            "detalle": [],
        }

    resultado: dict[str, Any] = {
        "modo": "APLICAR",
        "nuevas": 0,
        "existentes": 0,
        "publicaciones": 0,
        "seguimientos_vinculados": 0,
        "seguimientos_revision": 0,
        "organismos_creados": 0,
        "errores": [],
        "detalle": [],
    }

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        # Debe existir previamente como identidad funcional autorizada.
        # No se crea aquí ninguna fuente productiva de forma implícita.
        fuente = resolver_fuente(
            cursor,
            nombre="Boletín Oficial de la Provincia de Alicante",
            tipo="BOP",
        )
        fuente_id = fuente["id"]

        for hallazgo in revision["detalle"]:
            clase = hallazgo["clase"]
            if clase not in ("NUEVA_CONVOCATORIA", "ANUNCIO_DIFICIL_COBERTURA", "SEGUIMIENTO"):
                continue

            fecha_publicacion = _fecha_bop(hallazgo.get("fecha_publicacion"))
            if fecha_publicacion is None:
                resultado["seguimientos_revision"] += 1
                resultado["detalle"].append({
                    "referencia": hallazgo["referencia"],
                    "clase": clase,
                    "estado": "REVISION",
                    "motivo": "FECHA_INVALIDA",
                })
                continue

            if clase == "SEGUIMIENTO":
                cursor.execute(
                    """
                    SELECT p.id,p.denominacion,p.codigo_externo,p.fecha_convocatoria,p.tipo_proceso,o.municipio
                    FROM procesos p
                    JOIN organismos o ON o.id=p.organismo_id
                    WHERE o.tipo='AYUNTAMIENTO'
                      AND LOWER(COALESCE(o.provincia,''))='alicante'
                      AND p.ambito_administrativo='SI'
                      AND p.estado='EN_CURSO'
                      AND p.fecha_convocatoria IS NOT NULL
                      AND p.fecha_convocatoria <= %s
                    ORDER BY p.fecha_convocatoria DESC,p.id DESC
                    """,
                    (fecha_publicacion,),
                )
                proceso, motivo = seleccionar_proceso_seguimiento(hallazgo, list(cursor.fetchall()))
                if not proceso:
                    resultado["seguimientos_revision"] += 1
                    resultado["detalle"].append({
                        "referencia": hallazgo["referencia"],
                        "clase": clase,
                        "estado": "REVISION",
                        "motivo": motivo,
                    })
                    continue
                proceso_id = proceso["id"]
                resultado["seguimientos_vinculados"] += 1
                estado_terminal = clasificar_evento_terminal(
                    proceso.get("tipo_proceso"),
                    hallazgo.get("extracto") or hallazgo.get("denominacion"),
                )
                if estado_terminal:
                    cursor.execute(
                        "UPDATE procesos SET estado=%s,updated_at=NOW() WHERE id=%s",
                        (estado_terminal, proceso_id),
                    )
            else:
                grupo, subgrupo = _grupo_subgrupo_documento(hallazgo.get("url_documento"))
                cursor.execute(
                    "SELECT id FROM procesos WHERE identificador_estable=%s",
                    (hallazgo["referencia"],),
                )
                existente = cursor.fetchone()
                if existente:
                    proceso_id = existente["id"]
                    resultado["existentes"] += 1
                    if grupo or subgrupo:
                        cursor.execute(
                            "UPDATE procesos SET grupo=COALESCE(%s,grupo),subgrupo=COALESCE(%s,subgrupo),updated_at=NOW() WHERE id=%s",
                            (grupo, subgrupo, proceso_id),
                        )
                else:
                    municipio = hallazgo["denominacion"]
                    org = resolver_organismo(
                        cursor,
                        tipo="AYUNTAMIENTO",
                        provincia="Alicante",
                        municipio=municipio,
                    )
                    if org is None:
                        nombre = f"Ayuntamiento de {municipio}"
                        cursor.execute(
                            """
                            INSERT INTO organismos
                                (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                            VALUES (%s,'AYUNTAMIENTO',%s,'Alicante',TRUE,NOW(),NOW())
                            RETURNING id
                            """,
                            (nombre, municipio),
                        )
                        organismo_id = cursor.fetchone()["id"]
                        resultado["organismos_creados"] += 1
                    else:
                        organismo_id = org["id"]

                    cursor.execute(
                        """
                        INSERT INTO procesos
                            (organismo_id,codigo_externo,identificador_estable,denominacion,tipo_proceso,grupo,subgrupo,
                             estado,fecha_convocatoria,fuente_principal_id,es_oportunidad,
                             ambito_administrativo,datos_json,updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,'EN_CURSO',%s,%s,TRUE,'SI',%s,NOW())
                        RETURNING id
                        """,
                        (
                            organismo_id,
                            hallazgo["edicto"],
                            hallazgo["referencia"],
                            hallazgo["extracto"],
                            (
                                "Anuncio difícil cobertura (ADC)"
                                if clase == "ANUNCIO_DIFICIL_COBERTURA"
                                else None
                            ),
                            grupo,
                            subgrupo,
                            fecha_publicacion,
                            fuente_id,
                            Jsonb({
                                "url_oficial": hallazgo["url_documento"],
                                "bop_referencia": hallazgo["referencia"],
                                "origen": "BOP_ALICANTE",
                            }),
                        ),
                    )
                    proceso_id = cursor.fetchone()["id"]
                    resultado["nuevas"] += 1

            cursor.execute(
                "SELECT id FROM publicaciones WHERE fuente_id=%s AND referencia=%s LIMIT 1",
                (fuente_id, hallazgo["referencia"]),
            )
            publicacion = cursor.fetchone()
            if publicacion is None:
                cursor.execute(
                    """
                    INSERT INTO publicaciones
                        (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,
                         url,datos_json,detectada_at)
                    VALUES (%s,%s,%s,'BOP',%s,%s,%s,%s,NOW())
                    RETURNING id
                    """,
                    (
                        proceso_id,
                        fuente_id,
                        hallazgo["referencia"],
                        hallazgo["extracto"],
                        fecha_publicacion,
                        hallazgo["url_documento"],
                        Jsonb({"origen": "BOP_ALICANTE", "clase": clase}),
                    ),
                )
                cursor.fetchone()
                resultado["publicaciones"] += 1

            resultado["detalle"].append({
                "referencia": hallazgo["referencia"],
                "clase": clase,
                "estado": "VINCULADO" if clase == "SEGUIMIENTO" else "IMPORTADO",
                "proceso_id": proceso_id,
            })

        connection.commit()
    return resultado
