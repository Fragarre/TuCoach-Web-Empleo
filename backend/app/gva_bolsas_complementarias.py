from __future__ import annotations

"""Descubrimiento complementario, de solo lectura, de bolsas administrativas GVA.

Este módulo NO persiste procesos, publicaciones ni cambios y NO genera
notificaciones. Su función es producir un plan auditable antes de habilitar
cualquier escritura.
"""

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import gva_clean
from .database import get_connection
from .gva_estatal_service import _get_gva_con_reintentos
from .gva_estatal_source import nuevo_cliente
from .organismos import resolver_fuente, resolver_organismo


CODIGOS_ADMIN_ESTRICTOS = ("A1-01", "A2-01", "C1-01", "C2-01")
EXCLUSIONES = (
    "promocion interna",
    "dificil cobertura",
    "acto unico",
    "consulta de baremo",
    "consulta de estados",
    "cesion de integrantes",
)


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto or "")
    return "".join(
        c for c in normalizado if unicodedata.category(c) != "Mn"
    ).lower()


def _id_emp(href: str) -> int | None:
    valores = parse_qs(urlparse(href).query).get("id_emp")
    if not valores:
        return None
    try:
        return int(valores[0])
    except (TypeError, ValueError):
        return None


def _es_tarjeta_bolsa(enlace) -> bool:
    tarjeta = enlace.find_parent(
        class_=lambda valor: valor and "card" in str(valor).split()
    )
    texto = " ".join((tarjeta or enlace).get_text(" ", strip=True).split())
    return bool(
        re.search(
            r"proceso selectivo:\s*bolsa de trabajo\b",
            _sin_acentos(texto),
        )
    )


def _descubrir_por_codigo(client, codigo: str) -> dict[int, str]:
    respuesta = _get_gva_con_reintentos(
        client,
        gva_clean.GVA_SEARCH_URL,
        params={
            "convocatoria": "1",
            "descripcion": codigo,
            "pagina": "0",
            "tamanyoPagina": "100",
            "elementosPaginacion": "100",
        },
    )
    soup = BeautifulSoup(respuesta.text, "html.parser")
    encontrados: dict[int, str] = {}
    for enlace in soup.select('a[href*="detall-ocupacio-publica"]'):
        href = str(enlace.get("href") or "")
        identificador = _id_emp(href)
        if identificador is None or not _es_tarjeta_bolsa(enlace):
            continue
        encontrados[identificador] = urljoin(gva_clean.GVA_BASE_URL, href)
    return encontrados


def _campo_publico(texto: str, etiqueta: str, siguientes: tuple[str, ...]) -> str | None:
    limites = "|".join(re.escape(x) for x in siguientes)
    patron = rf"{re.escape(etiqueta)}\s*:?\s*(.+?)(?=\s+(?:{limites})\s*:|$)"
    m = re.search(patron, texto, re.I)
    return " ".join(m.group(1).split()) if m else None


def _clasificar_detalle(id_emp: int, url: str, html: str) -> dict[str, Any]:
    proceso = gva_clean.parsear_detalle(url, html, id_emp)
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    normalizado = _sin_acentos(texto)
    # La ficha antigua puede no mostrar "Plazo" tras Fase; acotamos también
    # por los siguientes bloques estructurales para no absorber todo el historial.
    m_fase = re.search(
        r"\bFase\s*:?\s*(?P<fase>.+?)(?=\s+(?:Plazo|Publicaci[oó]n Web|Forma de presentaci[oó]n|Enlaces|Informaci[oó]n complementaria|Listado de etapas|Etapa actual)\b|$)",
        texto,
        re.I,
    )
    fase = " ".join(m_fase.group("fase").split()) if m_fase else None
    etapa_actual = None
    if m_fase:
        prefijo = texto[:m_fase.start()]
        etapas = list(re.finditer(r"Etapa actual\s*:\s*", prefijo, re.I))
        if etapas:
            etapa_actual = " ".join(prefijo[etapas[-1].end():].split()) or None
    codigos = [
        codigo
        for codigo in CODIGOS_ADMIN_ESTRICTOS
        if codigo.lower() in normalizado
    ]
    exclusion = next(
        (x for x in EXCLUSIONES if _sin_acentos(x) in normalizado),
        None,
    )
    es_bolsa = proceso.get("tipo_proceso") == "Bolsa de trabajo"
    denominacion_norm = _sin_acentos(str(proceso.get("denominacion") or ""))
    es_cesion = "cesion de datos" in denominacion_norm
    es_adc = (
        "anuncio dificil cobertura" in normalizado
        or "dificil cobertura" in denominacion_norm
    )
    if es_adc:
        categoria_gva = "ADC"
    elif es_cesion:
        categoria_gva = "CESION_DATOS"
    elif es_bolsa:
        categoria_gva = "BOLSA"
    else:
        categoria_gva = "OTRO"

    # El código por sí solo no basta: la búsqueda GVA también devuelve
    # especialidades/APT y bolsas sectoriales que contienen A1-01/A2-01/C1-01/C2-01.
    # Este complemento se limita a los cuerpos administrativos generales.
    especialidad = next(
        (
            motivo
            for patron, motivo in (
                (r"\bAPT[- ]", "especialidad_apt"),
                (r"\bC1-07\b", "especialidad_c1_07"),
                (r"\bC2-01-02\b", "especialidad_c2_01_02"),
                (r"\bC2-01-EDU\b|\bC1-01-EDU\b", "sector_educacion"),
                (r"protocolo", "especialidad_protocolo"),
                (r"orientador(?:a)? laboral", "especialidad_orientacion_laboral"),
                (r"comunicacion y relaciones informativas", "especialidad_comunicacion"),
                (r"fondos europeos", "especialidad_fondos_europeos"),
                (r"agentes tributarios", "especialidad_agentes_tributarios"),
                (r"asistencia al contribuyente", "especialidad_tributaria"),
                (r"discapacidad intelectual", "turno_discapacidad_intelectual"),
            )
            if re.search(patron, denominacion_norm, re.I)
        ),
        None,
    )

    proceso["cuerpo_escala"] = codigos[0] if len(codigos) == 1 else None
    proceso["ambito_administrativo"] = "SI" if codigos and especialidad is None else "NO"
    proceso["es_oportunidad"] = bool(
        codigos
        and categoria_gva == "BOLSA"
        and exclusion is None
        and especialidad is None
    )
    if exclusion is None and especialidad is not None:
        exclusion = especialidad
    proceso["motivo_exclusion"] = exclusion
    proceso["datos_json"] = {
        **(proceso.get("datos_json") or {}),
        "fuente_descubrimiento": "sede.gva.es",
        "codigos_administrativos": codigos,
        "etapa_actual_gva": etapa_actual,
        "fase_gva": fase,
        "categoria_gva": categoria_gva,
        "especialidad_excluida": especialidad,
    }
    return proceso


def _bolsa_directa_actualizable(existente: dict[str, Any] | None, id_emp: int) -> bool:
    """Limita el refresco complementario a las bolsas creadas por este módulo."""
    if not existente:
        return False
    if str(existente.get("identificador_estable") or "") != f"GVA:{id_emp}":
        return False
    datos = existente.get("datos_json") or {}
    return (
        str(datos.get("fuente_descubrimiento") or "").strip().lower() == "sede.gva.es"
        and str(datos.get("categoria_gva") or "").strip().upper() == "BOLSA"
    )

def _bolsa_directa_cambia(existente: dict[str, Any], proceso: dict[str, Any]) -> bool:
    """Compara solo los campos que este módulo está autorizado a refrescar."""
    datos_previos = existente.get("datos_json") or {}
    datos_nuevos = proceso.get("datos_json") or {}
    return any((
        existente.get("denominacion") != proceso.get("denominacion"),
        existente.get("cuerpo_escala") != proceso.get("cuerpo_escala"),
        existente.get("grupo") != proceso.get("grupo"),
        existente.get("turno") != proceso.get("turno"),
        existente.get("anio_convocatoria") != proceso.get("anio_convocatoria"),
        proceso.get("fecha_apertura") is not None and existente.get("fecha_apertura") != proceso.get("fecha_apertura"),
        proceso.get("fecha_cierre") is not None and existente.get("fecha_cierre") != proceso.get("fecha_cierre"),
        proceso.get("ultima_publicacion_at") is not None and existente.get("ultima_publicacion_at") != proceso.get("ultima_publicacion_at"),
        datos_previos.get("fase_gva") != datos_nuevos.get("fase_gva"),
        datos_previos.get("etapa_actual_gva") != datos_nuevos.get("etapa_actual_gva"),
        (datos_previos.get("contenido_hash") or datos_previos.get("huella_novedad_bolsa"))
            != (datos_nuevos.get("contenido_hash") or (proceso.get("publicacion") or {}).get("contenido_hash")),
    ))


def _cargar_coincidencias(candidatos: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    ids = [int(p["datos_json"]["id_emp"]) for p in candidatos]
    identificadores = [str(p["identificador_estable"]) for p in candidatos]
    if not ids:
        return {}

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo = resolver_organismo(
            cursor,
            tipo="ADMINISTRACION_AUTONOMICA",
            provincia=None,
            nombre="Generalitat Valenciana",
        )
        if organismo is None:
            return {}
        cursor.execute(
            """
            SELECT id, identificador_estable, denominacion, cuerpo_escala, grupo,
                   turno, anio_convocatoria, fecha_apertura, fecha_cierre,
                   ultima_publicacion_at, datos_json
            FROM procesos
            WHERE organismo_id=%s
              AND (
                    identificador_estable = ANY(%s)
                    OR CASE
                         WHEN COALESCE(datos_json->>'id_emp','') ~ '^[0-9]+$'
                         THEN (datos_json->>'id_emp')::bigint = ANY(%s)
                         ELSE FALSE
                       END
                    OR CASE
                         WHEN COALESCE(datos_json->>'codigo_gva','') ~ '^[0-9]+$'
                         THEN (datos_json->>'codigo_gva')::bigint = ANY(%s)
                         ELSE FALSE
                       END
                    OR CASE
                         WHEN COALESCE(datos_json->>'codigo_gva_resuelto','') ~ '^[0-9]+$'
                         THEN (datos_json->>'codigo_gva_resuelto')::bigint = ANY(%s)
                         ELSE FALSE
                       END
                  )
            """,
            (organismo["id"], identificadores, ids, ids, ids),
        )
        filas = list(cursor.fetchall())

    salida: dict[int, dict[str, Any]] = {}
    for fila in filas:
        datos = fila.get("datos_json") or {}
        valores = [
            datos.get("id_emp"),
            datos.get("codigo_gva"),
            datos.get("codigo_gva_resuelto"),
        ]
        identificador = str(fila.get("identificador_estable") or "")
        m = re.fullmatch(r"GVA:(\d+)", identificador)
        if m:
            valores.append(m.group(1))
        for valor in valores:
            try:
                numero = int(valor)
            except (TypeError, ValueError):
                continue
            if numero in ids:
                salida[numero] = dict(fila)
    return salida

def inventariar_bolsas_gva_complementarias() -> dict[str, Any]:
    """Inventario GVA completo sin consultar ni escribir la base de datos."""
    descubiertas: dict[int, str] = {}
    diagnostico: list[dict[str, Any]] = []
    with nuevo_cliente() as client:
        for codigo in CODIGOS_ADMIN_ESTRICTOS:
            try:
                encontradas = _descubrir_por_codigo(client, codigo)
                descubiertas.update(encontradas)
                diagnostico.append({"codigo": codigo, "estado": "OK", "bolsas": len(encontradas)})
            except Exception as exc:
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        candidatas: list[dict[str, Any]] = []
        excluidas: list[dict[str, Any]] = []
        for id_emp, url in sorted(descubiertas.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url)
                proceso = _clasificar_detalle(id_emp, url, respuesta.text)
            except Exception as exc:
                excluidas.append({
                    "id_emp": id_emp, "url": url, "motivo": "error_detalle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            item = {
                "id_emp": id_emp,
                "denominacion": proceso.get("denominacion"),
                "cuerpo_escala": proceso.get("cuerpo_escala"),
                "etapa_actual_gva": (proceso.get("datos_json") or {}).get("etapa_actual_gva"),
                "fase_gva": (proceso.get("datos_json") or {}).get("fase_gva"),
                "motivo_exclusion": proceso.get("motivo_exclusion"),
            }
            if proceso.get("es_oportunidad"):
                candidatas.append(item)
            else:
                excluidas.append(item)

    en_funcionamiento = [
        x for x in candidatas
        if str(x.get("fase_gva") or "").strip().lower() == "bolsa en funcionamiento"
    ]
    return {
        "modo": "INVENTARIO_SIN_BD",
        "escrituras_bd": False,
        "descubiertas": len(descubiertas),
        "candidatas": len(candidatas),
        "en_funcionamiento": len(en_funcionamiento),
        "diagnostico": diagnostico,
        "bolsas_en_funcionamiento": en_funcionamiento,
        "candidatas_otras_fases": [x for x in candidatas if x not in en_funcionamiento],
        "excluidas": excluidas,
    }


def planificar_bolsas_gva_complementarias() -> dict[str, Any]:
    """Genera un plan NUEVA/YA_EXISTE sin realizar ninguna escritura."""
    descubiertas: dict[int, str] = {}
    diagnostico: list[dict[str, Any]] = []

    with nuevo_cliente() as client:
        for codigo in CODIGOS_ADMIN_ESTRICTOS:
            try:
                encontradas = _descubrir_por_codigo(client, codigo)
                descubiertas.update(encontradas)
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "OK",
                    "bolsas": len(encontradas),
                })
            except Exception as exc:
                diagnostico.append({
                    "codigo": codigo,
                    "estado": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        candidatos: list[dict[str, Any]] = []
        excluidos: list[dict[str, Any]] = []
        for id_emp, url in sorted(descubiertas.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url)
                proceso = _clasificar_detalle(id_emp, url, respuesta.text)
            except Exception as exc:
                excluidos.append({
                    "id_emp": id_emp,
                    "url": url,
                    "motivo": "error_detalle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            if proceso["es_oportunidad"]:
                candidatos.append(proceso)
            else:
                excluidos.append({
                    "id_emp": id_emp,
                    "url": url,
                    "motivo": proceso.get("motivo_exclusion") or "fuera_filtro",
                })

    existentes = _cargar_coincidencias(candidatos)
    acciones: list[dict[str, Any]] = []
    for proceso in candidatos:
        id_emp = int(proceso["datos_json"]["id_emp"])
        existente = existentes.get(id_emp)
        actualizable = _bolsa_directa_actualizable(existente, id_emp)
        cambia = bool(actualizable and _bolsa_directa_cambia(existente, proceso))
        accion = "NUEVA" if not existente else ("ACTUALIZAR" if cambia else "YA_EXISTE")
        acciones.append({
            "accion": accion,
            "id_emp": id_emp,
            "identificador_estable": proceso["identificador_estable"],
            "denominacion": proceso["denominacion"],
            "cuerpo_escala": proceso.get("cuerpo_escala"),
            "etapa_actual_gva": proceso["datos_json"].get("etapa_actual_gva"),
            "fase_gva": proceso["datos_json"].get("fase_gva"),
            "categoria_gva": proceso["datos_json"].get("categoria_gva"),
            "proceso_existente_id": existente.get("id") if existente else None,
            "identificador_existente": (
                existente.get("identificador_estable") if existente else None
            ),
        })

    return {
        "modo": "SOLO_REVISION",
        "escrituras_bd": False,
        "descubiertas": len(descubiertas),
        "candidatas": len(candidatos),
        "nuevas": sum(a["accion"] == "NUEVA" for a in acciones),
        "actualizar": sum(a["accion"] == "ACTUALIZAR" for a in acciones),
        "ya_existentes": sum(a["accion"] == "YA_EXISTE" for a in acciones),
        "diagnostico": diagnostico,
        "errores": [
            *[item for item in diagnostico if item.get("estado") == "ERROR"],
            *[item for item in excluidos if item.get("motivo") == "error_detalle"],
        ],
        "acciones": acciones,
        "excluidos": excluidos,
    }


def _resolver_identidad_gva_directa(cursor) -> tuple[int, int]:
    organismo = resolver_organismo(
        cursor,
        tipo="ADMINISTRACION_AUTONOMICA",
        provincia=None,
        nombre="Generalitat Valenciana",
    )
    if organismo is None:
        raise RuntimeError("Persistencia GVA directa bloqueada: organismo Generalitat Valenciana no localizado")
    fuente = resolver_fuente(
        cursor,
        nombre="Diari Oficial de la Generalitat Valenciana",
        tipo="DOGV",
        organismo_id=organismo["id"],
    )
    return int(organismo["id"]), int(fuente["id"])


def persistir_bolsas_gva_complementarias(*, aplicar: bool = False) -> dict[str, Any]:
    """Inserta bolsas nuevas y refresca únicamente las bolsas directas que gestiona este módulo."""
    if not aplicar:
        plan = planificar_bolsas_gva_complementarias()
        nuevas = [a for a in plan["acciones"] if a["accion"] == "NUEVA"]
        return {
            **plan,
            "persistencia": "SOLO_REVISION",
            "insertables": len(nuevas),
        }

    # En modo aplicado se descubre una sola vez, inmediatamente antes de
    # escribir. Antes se ejecutaba primero una planificación completa y se
    # repetían las mismas consultas GVA; esa primera lectura no intervenía en
    # la persistencia y multiplicaba los timeouts cuando la Sede no respondía.
    descubiertas: dict[int, str] = {}
    errores: list[dict[str, Any]] = []
    with nuevo_cliente() as client:
        for codigo in CODIGOS_ADMIN_ESTRICTOS:
            try:
                descubiertas.update(_descubrir_por_codigo(client, codigo))
            except Exception as exc:
                errores.append({
                    "codigo": codigo,
                    "fase": "descubrimiento",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        candidatos: list[dict[str, Any]] = []
        for id_emp, url in sorted(descubiertas.items()):
            try:
                respuesta = _get_gva_con_reintentos(client, url)
                proceso = _clasificar_detalle(id_emp, url, respuesta.text)
            except Exception as exc:
                errores.append({
                    "id_emp": id_emp,
                    "fase": "detalle",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            if proceso.get("es_oportunidad"):
                candidatos.append(proceso)

    existentes = _cargar_coincidencias(candidatos)
    insertables = [
        p for p in candidatos
        if int(p["datos_json"]["id_emp"]) not in existentes
        and str(p["datos_json"].get("fase_gva") or "").strip().lower() == "bolsa en funcionamiento"
    ]
    actualizables = [
        p for p in candidatos
        if (
            _bolsa_directa_actualizable(
                existentes.get(int(p["datos_json"]["id_emp"])),
                int(p["datos_json"]["id_emp"]),
            )
            and _bolsa_directa_cambia(
                existentes[int(p["datos_json"]["id_emp"])],
                p,
            )
        )
    ]

    insertados: list[dict[str, Any]] = []
    actualizados: list[dict[str, Any]] = []
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo_id, fuente_id = _resolver_identidad_gva_directa(cursor)
        for proceso in insertables:
            datos = dict(proceso.get("datos_json") or {})
            id_emp = int(datos["id_emp"])
            cursor.execute(
                """
                INSERT INTO procesos (
                    organismo_id,codigo_externo,identificador_estable,denominacion,
                    cuerpo_escala,grupo,tipo_proceso,turno,estado,
                    anio_convocatoria,fecha_apertura,fecha_cierre,ultima_publicacion_at,
                    fuente_principal_id,datos_json,es_oportunidad,origen_dato,
                    revision_estado,ambito_administrativo,created_at,updated_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,'EN_CURSO',
                    %s,%s,%s,%s,%s,%s,TRUE,'AUTOMATICO',
                    'PUBLICADA','SI',NOW(),NOW()
                )
                ON CONFLICT (identificador_estable) DO NOTHING
                RETURNING id
                """,
                (
                    organismo_id,
                    str(id_emp),
                    f"GVA:{id_emp}",
                    proceso["denominacion"],
                    proceso.get("cuerpo_escala"),
                    proceso.get("grupo"),
                    "Bolsa de trabajo",
                    proceso.get("turno"),
                    proceso.get("anio_convocatoria"),
                    proceso.get("fecha_apertura"),
                    proceso.get("fecha_cierre"),
                    proceso.get("ultima_publicacion_at"),
                    fuente_id,
                    Jsonb(datos),
                ),
            )
            fila = cursor.fetchone()
            if fila:
                insertados.append({"id_emp": id_emp, "proceso_id": int(fila["id"])})

        for proceso in actualizables:
            datos = dict(proceso.get("datos_json") or {})
            publicacion_origen = proceso.get("publicacion") or {}
            huella_novedad = publicacion_origen.get("contenido_hash")
            if huella_novedad:
                datos["huella_novedad_bolsa"] = huella_novedad
            id_emp = int(datos["id_emp"])
            existente = existentes[id_emp]
            cursor.execute(
                """
                UPDATE procesos
                SET denominacion=%s,
                    cuerpo_escala=%s,
                    grupo=%s,
                    turno=%s,
                    anio_convocatoria=%s,
                    fecha_apertura=COALESCE(%s,fecha_apertura),
                    fecha_cierre=COALESCE(%s,fecha_cierre),
                    ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),
                    datos_json=COALESCE(datos_json,'{}'::jsonb) || %s,
                    updated_at=NOW()
                WHERE id=%s AND identificador_estable=%s
                RETURNING id, updated_at
                """,
                (
                    proceso.get("denominacion"),
                    proceso.get("cuerpo_escala"),
                    proceso.get("grupo"),
                    proceso.get("turno"),
                    proceso.get("anio_convocatoria"),
                    proceso.get("fecha_apertura"),
                    proceso.get("fecha_cierre"),
                    proceso.get("ultima_publicacion_at"),
                    Jsonb(datos),
                    existente["id"], existente["identificador_estable"],
                ),
            )
            fila = cursor.fetchone()
            if fila:
                proceso_id = int(fila["id"])
                actualizados.append({"id_emp": id_emp, "proceso_id": proceso_id})
                etapa = datos.get("etapa_actual_gva") or datos.get("fase_gva") or "Actualización de bolsa"
                referencia = f"GVA_BOLSA_ETAPA:{id_emp}:{huella_novedad or etapa}"
                cursor.execute(
                    """
                    INSERT INTO publicaciones (
                        proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,
                        datos_json,detectada_at
                    ) VALUES (%s,%s,%s,'BOLSA_ETAPA',%s,%s,%s,%s,NOW())
                    ON CONFLICT (fuente_id,referencia,url) DO NOTHING
                    RETURNING id
                    """,
                    (
                        proceso_id,
                        fuente_id,
                        referencia,
                        str(etapa),
                        proceso.get("ultima_publicacion_at"),
                        datos.get("url_detalle") or datos.get("url_oficial") or "",
                        Jsonb({"origen": "GVA_BOLSA", "etapa_actual_gva": datos.get("etapa_actual_gva"), "fase_gva": datos.get("fase_gva")}),
                    ),
                )
                cursor.fetchone()

        connection.commit()

    return {
        "modo": "APLICADO",
        "escrituras_bd": True,
        "insertables": len(insertables),
        "insertados": insertados,
        "actualizables": len(actualizables),
        "actualizados": actualizados,
        "omitidas_por_deduplicacion_o_fase": len(candidatos) - len(insertables) - len(actualizables),
        "errores": [*plan.get("errores", []), *errores],
    }
