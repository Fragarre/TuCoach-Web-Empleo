from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .boe_local_extractor import extraer_convocatorias_boe_local
from .database import get_connection
from .organismos import resolver_fuente


def _sin(texto: str | None) -> str:
    valor = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in valor if unicodedata.category(c) != "Mn")


def _entidad_visible(entidad: str | None) -> str | None:
    if not entidad:
        return None
    return re.sub(r"\s*\([^)]*\)\s*$", "", entidad).strip() or None


def _nombres_entidad(entidad: str | None) -> set[str]:
    """Variantes oficiales explícitas; no realiza matching aproximado."""
    visible = _entidad_visible(entidad)
    if not visible:
        return set()
    prefijo = ""
    nombre = visible
    m = re.match(r"^(ayuntamiento de\s+)(.+)$", visible, flags=re.I)
    if m:
        prefijo, nombre = m.group(1), m.group(2)
    variantes = {nombre}
    if "/" in nombre:
        variantes.update(x.strip() for x in nombre.split("/") if x.strip())
    return {_sin(f"{prefijo}{x}") for x in variantes}


def _familia(denominacion: str | None) -> str | None:
    n = _sin(denominacion)
    if "auxiliar administr" in n:
        return "AUXILIAR_ADMINISTRATIVO"
    if ("tecnico" in n or "tecnica" in n or "tecnic" in n) and (
        "administracion general" in n or "administracio general" in n
    ):
        return "TAG"
    if any(x in n for x in ("administrativo", "administrativa", "administratiu", "administrativa")):
        return "ADMINISTRATIVO"
    if ("tecnico" in n or "tecnica" in n or "tecnic" in n) and ("gestion" in n or "gestio" in n):
        return "TECNICO_GESTION"
    return None


def _buscar_organismo(
    organismos: list[dict[str, Any]],
    entidad: str | None,
    provincia: str | None,
) -> dict[str, Any] | None:
    visible = _entidad_visible(entidad)
    if not visible or not provincia:
        return None
    objetivos = _nombres_entidad(visible)
    provincia_objetivo = _sin(provincia)
    exactos = [
        o for o in organismos
        if _sin(o.get("nombre")) in objetivos
        and _sin(o.get("provincia")) == provincia_objetivo
    ]
    return exactos[0] if len(exactos) == 1 else None


def _tipo_y_municipio(entidad: str) -> tuple[str, str | None]:
    normalizada = _sin(entidad)
    if normalizada.startswith("ayuntamiento de "):
        return "AYUNTAMIENTO", entidad[len("Ayuntamiento de "):].strip() or None
    if normalizada.startswith("mancomunitat ") or normalizada.startswith("mancomunidad "):
        return "MANCOMUNIDAD", None
    return "ENTIDAD_LOCAL", None


def _candidatos_bop(
    cursor,
    *,
    organismo_id: int,
    fecha_bases: str | None,
    denominacion: str | None,
    plazas: int | None,
) -> list[dict[str, Any]]:
    familia = _familia(denominacion)
    if not fecha_bases or not familia:
        return []
    cursor.execute(
        """
        SELECT id,identificador_estable,codigo_externo,denominacion,plazas,fecha_convocatoria,datos_json
        FROM procesos
        WHERE organismo_id=%s
          AND ambito_administrativo='SI'
          AND fecha_convocatoria=%s
        ORDER BY id
        """,
        (organismo_id, fecha_bases),
    )
    resultado = []
    for proceso in cursor.fetchall():
        if _familia(proceso.get("denominacion")) != familia:
            continue
        if plazas is not None and proceso.get("plazas") is not None and proceso.get("plazas") != plazas:
            continue
        resultado.append(proceso)
    return resultado


def _es_turno_interno(turno: str | None) -> bool:
    return "promocion interna" in _sin(turno)


def _datos_boe(convocatoria: dict[str, Any], codigo: str) -> dict[str, Any]:
    return {
        "origen": "BOE_LOCAL",
        "boe_id": convocatoria.get("boe_id"),
        "codigo_externo": codigo,
        "fecha_boe": convocatoria.get("fecha_boe"),
        "entidad": convocatoria.get("entidad"),
        "denominacion": convocatoria.get("denominacion"),
        "plazas": convocatoria.get("plazas"),
        "bases_bop": convocatoria.get("bases_bop"),
        "plazo_solicitudes_literal": convocatoria.get("plazo_solicitudes_literal"),
        "url_html": convocatoria.get("url_html"),
        "url_xml": convocatoria.get("url_xml"),
        "url_pdf": convocatoria.get("url_pdf"),
        "texto_plaza": convocatoria.get("texto_plaza"),
    }


def _fusionar_boe_agregados(
    existentes: list[dict[str, Any]] | None,
    nuevos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Une publicaciones BOE por identidad documental sin duplicarlas."""
    por_id: dict[str, dict[str, Any]] = {}
    orden: list[str] = []
    for item in [*(existentes or []), *nuevos]:
        if not isinstance(item, dict):
            continue
        clave = str(item.get("boe_id") or item.get("codigo_externo") or "").strip()
        if not clave:
            continue
        if clave not in por_id:
            orden.append(clave)
        por_id[clave] = {**por_id.get(clave, {}), **item}
    return [por_id[clave] for clave in orden]


def _agrupar_convocatorias_por_boe(convocatorias: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa las plazas extraídas de un mismo documento BOE sin perder su total."""
    agrupadas: dict[str, dict[str, Any]] = {}
    orden: list[str] = []
    for convocatoria in convocatorias:
        boe_id = str(convocatoria.get("boe_id") or "").strip()
        if not boe_id:
            continue
        if boe_id not in agrupadas:
            item = dict(convocatoria)
            item["denominaciones"] = [convocatoria.get("denominacion")] if convocatoria.get("denominacion") else []
            agrupadas[boe_id] = item
            orden.append(boe_id)
            continue
        actual = agrupadas[boe_id]
        plazas_actuales = actual.get("plazas")
        plazas_nuevas = convocatoria.get("plazas")
        if isinstance(plazas_actuales, int) and isinstance(plazas_nuevas, int):
            actual["plazas"] = plazas_actuales + plazas_nuevas
        elif plazas_actuales is None:
            actual["plazas"] = plazas_nuevas
        denominacion = convocatoria.get("denominacion")
        if denominacion and denominacion not in actual["denominaciones"]:
            actual["denominaciones"].append(denominacion)
    return [agrupadas[boe_id] for boe_id in orden]


def _insertar_publicacion_boe(cursor, *, fuente_id: int, proceso_id: int, convocatoria: dict[str, Any], codigo: str) -> bool:
    url = convocatoria.get("url_html")
    if not url:
        raise RuntimeError(f"URL BOE no disponible para {codigo}")
    cursor.execute(
        """
        SELECT 1
        FROM publicaciones
        WHERE fuente_id=%s
          AND (
                referencia=%s
                OR datos_json->>'boe_id'=%s
              )
        LIMIT 1
        """,
        (fuente_id, codigo, convocatoria.get("boe_id")),
    )
    if cursor.fetchone() is not None:
        return False
    cursor.execute(
        """
        INSERT INTO publicaciones (
            proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json,detectada_at
        ) VALUES (%s,%s,%s,'BOE',%s,%s,%s,%s,NOW())
        """,
        (
            proceso_id, fuente_id, codigo, convocatoria.get("denominacion"),
            convocatoria.get("fecha_boe"), url,
            Jsonb({
                "origen": "BOE_LOCAL",
                "boe_id": convocatoria.get("boe_id"),
                "codigo_externo": codigo,
                "bases_bop": convocatoria.get("bases_bop"),
            }),
        ),
    )
    return True


def previsualizar_importacion_boe_local(*, hasta: date, dias: int = 30, aplicar: bool = False, boe_ids_absorbidos: set[str] | None = None) -> dict[str, Any]:
    """Previsualiza por defecto; solo inserta o vincula convocatorias inequívocas con aplicar=True."""
    extraccion = extraer_convocatorias_boe_local(hasta=hasta, dias=dias)
    resultado: dict[str, Any] = {
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
        "desde": extraccion["desde"],
        "hasta": extraccion["hasta"],
        "convocatorias_extraidas": extraccion["convocatorias"],
        "dias_con_error": extraccion["dias_con_error"],
        "errores": extraccion["errores"],
        "fuera_alcance_provincia": 0,
        "excluidas_turno_interno": 0,
        "nuevas": 0,
        "existentes_boe": 0,
        "absorbidas_bop_revision": 0,
        "posibles_existentes_bop": 0,
        "vinculadas_bop": 0,
        "revision_solapamiento": 0,
        "insertados": 0,
        "organismos_creados": 0,
        "publicaciones_creadas": 0,
        "detalle": [],
    }

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id,nombre,tipo,provincia,municipio FROM organismos ORDER BY id")
        organismos = list(cursor.fetchall())
        fuente_boe = resolver_fuente(
            cursor,
            nombre="Boletín Oficial del Estado",
            tipo="BOE",
        )
        fuente_boe_id = fuente_boe["id"]

        provincias_objetivo = {"Valencia", "Alicante", "Castellón"}
        for convocatoria in extraccion["detalle"]:
            provincia = convocatoria.get("provincia")
            if provincia not in provincias_objetivo:
                resultado["fuera_alcance_provincia"] += 1
                continue

            codigo = convocatoria["codigo_externo"]
            estable = f"BOELOCAL:{codigo}"

            if not aplicar and convocatoria.get("boe_id") in (boe_ids_absorbidos or set()):
                resultado["absorbidas_bop_revision"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "boe_id": convocatoria.get("boe_id"),
                    "estado_importacion": "ABSORBIDA_BOP_REVISION",
                })
                continue

            if _es_turno_interno(convocatoria.get("turno")):
                resultado["excluidas_turno_interno"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "entidad": convocatoria.get("entidad"),
                    "denominacion": convocatoria.get("denominacion"),
                    "plazas": convocatoria.get("plazas"),
                    "sistema_selectivo": convocatoria.get("sistema_selectivo"),
                    "turno": convocatoria.get("turno"),
                    "fecha_boe": convocatoria.get("fecha_boe"),
                    "estado_importacion": "EXCLUIDA_TURNO_INTERNO",
                })
                continue

            # Una referencia BOE ya absorbida como publicación de un proceso BOP
            # no debe reaparecer como proceso BOE independiente.
            cursor.execute(
                """
                SELECT proceso_id
                FROM publicaciones
                WHERE fuente_id=%s
                  AND (
                        referencia=%s
                        OR datos_json->>'boe_id'=%s
                      )
                LIMIT 1
                """,
                (fuente_boe_id, codigo, convocatoria.get("boe_id")),
            )
            publicacion_existente = cursor.fetchone()
            if publicacion_existente:
                resultado["existentes_boe"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "estado_importacion": "EXISTENTE_PUBLICACION",
                    "proceso_id": publicacion_existente["proceso_id"],
                })
                continue

            cursor.execute("SELECT id,identificador_estable FROM procesos WHERE identificador_estable=%s", (estable,))
            existente = cursor.fetchone()
            if existente:
                resultado["existentes_boe"] += 1
                resultado["detalle"].append({
                    "codigo_externo": codigo,
                    "identificador_estable": estable,
                    "estado_importacion": "EXISTENTE_BOE",
                    "proceso_id": existente["id"],
                })
                continue

            organismo = _buscar_organismo(organismos, convocatoria.get("entidad"), provincia)
            candidatos = []
            if organismo:
                bases = convocatoria.get("bases_bop") or {}
                candidatos = _candidatos_bop(
                    cursor,
                    organismo_id=organismo["id"],
                    fecha_bases=bases.get("fecha"),
                    denominacion=convocatoria.get("denominacion"),
                    plazas=convocatoria.get("plazas"),
                )

            item = {
                "codigo_externo": codigo,
                "identificador_estable": estable,
                "entidad": convocatoria.get("entidad"),
                "organismo_id": organismo["id"] if organismo else None,
                "denominacion": convocatoria.get("denominacion"),
                "plazas": convocatoria.get("plazas"),
                "sistema_selectivo": convocatoria.get("sistema_selectivo"),
                "turno": convocatoria.get("turno"),
                "fecha_boe": convocatoria.get("fecha_boe"),
                "bases_bop": convocatoria.get("bases_bop"),
                "url_html": convocatoria.get("url_html"),
            }

            if len(candidatos) == 1:
                resultado["posibles_existentes_bop"] += 1
                candidato = candidatos[0]
                item["proceso_bop_candidato"] = candidato
                item["estado_importacion"] = "POSIBLE_EXISTENTE_BOP"
                if aplicar:
                    proceso_id = candidato["id"]
                    datos_boe = _datos_boe(convocatoria, codigo)
                    cursor.execute(
                        """
                        UPDATE procesos
                        SET datos_json = COALESCE(datos_json,'{}'::jsonb) || %s,
                            ultima_publicacion_at = GREATEST(
                                COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                                %s::date::timestamptz
                            ),
                            updated_at = NOW()
                        WHERE id=%s
                        """,
                        (
                            Jsonb({"boe_local": datos_boe}),
                            convocatoria.get("fecha_boe"),
                            convocatoria.get("fecha_boe"),
                            proceso_id,
                        ),
                    )
                    if _insertar_publicacion_boe(cursor, fuente_id=fuente_boe_id, proceso_id=proceso_id, convocatoria=convocatoria, codigo=codigo):
                        resultado["publicaciones_creadas"] += 1
                    resultado["vinculadas_bop"] += 1
                    item["proceso_id"] = proceso_id
                    item["estado_importacion"] = "VINCULADA_BOP"
                resultado["detalle"].append(item)
                continue
            if len(candidatos) > 1:
                resultado["revision_solapamiento"] += 1
                item["estado_importacion"] = "REVISION_SOLAPAMIENTO"
                item["procesos_bop_candidatos"] = candidatos
                resultado["detalle"].append(item)
                continue

            resultado["nuevas"] += 1
            item["estado_importacion"] = "NUEVA"

            if aplicar:
                if organismo is None:
                    nombre = _entidad_visible(convocatoria.get("entidad"))
                    if not nombre:
                        raise RuntimeError(f"Entidad no identificada para {codigo}")
                    tipo, municipio = _tipo_y_municipio(nombre)
                    cursor.execute(
                        """
                        INSERT INTO organismos (nombre,tipo,municipio,provincia,activo,created_at,updated_at)
                        VALUES (%s,%s,%s,%s,TRUE,NOW(),NOW())
                        RETURNING id,nombre,tipo,provincia,municipio
                        """,
                        (nombre, tipo, municipio, provincia),
                    )
                    organismo = cursor.fetchone()
                    organismos.append(organismo)
                    resultado["organismos_creados"] += 1
                    item["organismo_id"] = organismo["id"]

                datos_proceso = _datos_boe(convocatoria, codigo)
                cursor.execute(
                    """
                    INSERT INTO procesos (
                        organismo_id,codigo_externo,identificador_estable,denominacion,plazas,
                        sistema_selectivo,turno,estado,fecha_convocatoria,ultima_publicacion_at,fuente_principal_id,
                        es_oportunidad,ambito_administrativo,datos_json,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'EN_CURSO',%s,%s::date::timestamptz,%s,TRUE,'SI',%s,NOW())
                    RETURNING id
                    """,
                    (
                        organismo["id"], codigo, estable, convocatoria.get("denominacion"),
                        convocatoria.get("plazas"), convocatoria.get("sistema_selectivo"),
                        convocatoria.get("turno"), convocatoria.get("fecha_boe"), convocatoria.get("fecha_boe"),
                        fuente_boe_id, Jsonb(datos_proceso),
                    ),
                )
                proceso_id = cursor.fetchone()["id"]
                resultado["insertados"] += 1
                item["proceso_id"] = proceso_id
                item["estado_importacion"] = "INSERTADA"

                if _insertar_publicacion_boe(cursor, fuente_id=fuente_boe_id, proceso_id=proceso_id, convocatoria=convocatoria, codigo=codigo):
                    resultado["publicaciones_creadas"] += 1

            resultado["detalle"].append(item)

        if aplicar:
            connection.commit()
        else:
            connection.rollback()

    return resultado

def recuperar_boe_para_proceso_bop(
    *,
    proceso_id: int,
    fecha_bases: date,
    hasta: date | None = None,
    max_dias: int = 180,
    aplicar: bool = False,
) -> dict[str, Any]:
    """Recupera BOE histórico para un proceso BOP ya creado, sin ampliar el cron ordinario."""
    hasta = hasta or date.today()
    dias = (hasta - fecha_bases).days + 1
    if dias < 1:
        return {"modo": "APLICADO" if aplicar else "SOLO_REVISION", "estado": "FUERA_RANGO"}
    dias = min(dias, max_dias)
    extraccion = extraer_convocatorias_boe_local(hasta=hasta, dias=dias)

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id,p.organismo_id,p.denominacion,p.plazas,p.fecha_convocatoria,p.datos_json,
                   o.nombre AS organismo_nombre,o.provincia
            FROM procesos p JOIN organismos o ON o.id=p.organismo_id
            WHERE p.id=%s
            """,
            (proceso_id,),
        )
        proceso = cursor.fetchone()
        if not proceso:
            return {"modo": "APLICADO" if aplicar else "SOLO_REVISION", "estado": "PROCESO_NO_ENCONTRADO"}

        convocatorias_coincidentes: list[dict[str, Any]] = []
        familia_proceso = _familia(proceso.get("denominacion"))
        for convocatoria in extraccion["detalle"]:
            if convocatoria.get("provincia") != proceso["provincia"]:
                continue
            if proceso["fecha_convocatoria"] and (convocatoria.get("bases_bop") or {}).get("fecha") != proceso["fecha_convocatoria"].isoformat():
                continue
            if _sin(proceso["organismo_nombre"]) not in _nombres_entidad(convocatoria.get("entidad")):
                continue
            # Compartir organismo y boletín de bases no basta: una misma edición
            # del BOP puede contener bases para categorías distintas que después
            # se convocan en BOE separados. Solo absorbemos filas BOE de la misma
            # familia administrativa que el proceso BOP.
            if not familia_proceso or _familia(convocatoria.get("denominacion")) != familia_proceso:
                continue
            convocatorias_coincidentes.append(convocatoria)

        candidatos = _agrupar_convocatorias_por_boe(convocatorias_coincidentes)
        candidatos_detalle = [
            {
                "codigo_externo": candidato.get("codigo_externo"),
                "boe_id": candidato.get("boe_id"),
                "fecha_boe": candidato.get("fecha_boe"),
                "entidad": candidato.get("entidad"),
                "denominacion": candidato.get("denominacion"),
                "plazas": candidato.get("plazas"),
                "bases_bop": candidato.get("bases_bop"),
                "plazo_solicitudes_literal": candidato.get("plazo_solicitudes_literal"),
                "url_html": candidato.get("url_html"),
                "url_xml": candidato.get("url_xml"),
                "url_pdf": candidato.get("url_pdf"),
            }
            for candidato in candidatos
        ]
        # Las plazas sirven para reconocer un BOE único completo, pero no para
        # medir la "cobertura" de varios BOE. Un mismo proceso BOP puede publicar
        # BOE sucesivos con subconjuntos que se solapan (p. ej. turnos o categorías),
        # por lo que sumar documentos distintos inventaría una magnitud sin garantía.
        plazas_candidatas = sum(
            candidato.get("plazas") or 0
            for candidato in candidatos
            if isinstance(candidato.get("plazas"), int)
        )
        cobertura_completa = (
            len(candidatos) == 1
            and isinstance(proceso.get("plazas"), int)
            and proceso["plazas"] > 0
            and candidatos[0].get("plazas") == proceso["plazas"]
        )
        es_agregado = (
            len(candidatos) > 1
            or (
                len(candidatos) == 1
                and isinstance(proceso.get("plazas"), int)
                and isinstance(candidatos[0].get("plazas"), int)
                and candidatos[0]["plazas"] != proceso["plazas"]
            )
        )

        if not candidatos:
            connection.rollback()
            return {
                "modo": "APLICADO" if aplicar else "SOLO_REVISION",
                "estado": "SIN_COINCIDENCIA",
                "candidatos": 0,
                "candidatos_detalle": [],
                "boe_local_agregados_propuestos": [],
                "plazas_proceso": proceso.get("plazas"),
                "plazas_candidatas": 0,
                "cobertura_completa": False,
                "dias_revisados": dias,
                "errores": extraccion["errores"],
            }

        if es_agregado:
            existentes = (proceso.get("datos_json") or {}).get("boe_local_agregados") or []
            fusionados = _fusionar_boe_agregados(existentes, candidatos_detalle)
            if not aplicar:
                connection.rollback()
                return {
                    "modo": "SOLO_REVISION",
                    "estado": "AGREGADO_PARCIAL" if not cobertura_completa else "AGREGADO_COMPLETO",
                    "candidatos": len(candidatos),
                    "candidatos_detalle": candidatos_detalle,
                    "boe_local_agregados_propuestos": fusionados,
                    "plazas_proceso": proceso.get("plazas"),
                    "plazas_candidatas": plazas_candidatas,
                    "cobertura_completa": cobertura_completa,
                    "dias_revisados": dias,
                    "errores": extraccion["errores"],
                }

            fuente_boe = resolver_fuente(cursor, nombre="Boletín Oficial del Estado", tipo="BOE")
            cursor.execute(
                """
                UPDATE procesos
                SET datos_json = COALESCE(datos_json,'{}'::jsonb) || %s,
                    ultima_publicacion_at = GREATEST(
                        COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                        %s::date::timestamptz
                    ),
                    updated_at=NOW()
                WHERE id=%s
                """,
                (
                    Jsonb({"boe_local_agregados": fusionados}),
                    max(c.get("fecha_boe") for c in candidatos if c.get("fecha_boe")),
                    max(c.get("fecha_boe") for c in candidatos if c.get("fecha_boe")),
                    proceso_id,
                ),
            )
            creadas = 0
            for candidato in candidatos:
                if _insertar_publicacion_boe(
                    cursor,
                    fuente_id=fuente_boe["id"],
                    proceso_id=proceso_id,
                    convocatoria=candidato,
                    codigo=candidato["codigo_externo"],
                ):
                    creadas += 1
            connection.commit()
            return {
                "modo": "APLICADO",
                "estado": "AGREGADO_ACTUALIZADO" if creadas else "AGREGADO_SIN_CAMBIOS",
                "candidatos": len(candidatos),
                "boe_local_agregados": fusionados,
                "plazas_proceso": proceso.get("plazas"),
                "plazas_candidatas": plazas_candidatas,
                "cobertura_completa": cobertura_completa,
                "publicaciones_creadas": creadas,
                "dias_revisados": dias,
                "errores": extraccion["errores"],
            }

        convocatoria = candidatos[0]
        codigo = convocatoria["codigo_externo"]
        if not aplicar:
            connection.rollback()
            return {
                "modo": "SOLO_REVISION",
                "estado": "COINCIDENCIA_UNICA",
                "codigo_externo": codigo,
                "boe_id": convocatoria.get("boe_id"),
                "fecha_boe": convocatoria.get("fecha_boe"),
                "plazo_solicitudes_literal": convocatoria.get("plazo_solicitudes_literal"),
            }

        fuente_boe = resolver_fuente(cursor, nombre="Boletín Oficial del Estado", tipo="BOE")
        datos_boe = _datos_boe(convocatoria, codigo)
        cursor.execute(
            """
            UPDATE procesos
            SET datos_json = COALESCE(datos_json,'{}'::jsonb) || %s,
                ultima_publicacion_at = GREATEST(
                    COALESCE(ultima_publicacion_at, %s::date::timestamptz),
                    %s::date::timestamptz
                ),
                updated_at=NOW()
            WHERE id=%s
            """,
            (Jsonb({"boe_local": datos_boe}), convocatoria.get("fecha_boe"), convocatoria.get("fecha_boe"), proceso_id),
        )
        creada = _insertar_publicacion_boe(
            cursor, fuente_id=fuente_boe["id"], proceso_id=proceso_id,
            convocatoria=convocatoria, codigo=codigo,
        )
        connection.commit()
        return {
            "modo": "APLICADO",
            "estado": "VINCULADA",
            "codigo_externo": codigo,
            "boe_id": convocatoria.get("boe_id"),
            "fecha_boe": convocatoria.get("fecha_boe"),
            "publicacion_creada": creada,
        }
