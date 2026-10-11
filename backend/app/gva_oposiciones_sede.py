"""Oposiciones de la Generalitat Valenciana desde el buscador autonómico de la Sede.

Sustituye al descubrimiento por el portal estatal (administracion.gob.es), que
devolvía 0 resultados sin avisar. Aquí:

* el listado (``cercador-ocupacio-publica``) se lee con parsers que FALLAN EN
  ALTO si la página no tiene la estructura esperada (nunca "0 en silencio");
* cada ficha (``detall-ocupacio-publica?id_emp=N``) se analiza para saber su
  etapa/fase actual y decidir si el proceso sigue ACTIVO o ya está RESUELTO
  (misma definición única de ``ciclo_vida``);
* solo se insertan procesos activos del ámbito administrativo (cuerpos
  A1/A2/C1/C2-01, turno libre o promoción interna); los ya resueltos nunca se insertan, y los que
  tenemos EN_CURSO se cierran cuando su ficha acredita la resolución.

Los parsers y reglas son puros (sin red ni BD) y se prueban con HTML guardado.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import ciclo_vida
from .ciclo_vida import normalizar
from .database import get_connection
from .gva_bolsas_complementarias import _resolver_identidad_gva_directa, motivo_especialidad
from .gva_estatal_service import _get_gva_con_reintentos
from .gva_estatal_source import PATRONES_ADMIN, nuevo_cliente

BASE_URL = "https://sede.gva.es"
URL_LISTADO = "https://sede.gva.es/es/cercador-ocupacio-publica"
GRUPOS_ADMIN = "1029,1030,1032,1033"  # A1, A2, C1, C2
CONVOCATORIAS_SEDE = "507,509"  # Oposición, Proceso de estabilización
MAX_PAGINAS = 12
MAX_CONSULTAS_RANGO = 40
MAX_FICHAS_POR_EJECUCION = 120
PAUSA_ENTRE_PETICIONES = 0.4

TIPOS_ACEPTADOS = {"oposicion": "Oposición", "proceso de estabilizacion": "Proceso de estabilización"}
# Excepción acordada: la Convocatoria 1/26 del Cuerpo Superior de Administración
# se divide en procesos selectivos (general, ciencias jurídicas, fondos europeos)
# y los tres son oposiciones administrativas.
ESPECIALIDADES_PERMITIDAS_CUERPO_SUPERIOR = frozenset({"especialidad_apt", "especialidad_fondos_europeos"})
FASES_RESOLUCION = frozenset({"adjudicacion y nombramiento", "nombramiento"})
_RE_FECHA = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_RE_CODIGO_CUERPO = re.compile(r"\b([abc][12])-(\d{2})(?:-[a-z0-9]{2,5})*\b", re.I)
_RE_ETAPA_INSCRIPCION = re.compile(
    r"bases y apertura|apertura de plazo|presentacion de solicitudes|solicitudes|ampliacion del? plazo|convocatoria"
)


class EstructuraInesperada(RuntimeError):
    """La página de la Sede no tiene la estructura que el parser conoce."""


# ------------------------------------------------------------------ utilidades --
def _txt(nodo) -> str:
    return " ".join(nodo.get_text(" ", strip=True).split()) if nodo else ""


def _fecha(valor: str | None) -> date | None:
    m = _RE_FECHA.search(valor or "")
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _entero(valor: str | None) -> int | None:
    m = re.search(r"[\d.]+", valor or "")
    if not m:
        return None
    try:
        return int(m.group(0).replace(".", ""))
    except ValueError:
        return None


def _id_emp(href: str | None) -> int | None:
    m = re.search(r"id_emp=(\d+)", href or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------- listado --
def parsear_listado(html: str) -> dict[str, Any]:
    """Devuelve ``{"total", "desde", "hasta", "tarjetas": [...]}`` o lanza
    ``EstructuraInesperada`` si la página no parece un listado de la Sede."""
    soup = BeautifulSoup(html, "html.parser")
    total_nodo = soup.select_one(".total-results")
    contenedor = soup.select_one(".search-results")
    if total_nodo is None and contenedor is None:
        raise EstructuraInesperada("el listado no contiene .total-results ni .search-results")

    texto = _txt(soup)
    total = None
    m = re.search(r"([\d.]+)\s+resultados?", _txt(total_nodo)) if total_nodo else None
    if m:
        total = _entero(m.group(1))
    desde = hasta = None
    m = re.search(r"del\s+(\d+)\s+al\s+(\d+)\s+de\s+un\s+total\s+de\s+([\d.]+)", texto, re.I)
    if m:
        desde, hasta, total = int(m.group(1)), int(m.group(2)), _entero(m.group(3))

    tarjetas: list[dict[str, Any]] = []
    for card in soup.select("div.opposition.card"):
        enlace = card.select_one('a[href*="id_emp"]')
        id_emp = _id_emp(enlace.get("href") if enlace else None)
        if id_emp is None:
            continue
        campos: dict[str, str] = {}
        for etiqueta in card.select("span.bold"):
            hermano = etiqueta.find_next_sibling("span")
            clave = normalizar(_txt(etiqueta))
            if hermano is not None:
                campos[clave] = _txt(hermano)
            else:
                campos[clave] = _txt(etiqueta.parent).replace(_txt(etiqueta), "", 1).strip()
        plazas = campos.get("plazas")
        if plazas is None:
            m_pl = re.search(r"Plazas\s*([\d.]+)", _txt(card))
            plazas = m_pl.group(1) if m_pl else None
        plazo = [f for f in (_fecha(_txt(s)) for s in card.select(".dates span")) if f]
        insignia = _txt(card.select_one(".state, .gva-badge"))
        organismo_nodo = card.select_one(".additional-info")
        tarjetas.append({
            "id_emp": id_emp,
            "url": urljoin(BASE_URL, enlace.get("href")),
            "titulo": _txt(card.select_one(".title")),
            "organismo": _txt(organismo_nodo),
            "proceso_selectivo": campos.get("proceso selectivo"),
            "tipo_prueba": campos.get("tipo de prueba"),
            "plazas": _entero(plazas),
            "plazo_etapa_desde": plazo[0].isoformat() if plazo else None,
            "plazo_etapa_hasta": plazo[-1].isoformat() if len(plazo) > 1 else None,
            "insignia": insignia or None,
        })
    if total is None:
        raise EstructuraInesperada("el listado no indica el total de resultados")
    if total > 0 and not tarjetas:
        raise EstructuraInesperada(f"el listado indica {total} resultados pero no se pudo leer ninguna tarjeta")
    # La Sede corta a 100 resultados y lo avisa con un cartel: nunca hay que
    # fiarse de un listado truncado (se perderían procesos sin ningún error).
    truncado = bool(re.search(r"demasiados resultados|solo se muestran los \d+ primeros", texto, re.I)) or total >= 100
    return {"total": total, "desde": desde, "hasta": hasta, "tarjetas": tarjetas, "truncado": truncado}


def _parametros_listado(pagina: int, desde: date | None, hasta: date | None) -> dict[str, str]:
    params = {
        "pagina": str(pagina),
        "tamanyoPagina": "30",
        "grupos": GRUPOS_ADMIN,
        "convocatorias": CONVOCATORIAS_SEDE,
        "tipoOrganismo": "GVA",
    }
    if desde:
        params["fechaPublicacionDesde"] = desde.isoformat()
    if hasta:
        params["fechaPublicacionHasta"] = hasta.isoformat()
    return params


def _recorrer_paginas(client, desde: date | None, hasta: date | None) -> tuple[dict[int, dict[str, Any]], bool]:
    vistas: dict[int, dict[str, Any]] = {}
    truncado = False
    for pagina in range(1, MAX_PAGINAS + 1):
        respuesta = _get_gva_con_reintentos(client, URL_LISTADO, params=_parametros_listado(pagina, desde, hasta))
        listado = parsear_listado(respuesta.text)
        truncado = truncado or listado["truncado"]
        if listado["truncado"]:
            break  # inútil leer más páginas de un tramo truncado: se divide por fechas
        for tarjeta in listado["tarjetas"]:
            vistas[tarjeta["id_emp"]] = tarjeta
        total = listado["total"]
        if not listado["tarjetas"] or len(vistas) >= total:
            break
        if listado["hasta"] is not None and listado["hasta"] >= total:
            break
        time.sleep(PAUSA_ENTRE_PETICIONES)
    return vistas, truncado


def descubrir_oposiciones(client, *, desde: date | None, hasta: date | None) -> list[dict[str, Any]]:
    """Recorre el listado. Si la Sede lo trunca (máx. 100 resultados) parte el rango
    de fechas en dos y repite; si un solo día sigue truncado, falla en alto."""
    if desde is None or hasta is None:
        vistas, truncado = _recorrer_paginas(client, desde, hasta)
        if truncado:
            raise EstructuraInesperada("la Sede devolvió un listado truncado (máx. 100) y no hay rango de fechas que dividir")
        return sorted(vistas.values(), key=lambda t: t["id_emp"])

    resultado: dict[int, dict[str, Any]] = {}
    pendientes = [(desde, hasta)]
    consultas = 0
    while pendientes:
        d, h = pendientes.pop()
        consultas += 1
        if consultas > MAX_CONSULTAS_RANGO:
            raise EstructuraInesperada(f"demasiadas consultas al dividir el rango de fechas ({MAX_CONSULTAS_RANGO})")
        vistas, truncado = _recorrer_paginas(client, d, h)
        if truncado:
            if d >= h:
                raise EstructuraInesperada(f"la Sede trunca el listado incluso para un solo día ({d.isoformat()})")
            mitad = d + (h - d) // 2
            pendientes.append((d, mitad))
            pendientes.append((mitad + timedelta(days=1), h))
            continue
        resultado.update(vistas)
    return sorted(resultado.values(), key=lambda t: t["id_emp"])


# ----------------------------------------------------------------------- ficha --
def parsear_ficha(html: str, id_emp: int, url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    raiz = soup.select_one("div.datosOposicion") or soup
    texto = _txt(raiz)
    lista = soup.select_one(".lista-etapas")
    if lista is None and "Información básica" not in texto and "Informació bàsica" not in texto:
        raise EstructuraInesperada(f"la ficha {id_emp} no tiene la estructura esperada")

    etapas: list[dict[str, Any]] = []
    etapa_actual = None
    if lista is not None:
        for bloque in lista.select("div.informacion"):
            nombre_nodo = bloque.select_one("p.font-weight-bold")
            if nombre_nodo is None:
                continue
            nombre = _txt(nombre_nodo)
            es_actual = bool(re.match(r"etapa actual\s*:", nombre, re.I))
            nombre = re.sub(r"^etapa actual\s*:\s*", "", nombre, flags=re.I).strip()
            fecha = _fecha(_txt(bloque.select_one(".fecha-etapa")))
            plazo_nodo = bloque.parent.select_one(".plazo-etapa") if bloque.parent else None
            etapas.append({
                "nombre": nombre,
                "fecha_publicacion": fecha.isoformat() if fecha else None,
                "plazo": _txt(plazo_nodo) or None,
                "actual": es_actual,
            })
            if es_actual and etapa_actual is None:
                etapa_actual = nombre
    if etapa_actual is None:
        m = re.search(r"Etapa actual\s*:\s*(.+?)(?:\s+Plazo\b|\s+Fase\b|\s+Atrás|$)", texto)
        etapa_actual = m.group(1).strip() if m else None

    fase_nodo = soup.select_one("div.bloque.fase .textos")
    fase = _txt(fase_nodo) or None
    if fase is None:
        m = re.search(r"\bFase\s+(.+?)(?=\s+Plazo\b|\s+Publicaci[oó]n|\s+Enlaces|$)", texto)
        fase = m.group(1).strip() if m else None

    plazo_desde = plazo_hasta = None
    m = re.search(r"Plazo\s+(?:abierto\s+|cerrado\s+)?Desde\s+(\d{2}-\d{2}-\d{4})\s+(?:hasta|a)\s+(\d{2}-\d{2}-\d{4})", texto, re.I)
    if m:
        plazo_desde, plazo_hasta = _fecha(m.group(1)), _fecha(m.group(2))

    def tras(etiqueta: str, hasta: str) -> str | None:
        m_ = re.search(rf"{etiqueta}\s+(.+?)\s+(?:{hasta})\b", texto)
        return m_.group(1).strip() if m_ else None

    convocatoria = tras("Información básica Convocatoria", "Prueba")
    prueba = tras(r"\bPrueba", "Grupo")
    grupo_m = re.search(r"\bGrupo\s+([A-Z0-9/]+)", texto)
    titulo_nodo = raiz.find(["h1", "h2"])
    titulo = _txt(titulo_nodo)
    if not titulo:
        m = re.match(r"(?:Detalle\s+)?(.+?)\s+Conselleria|\s+Código SIA", texto)
        titulo = (m.group(1) if m and m.group(1) else texto[:200]).strip()
    titulo = re.sub(r"^Detalle\s+", "", titulo)
    sia = re.search(r"Código SIA\s*:\s*(\d+)", texto)
    gva = re.search(r"Código GVA\s*:\s*(\d+)", texto)
    plazas = re.search(r"Núm\. de plazas totales\s*([\d.]+)", texto)
    dogv = re.search(r"Publicación DOGV\s*núm\.\s*(\d+)\s*de\s*(\d{2}-\d{2}-\d{4})", texto)
    dogv_enlace = None
    for a in soup.select("a[href*='dogv.gva.es']"):
        dogv_enlace = a.get("href")
        break
    fechas_pub = [e["fecha_publicacion"] for e in etapas if e.get("fecha_publicacion")]
    return {
        "id_emp": id_emp,
        "url": url or f"{URL_LISTADO.replace('cercador-ocupacio-publica', 'detall-ocupacio-publica')}?id_emp={id_emp}",
        "titulo": titulo,
        "codigo_sia": sia.group(1) if sia else None,
        "codigo_gva": gva.group(1) if gva else str(id_emp),
        "etapa_actual": etapa_actual,
        "fase": fase,
        "plazo_desde": plazo_desde.isoformat() if plazo_desde else None,
        "plazo_hasta": plazo_hasta.isoformat() if plazo_hasta else None,
        "convocatoria": convocatoria,
        "prueba": prueba,
        "grupo": grupo_m.group(1).upper() if grupo_m else None,
        "plazas": _entero(plazas.group(1)) if plazas else None,
        "dogv_numero": dogv.group(1) if dogv else None,
        "dogv_fecha": _fecha(dogv.group(2)).isoformat() if dogv and _fecha(dogv.group(2)) else None,
        "dogv_url": dogv_enlace,
        "etapas": etapas,
        "ultima_publicacion": max(fechas_pub) if fechas_pub else None,
        "contenido_hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
    }


# ------------------------------------------------------------------ reglas -----
def evaluar_cierre(ficha: dict[str, Any], tipo_proceso: str = "Oposición") -> dict[str, Any] | None:
    """Decide si la ficha acredita que el proceso YA está resuelto.

    Devuelve ``{"estado","regla","titulo","fecha"}`` o ``None`` si sigue activo.
    Los resultados parciales/provisionales (por ejercicio o fase) no cierran.
    """
    fase = normalizar(ficha.get("fase"))
    etapas = ficha.get("etapas") or []
    if fase in FASES_RESOLUCION:
        return {
            "estado": ciclo_vida.ESTADO_FINALIZADO,
            "regla": "FASE_ADJUDICACION_NOMBRAMIENTO",
            "titulo": ficha.get("etapa_actual") or ficha.get("fase"),
            "fecha": ficha.get("ultima_publicacion"),
        }
    for etapa in etapas:
        if normalizar(etapa.get("nombre")) == "nombramiento":
            return {
                "estado": ciclo_vida.ESTADO_FINALIZADO,
                "regla": "ETAPA_NOMBRAMIENTO",
                "titulo": etapa["nombre"],
                "fecha": etapa.get("fecha_publicacion"),
            }
    publicaciones = []
    if ficha.get("etapa_actual"):
        publicaciones.append({"id": 0, "titulo": ficha["etapa_actual"], "fecha_publicacion": ficha.get("ultima_publicacion")})
    for i, etapa in enumerate(etapas, start=1):
        publicaciones.append({"id": i, "titulo": etapa.get("nombre"), "fecha_publicacion": etapa.get("fecha_publicacion")})
    evidencia = ciclo_vida.elegir_evidencia(publicaciones, tipo_proceso)
    if evidencia is None:
        return None
    return {
        "estado": evidencia["estado"],
        "regla": evidencia["regla"],
        "titulo": evidencia["titulo"],
        "fecha": evidencia["fecha_publicacion"],
    }


def clasificar_ambito(titulo: str, convocatoria: str | None = None) -> dict[str, Any]:
    """¿Es una oposición administrativa general (A1/A2/C1/C2-01)? Turno libre o promoción interna."""
    n = normalizar(titulo)
    codigos = sorted({m.group(0).upper() for m in _RE_CODIGO_CUERPO.finditer(titulo or "")})
    turno = "TURNO_LIBRE"
    if "promocion interna" in n or normalizar(convocatoria) == "promocion interna":
        return {"administrativo": False, "motivo": "promocion_interna", "codigos": codigos, "turno": "PROMOCION_INTERNA"}
    # El buscador de la Sede NO marca como "Libre" algunas convocatorias de turno libre
    # (p. ej. la 70/26), así que no se filtra allí: se filtra aquí por el título.
    # Si el título habla de un turno que no es el libre (reserva de discapacidad...), fuera.
    if re.search(r"\bturno\b", n) and "turno libre" not in n:
        return {"administrativo": False, "motivo": "turno_no_libre", "codigos": codigos, "turno": "OTRO"}
    especialidad = motivo_especialidad(n)
    if codigos:
        generales = [c for c in codigos if re.fullmatch(r"(A1|A2|C1|C2)-01(?:-[A-Z0-9]+)*", c)]
        if not generales:
            return {"administrativo": False, "motivo": "cuerpo_no_administrativo_general", "codigos": codigos, "turno": turno}
        if especialidad and not (
            especialidad in ESPECIALIDADES_PERMITIDAS_CUERPO_SUPERIOR
            and "cuerpo superior de administracion" in n
        ):
            return {"administrativo": False, "motivo": especialidad, "codigos": codigos, "turno": turno}
        return {"administrativo": True, "motivo": None, "codigos": codigos, "turno": turno}
    if especialidad:
        return {"administrativo": False, "motivo": especialidad, "codigos": codigos, "turno": turno}
    if any(re.search(p, n) for p in PATRONES_ADMIN):
        return {"administrativo": True, "motivo": None, "codigos": codigos, "turno": turno}
    return {"administrativo": False, "motivo": "sin_perfil_administrativo", "codigos": codigos, "turno": turno}


def _es_etapa_inscripcion(etapa: str | None) -> bool:
    return bool(etapa and _RE_ETAPA_INSCRIPCION.search(normalizar(etapa)))


def construir_proceso(tarjeta: dict[str, Any] | None, ficha: dict[str, Any]) -> dict[str, Any]:
    """Registro listo para persistir (no toca BD)."""
    titulo = ficha.get("titulo") or (tarjeta or {}).get("titulo") or f"Proceso GVA {ficha['id_emp']}"
    ambito = clasificar_ambito(titulo, ficha.get("convocatoria"))
    inscripcion = _es_etapa_inscripcion(ficha.get("etapa_actual"))
    apertura = ficha.get("plazo_desde") if inscripcion else None
    cierre = ficha.get("plazo_hasta") if inscripcion else None
    m_anio = re.search(r"\b(?:convocatoria\s+)?\d+/(\d{2})\b", titulo, re.I)
    anio = 2000 + int(m_anio.group(1)) if m_anio else None
    if not anio and ficha.get("dogv_fecha"):
        anio = int(ficha["dogv_fecha"][:4])
    grupo = ficha.get("grupo")
    codigo_unico = ambito["codigos"][0] if len(ambito["codigos"]) == 1 else None
    return {
        "id_emp": ficha["id_emp"],
        "identificador_estable": f"GVA:{ficha['id_emp']}",
        "codigo_externo": ficha.get("codigo_gva") or str(ficha["id_emp"]),
        "denominacion": titulo,
        "tipo_proceso": TIPOS_ACEPTADOS.get(normalizar((tarjeta or {}).get("proceso_selectivo") or ficha.get("convocatoria")), "Oposición"),
        "cuerpo_escala": codigo_unico,
        "grupo": grupo,
        "turno": ambito["turno"],
        "anio_convocatoria": anio,
        "fecha_apertura": apertura,
        "fecha_cierre": cierre,
        "ultima_publicacion": ficha.get("ultima_publicacion") or ficha.get("dogv_fecha"),
        "ambito": ambito,
        "datos_json": {
            "fuente_descubrimiento": "sede.gva.es",
            "categoria_gva": "OPOSICION",
            "id_emp": ficha["id_emp"],
            "codigo_gva": ficha.get("codigo_gva"),
            "codigo_sia": ficha.get("codigo_sia"),
            "url_detalle": ficha["url"],
            "proceso_selectivo_gva": (tarjeta or {}).get("proceso_selectivo"),
            "tipo_prueba_gva": (tarjeta or {}).get("tipo_prueba") or ficha.get("prueba"),
            "convocatoria_gva": ficha.get("convocatoria"),
            "etapa_actual_gva": ficha.get("etapa_actual"),
            "fase_gva": ficha.get("fase"),
            "plazo_etapa_desde": ficha.get("plazo_desde"),
            "plazo_etapa_hasta": ficha.get("plazo_hasta"),
            "plazas": ficha.get("plazas") or (tarjeta or {}).get("plazas"),
            "codigos_administrativos": ambito["codigos"],
            "dogv_numero": ficha.get("dogv_numero"),
            "dogv_fecha": ficha.get("dogv_fecha"),
            "dogv_url": ficha.get("dogv_url"),
            "etapas": ficha.get("etapas"),
            "contenido_hash": ficha.get("contenido_hash"),
        },
    }


# ------------------------------------------------------------------ persistencia --
def _cargar_existentes(cursor, organismo_id: int, ids: list[int]) -> dict[int, dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, identificador_estable, estado, tipo_proceso, denominacion, datos_json
        FROM procesos
        WHERE organismo_id=%s
          AND (
                identificador_estable = ANY(%s)
                OR CASE WHEN COALESCE(datos_json->>'id_emp','') ~ '^[0-9]+$'
                        THEN (datos_json->>'id_emp')::bigint = ANY(%s) ELSE FALSE END
                OR CASE WHEN COALESCE(datos_json->>'codigo_gva','') ~ '^[0-9]+$'
                        THEN (datos_json->>'codigo_gva')::bigint = ANY(%s) ELSE FALSE END
              )
        """,
        (organismo_id, [f"GVA:{i}" for i in ids], ids, ids),
    )
    salida: dict[int, dict[str, Any]] = {}
    for fila in cursor.fetchall():
        datos = fila.get("datos_json") or {}
        candidatos = [datos.get("id_emp"), datos.get("codigo_gva")]
        m = re.fullmatch(r"GVA:(\d+)", str(fila.get("identificador_estable") or ""))
        if m:
            candidatos.append(m.group(1))
        for valor in candidatos:
            try:
                numero = int(valor)
            except (TypeError, ValueError):
                continue
            if numero in ids:
                salida.setdefault(numero, dict(fila))
    return salida


def _cargar_en_curso_propias(cursor, organismo_id: int) -> list[int]:
    """id_emp de las oposiciones GVA que seguimos como activas (para refrescarlas
    aunque ya no salgan en la ventana de fechas del listado)."""
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(datos_json->>'id_emp',''), substring(identificador_estable from '^GVA:(\\d+)$')) AS id_emp
        FROM procesos
        WHERE organismo_id=%s
          AND LOWER(COALESCE(estado,'')) NOT IN """ + ciclo_vida.ESTADOS_TERMINALES_SQL_IN + """
          AND LOWER(COALESCE(tipo_proceso,'')) NOT LIKE '%%bolsa%%'
          AND COALESCE(datos_json->>'categoria_gva','') = 'OPOSICION'
        """,
        (organismo_id,),
    )
    return sorted({int(f["id_emp"]) for f in cursor.fetchall() if str(f.get("id_emp") or "").isdigit()})


def _insertar(cursor, organismo_id: int, fuente_id: int, p: dict[str, Any]) -> int | None:
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
            %s,%s,%s,%s,%s,%s,TRUE,'AUTOMATICO','PUBLICADA','SI',NOW(),NOW()
        )
        ON CONFLICT (identificador_estable) DO NOTHING
        RETURNING id
        """,
        (
            organismo_id, p["codigo_externo"], p["identificador_estable"], p["denominacion"],
            p["cuerpo_escala"], p["grupo"], p["tipo_proceso"], p["turno"], p["anio_convocatoria"], p["fecha_apertura"],
            p["fecha_cierre"], p["ultima_publicacion"], fuente_id, Jsonb(p["datos_json"]),
        ),
    )
    fila = cursor.fetchone()
    return int(fila["id"]) if fila else None


def _refrescar(cursor, proceso_id: int, p: dict[str, Any]) -> None:
    cursor.execute(
        f"""
        UPDATE procesos
        SET denominacion=%s,
            fecha_apertura=COALESCE(%s,fecha_apertura),
            fecha_cierre=COALESCE(%s,fecha_cierre),
            ultima_publicacion_at=COALESCE(%s,ultima_publicacion_at),
            datos_json=COALESCE(datos_json,'{{}}'::jsonb) || %s,
            updated_at=NOW()
        WHERE id=%s AND LOWER(COALESCE(estado,'')) NOT IN {ciclo_vida.ESTADOS_TERMINALES_SQL_IN}
        """,
        (p["denominacion"], p["fecha_apertura"], p["fecha_cierre"], p["ultima_publicacion"],
         Jsonb(p["datos_json"]), proceso_id),
    )


def _registrar_publicaciones(cursor, proceso_id: int, fuente_id: int, p: dict[str, Any]) -> None:
    datos = p["datos_json"]
    url = datos["url_detalle"]
    id_emp = p["id_emp"]
    etapa = datos.get("etapa_actual_gva")
    if etapa:
        huella = (datos.get("contenido_hash") or "")[:16]
        cursor.execute(
            """
            INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json,detectada_at)
            VALUES (%s,%s,%s,'ETAPA',%s,%s,%s,%s,NOW())
            ON CONFLICT (fuente_id,referencia,url) DO NOTHING
            """,
            (proceso_id, fuente_id, f"GVA_OPOSICION_ETAPA:{id_emp}:{normalizar(etapa)[:80]}:{huella}",
             etapa, p["ultima_publicacion"], url,
             Jsonb({"origen": "GVA_SEDE", "fase_gva": datos.get("fase_gva")})),
        )
    if datos.get("dogv_numero"):
        cursor.execute(
            """
            INSERT INTO publicaciones (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,url,datos_json,detectada_at)
            VALUES (%s,%s,%s,'CONVOCATORIA',%s,%s,%s,%s,NOW())
            ON CONFLICT (fuente_id,referencia,url) DO NOTHING
            """,
            (proceso_id, fuente_id, f"GVA_OPOSICION_DOGV:{id_emp}:{datos['dogv_numero']}",
             f"DOGV núm. {datos['dogv_numero']}", datos.get("dogv_fecha"),
             datos.get("dogv_url") or url, Jsonb({"origen": "GVA_SEDE"})),
        )


def _cerrar(cursor, existente: dict[str, Any], cierre: dict[str, Any], url: str) -> bool:
    anterior = existente["estado"]
    cursor.execute(
        f"""
        UPDATE procesos
        SET estado=%s,
            datos_json=COALESCE(datos_json,'{{}}'::jsonb) || %s,
            updated_at=NOW()
        WHERE id=%s AND LOWER(COALESCE(estado,'')) NOT IN {ciclo_vida.ESTADOS_TERMINALES_SQL_IN}
        """,
        (cierre["estado"],
         Jsonb({"cierre": {"origen": "FICHA_GVA", "regla": cierre["regla"], "titulo": cierre["titulo"],
                           "fecha": cierre["fecha"], "url": url}}),
         existente["id"]),
    )
    if not cursor.rowcount:
        return False
    cursor.execute(
        "INSERT INTO cambios (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen,significativo) "
        "VALUES (%s,'ACTUALIZACION','estado',%s,%s,%s,TRUE)",
        (existente["id"], anterior, cierre["estado"], "Proceso selectivo resuelto según la ficha de la Sede GVA"),
    )
    return True


def analizar_oposiciones_gva_sede(*, dias: int, hoy: date | None = None, conocidas: list[int] | None = None) -> dict[str, Any]:
    """Lee Sede (listado + fichas). Sin escrituras en BD. ``conocidas`` son id_emp
    que ya seguimos como activas y deben revisarse aunque no salgan en el listado."""
    hoy = hoy or date.today()
    desde = hoy - timedelta(days=max(dias, 1) - 1)
    errores: list[dict[str, Any]] = []
    excluidas: list[dict[str, Any]] = []
    analizadas: list[dict[str, Any]] = []
    with nuevo_cliente() as client:
        tarjetas = descubrir_oposiciones(client, desde=desde, hasta=hoy)
        por_id = {t["id_emp"]: t for t in tarjetas}
        candidatas: list[int] = []
        for t in tarjetas:
            if normalizar(t.get("proceso_selectivo")) not in TIPOS_ACEPTADOS:
                excluidas.append({"id_emp": t["id_emp"], "titulo": t["titulo"], "motivo": f"proceso_selectivo:{t.get('proceso_selectivo')}"})
                continue
            ambito = clasificar_ambito(t["titulo"])
            if not ambito["administrativo"]:
                excluidas.append({"id_emp": t["id_emp"], "titulo": t["titulo"], "motivo": ambito["motivo"]})
                continue
            candidatas.append(t["id_emp"])
        for i in conocidas or []:
            if i not in candidatas:
                candidatas.append(i)
        recortadas = candidatas[MAX_FICHAS_POR_EJECUCION:]
        for id_emp in candidatas[:MAX_FICHAS_POR_EJECUCION]:
            url = f"{BASE_URL}/es/detall-ocupacio-publica?id_emp={id_emp}"
            try:
                respuesta = _get_gva_con_reintentos(client, url)
                ficha = parsear_ficha(respuesta.text, id_emp, url)
            except Exception as exc:
                errores.append({"id_emp": id_emp, "fase": "ficha", "error": f"{type(exc).__name__}: {exc}"})
                continue
            proceso = construir_proceso(por_id.get(id_emp), ficha)
            cierre = evaluar_cierre(ficha, proceso["tipo_proceso"])
            analizadas.append({"proceso": proceso, "cierre": cierre, "ficha_titulo": ficha["titulo"]})
            time.sleep(PAUSA_ENTRE_PETICIONES)
    return {
        "desde": desde.isoformat(), "hasta": hoy.isoformat(),
        "en_listado": len(tarjetas), "excluidas": excluidas, "analizadas": analizadas,
        "sin_revisar_por_limite": recortadas, "errores": errores,
    }


def persistir_oposiciones_gva_sede(*, dias: int = 30, aplicar: bool = False, hoy: date | None = None) -> dict[str, Any]:
    """Descubre oposiciones activas de la GVA, las inserta y cierra las resueltas."""
    conocidas: list[int] = []
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo_id, fuente_id = _resolver_identidad_gva_directa(cursor)
        conocidas = _cargar_en_curso_propias(cursor, organismo_id)

    analisis = analizar_oposiciones_gva_sede(dias=dias, hoy=hoy, conocidas=conocidas)
    analizadas = analisis["analizadas"]

    insertables = [a for a in analizadas if a["cierre"] is None and a["proceso"]["ambito"]["administrativo"]]
    resumen_base = {
        "desde": analisis["desde"], "hasta": analisis["hasta"],
        "en_listado": analisis["en_listado"],
        "excluidas": analisis["excluidas"],
        "sin_revisar_por_limite": analisis["sin_revisar_por_limite"],
        "errores": analisis["errores"],
    }
    if not aplicar:
        return {
            **resumen_base, "modo": "SOLO_REVISION", "escrituras_bd": False,
            "activas": [{"id_emp": a["proceso"]["id_emp"], "titulo": a["proceso"]["denominacion"],
                         "etapa": a["proceso"]["datos_json"]["etapa_actual_gva"]} for a in insertables],
            "resueltas": [{"id_emp": a["proceso"]["id_emp"], "titulo": a["proceso"]["denominacion"], "cierre": a["cierre"]}
                          for a in analizadas if a["cierre"] is not None],
        }

    insertados: list[dict[str, Any]] = []
    refrescados: list[dict[str, Any]] = []
    cerrados: list[dict[str, Any]] = []
    ignorados_resueltos: list[int] = []
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo_id, fuente_id = _resolver_identidad_gva_directa(cursor)
        existentes = _cargar_existentes(cursor, organismo_id, [a["proceso"]["id_emp"] for a in analizadas])
        for a in analizadas:
            p, cierre = a["proceso"], a["cierre"]
            existente = existentes.get(p["id_emp"])
            en_curso = existente is not None and not ciclo_vida.es_estado_terminal(existente["estado"])
            if cierre is not None:
                if en_curso and _cerrar(cursor, existente, cierre, p["datos_json"]["url_detalle"]):
                    cerrados.append({"id_emp": p["id_emp"], "proceso_id": existente["id"], "estado": cierre["estado"], "regla": cierre["regla"]})
                else:
                    ignorados_resueltos.append(p["id_emp"])
                continue
            if not p["ambito"]["administrativo"]:
                continue
            if existente is None:
                nuevo_id = _insertar(cursor, organismo_id, fuente_id, p)
                if nuevo_id:
                    _registrar_publicaciones(cursor, nuevo_id, fuente_id, p)
                    insertados.append({"id_emp": p["id_emp"], "proceso_id": nuevo_id, "titulo": p["denominacion"]})
            elif en_curso:
                _refrescar(cursor, int(existente["id"]), p)
                _registrar_publicaciones(cursor, int(existente["id"]), fuente_id, p)
                refrescados.append({"id_emp": p["id_emp"], "proceso_id": existente["id"]})
        connection.commit()

    return {
        **resumen_base, "modo": "APLICADO", "escrituras_bd": True,
        "insertados": insertados, "refrescados": refrescados, "cerrados": cerrados,
        "resueltas_no_insertadas": ignorados_resueltos,
    }
