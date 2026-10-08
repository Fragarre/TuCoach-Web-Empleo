from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.gva_http import nuevo_cliente_gva

GVA_SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
GVA_BASE_URL = "https://sede.gva.es"
DOGV_HOST = "dogv.gva.es"

CODIGOS_OBJETIVO = {"A1-01", "A2-01", "C1-01", "C2-01"}
PATRONES_ADMIN = (
    r"\bsuperior de administracion\b",
    r"\bcuerpo de gestion\b",
    r"\bcuerpo administrativo\b",
    r"\bcuerpo auxiliar\b",
)
PATRONES_EXCLUSION = (
    "promocion interna",
    "libre designacion",
    "concurso de traslados",
    "curso selectivo",
    "curso de formación selectivo",
    "curso de formacion selectivo",
)
ESTADOS_TERMINALES = (
    "finalizado",
    "finalitzat",
    "cancelado",
    "cancel·lado",
    "cancel·lat",
    "desistido",
    "desistit",
    "anulado",
    "anul·lat",
)
UA = "TuCoach-Empleo-GVA/1.0"


def normalizar(texto: str) -> str:
    return " ".join((texto or "").replace("\xa0", " ").split())


def sin_acentos(texto: str) -> str:
    import unicodedata

    return "".join(
        c
        for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def fecha(texto: str | None) -> date | None:
    if not texto:
        return None
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", texto)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def extraer_url_dogv(soup: BeautifulSoup) -> str | None:
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if DOGV_HOST in href.lower() and ".pdf" in href.lower():
            return urljoin(GVA_BASE_URL, href)
    return None


def extraer_fecha_publicacion(texto: str) -> date | None:
    patrones = (
        r"(?:publicaci[oó]n|publicado)\D{0,80}(\d{2}[-/]\d{2}[-/]\d{4})",
        r"(?:DOGV)\D{0,80}(\d{2}[-/]\d{2}[-/]\d{4})",
    )
    for patron in patrones:
        m = re.search(patron, texto, re.I)
        if m:
            return fecha(m.group(1))
    return None


def extraer_plazas(texto: str) -> int | None:
    for patron in (
        r"(?:n[uú]m\.?\s*de\s*plazas\s*totales|plazas\s*totales|plazas)\s*:?\s*([\d.]+)",
        r"([\d.]+)\s+plazas",
    ):
        m = re.search(patron, texto, re.I)
        if m:
            return int(m.group(1).replace(".", ""))
    return None


def extraer_grupo(texto: str) -> str | None:
    m = re.search(r"\bGrupo\s+([A-Z]\d(?:-\d{2})?)\b", texto, re.I)
    return m.group(1).upper() if m else None


def extraer_codigo_objetivo(texto: str) -> str | None:
    normal = texto.upper()
    encontrados = sorted(c for c in CODIGOS_OBJETIVO if c in normal)
    return encontrados[0] if len(encontrados) == 1 else None


def extraer_plazo(texto: str) -> tuple[date | None, date | None]:
    patrones = (
        r"(?:Plazo|Termini).*?(?:Desde|Des de)\s+(\d{2}[-/]\d{2}[-/]\d{4}).*?(?:hasta|a)\s+(\d{2}[-/]\d{2}[-/]\d{4})",
        r"(?:Desde el|Des del)\s+(\d{2}[-/]\d{2}[-/]\d{4}).*?(?:hasta el|fins al)\s+(\d{2}[-/]\d{2}[-/]\d{4})",
    )
    for patron in patrones:
        m = re.search(patron, texto, re.I)
        if m:
            return fecha(m.group(1)), fecha(m.group(2))
    return None, None


def es_fase_no_activa(texto: str) -> tuple[bool, str | None]:
    normal = sin_acentos(texto)
    for patron in PATRONES_EXCLUSION:
        if patron in normal:
            return True, patron
    for patron in ESTADOS_TERMINALES:
        if patron in normal:
            return True, patron
    return False, None


def parsear_detalle(id_emp: int, url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texto = normalizar(soup.get_text(" ", strip=True))
    h1 = soup.find("h1")
    titulo = normalizar(h1.get_text(" ", strip=True)) if h1 else f"Proceso GVA {id_emp}"

    codigo = extraer_codigo_objetivo(titulo + " " + texto)
    grupo = extraer_grupo(titulo + " " + texto)
    inicio, cierre = extraer_plazo(texto)
    publicacion = extraer_fecha_publicacion(texto)
    dogv_url = extraer_url_dogv(soup)
    inactiva, motivo = es_fase_no_activa(texto)

    return {
        "id_emp": id_emp,
        "identificador_estable": f"GVA:{id_emp}",
        "denominacion": titulo,
        "codigo_puesto": codigo or grupo,
        "grupo": grupo,
        "fecha_publicacion": publicacion,
        "fecha_limite_inscripcion": cierre,
        "fecha_apertura": inicio,
        "plazas": extraer_plazas(texto),
        "url_detalle": url,
        "url_dogv": dogv_url,
        "activa": not inactiva,
        "motivo_inactividad": motivo,
        "tipo_proceso": "Oposicion",
        "turno": "TURNO_LIBRE",
        "contenido_texto": texto,
    }


def descubrir(client: Any, max_paginas: int = 10) -> list[tuple[int, str]]:
    encontrados: dict[int, str] = {}
    for pagina in range(1, max_paginas + 1):
        respuesta = client.get(
            GVA_SEARCH_URL,
            params={
                "pagina": pagina,
                "tipoOrganismo": "1",
                "tamanyoPagina": "30",
            },
        )
        respuesta.raise_for_status()
        soup = BeautifulSoup(respuesta.text, "html.parser")
        enlaces = soup.select('a[href*="detall-ocupacio-publica"]')
        if not enlaces:
            break
        for enlace in enlaces:
            href = str(enlace.get("href") or "")
            m = re.search(r"id_emp=(\d+)", href)
            if m:
                encontrados[int(m.group(1))] = urljoin(GVA_BASE_URL, href)
    return sorted(encontrados.items())


def es_objetivo(registro: dict[str, Any]) -> bool:
    texto = sin_acentos(registro["denominacion"] + " " + registro["contenido_texto"])
    if registro["codigo_puesto"] not in CODIGOS_OBJETIVO:
        return False
    if "oposicion" not in texto:
        return False
    if "turno libre" not in texto:
        return False
    if any(patron in texto for patron in PATRONES_EXCLUSION):
        return False
    return True


def resolver_referencia(cursor: Any, nombre: str, tipo: str, url: str, organismo_id: int | None = None) -> int:
    if organismo_id is None:
        cursor.execute(
            "SELECT id FROM organismos WHERE nombre=%s AND tipo=%s ORDER BY id LIMIT 1",
            (nombre, tipo),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute(
            "INSERT INTO organismos (nombre, tipo) VALUES (%s,%s) RETURNING id",
            (nombre, tipo),
        )
    else:
        cursor.execute("SELECT id FROM organismos WHERE id=%s", (organismo_id,))
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("No se pudo resolver el organismo GVA")
    return row[0]


def resolver_fuente(cursor: Any, organismo_id: int, nombre: str, tipo: str, url: str) -> int:
    cursor.execute(
        "SELECT id FROM fuentes WHERE nombre=%s AND url=%s ORDER BY id LIMIT 1",
        (nombre, url),
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(
        "INSERT INTO fuentes (organismo_id,nombre,tipo,url) VALUES (%s,%s,%s,%s) RETURNING id",
        (organismo_id, nombre, tipo, url),
    )
    return cursor.fetchone()[0]


def guardar(registros: list[dict[str, Any]], aplicar: bool) -> dict[str, int]:
    estadisticas = {"insertados": 0, "actualizados": 0, "publicaciones": 0, "cambios": 0}

    with get_connection() as connection:
        with connection.cursor() as cursor:
            organismo_id = resolver_referencia(
                cursor,
                "Generalitat Valenciana - Administración General",
                "COMUNIDAD_AUTONOMA",
                GVA_SEARCH_URL,
            )
            fuente_id = resolver_fuente(
                cursor,
                organismo_id,
                "Sede GVA - Empleo Público",
                "SEDE_GVA",
                GVA_SEARCH_URL,
            )

            for r in registros:
                cursor.execute(
                    "SELECT * FROM procesos WHERE identificador_estable=%s",
                    (r["identificador_estable"],),
                )
                existente = cursor.fetchone()
                columnas = [d.name for d in cursor.description]
                actual = dict(zip(columnas, existente)) if existente else None

                datos = {
                    "id_emp": r["id_emp"],
                    "codigo_puesto": r["codigo_puesto"],
                    "url_detalle": r["url_detalle"],
                    "url_dogv": r["url_dogv"],
                    "fecha_publicacion": r["fecha_publicacion"].isoformat() if r["fecha_publicacion"] else None,
                    "fecha_limite_inscripcion": r["fecha_limite_inscripcion"].isoformat() if r["fecha_limite_inscripcion"] else None,
                    "criterio_activa": "no_curso_selectivo_ni_resultado_final",
                }

                if actual:
                    campos = {
                        "denominacion": r["denominacion"],
                        "grupo": r["codigo_puesto"],
                        "tipo_proceso": r["tipo_proceso"],
                        "sistema_selectivo": "OPOSICION",
                        "turno": r["turno"],
                        "plazas": r["plazas"],
                        "estado": "EN_SEGUIMIENTO" if r["activa"] else "FINALIZADO",
                        "es_oportunidad": r["activa"],
                        "ambito_administrativo": "SI",
                        "fecha_convocatoria": r["fecha_publicacion"],
                        "fecha_apertura": r["fecha_apertura"],
                        "fecha_cierre": r["fecha_limite_inscripcion"],
                        "ultima_publicacion_at": (
                            datetime.combine(r["fecha_publicacion"], datetime.min.time(), tzinfo=timezone.utc)
                            if r["fecha_publicacion"] else actual.get("ultima_publicacion_at")
                        ),
                        "fuente_principal_id": fuente_id,
                        "datos_json": Jsonb(datos),
                        "updated_at": datetime.now(timezone.utc),
                    }
                    cambios = []
                    for campo, nuevo in campos.items():
                        anterior = actual.get(campo)
                        if campo == "datos_json":
                            anterior_cmp = anterior or {}
                            nuevo_cmp = datos
                        else:
                            anterior_cmp = anterior
                            nuevo_cmp = nuevo
                        if anterior_cmp != nuevo_cmp:
                            cambios.append((campo, anterior_cmp, nuevo_cmp))

                    if cambios:
                        cursor.execute(
                            """UPDATE procesos SET
                               denominacion=%s, grupo=%s, tipo_proceso=%s,
                               sistema_selectivo=%s, turno=%s, plazas=%s,
                               estado=%s, es_oportunidad=%s,
                               ambito_administrativo=%s, fecha_convocatoria=%s,
                               fecha_apertura=%s, fecha_cierre=%s,
                               ultima_publicacion_at=%s, fuente_principal_id=%s,
                               datos_json=%s, updated_at=%s
                               WHERE id=%s""",
                            (
                                campos["denominacion"], campos["grupo"], campos["tipo_proceso"],
                                campos["sistema_selectivo"], campos["turno"], campos["plazas"],
                                campos["estado"], campos["es_oportunidad"],
                                campos["ambito_administrativo"], campos["fecha_convocatoria"],
                                campos["fecha_apertura"], campos["fecha_cierre"],
                                campos["ultima_publicacion_at"], campos["fuente_principal_id"],
                                campos["datos_json"], campos["updated_at"], actual["id"],
                            ),
                        )
                        for campo, anterior, nuevo in cambios:
                            cursor.execute(
                                """INSERT INTO cambios
                                   (proceso_id,tipo,campo,valor_anterior,valor_nuevo,resumen)
                                   VALUES (%s,'ACTUALIZACION',%s,%s,%s,%s)""",
                                (
                                    actual["id"], campo,
                                    str(anterior) if anterior is not None else None,
                                    str(nuevo) if nuevo is not None else None,
                                    f"Actualización GVA de {campo}",
                                ),
                            )
                            estadisticas["cambios"] += 1
                        estadisticas["actualizados"] += 1
                    proceso_id = actual["id"]
                else:
                    cursor.execute(
                        """INSERT INTO procesos
                           (organismo_id,codigo_externo,identificador_estable,denominacion,
                            grupo,tipo_proceso,sistema_selectivo,turno,plazas,estado,
                            es_oportunidad,ambito_administrativo,fecha_convocatoria,
                            fecha_apertura,fecha_cierre,ultima_publicacion_at,
                            fuente_principal_id,datos_json)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           RETURNING id""",
                        (
                            organismo_id, str(r["id_emp"]), r["identificador_estable"],
                            r["denominacion"], r["codigo_puesto"], r["tipo_proceso"],
                            "OPOSICION", r["turno"], r["plazas"],
                            "EN_SEGUIMIENTO" if r["activa"] else "FINALIZADO",
                            r["activa"], "SI", r["fecha_publicacion"], r["fecha_apertura"],
                            r["fecha_limite_inscripcion"],
                            datetime.combine(r["fecha_publicacion"], datetime.min.time(), tzinfo=timezone.utc)
                            if r["fecha_publicacion"] else None,
                            fuente_id, Jsonb(datos),
                        ),
                    )
                    proceso_id = cursor.fetchone()[0]
                    estadisticas["insertados"] += 1

                cursor.execute(
                    "SELECT id FROM publicaciones WHERE proceso_id=%s AND fuente_id=%s AND url=%s LIMIT 1",
                    (proceso_id, fuente_id, r["url_detalle"]),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        """INSERT INTO publicaciones
                           (proceso_id,fuente_id,referencia,tipo,titulo,fecha_publicacion,
                            url,contenido_hash,contenido_texto,datos_json)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            proceso_id, fuente_id, f"GVA:{r['id_emp']}:DETALLE",
                            "DETALLE_CONVOCATORIA", r["denominacion"],
                            r["fecha_publicacion"], r["url_detalle"], None,
                            r["contenido_texto"],
                            Jsonb({
                                "url_detalle": r["url_detalle"],
                                "url_dogv": r["url_dogv"],
                                "codigo_puesto": r["codigo_puesto"],
                            }),
                        ),
                    )
                    estadisticas["publicaciones"] += 1

            if aplicar:
                connection.commit()
            else:
                connection.rollback()

    return estadisticas


def run(*, max_paginas: int = 10, max_detalles: int | None = None, aplicar: bool = False) -> dict[str, Any]:
    descubiertos: list[dict[str, Any]] = []
    rechazados = 0

    with nuevo_cliente_gva(
        timeout=httpx.Timeout(45.0, connect=15.0),
        headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"},
    ) as client:
        candidatos = descubrir(client, max_paginas=max_paginas)
        if max_detalles is not None:
            candidatos = candidatos[:max_detalles]
        for id_emp, url in candidatos:
            respuesta = client.get(url)
            respuesta.raise_for_status()
            registro = parsear_detalle(id_emp, url, respuesta.text)
            if es_objetivo(registro) and registro["activa"]:
                descubiertos.append(registro)
            else:
                rechazados += 1

    persistencia = guardar(descubiertos, aplicar=aplicar)
    return {
        "descubiertos": len(descubiertos),
        "rechazados": rechazados,
        "persistencia": persistencia,
        "modo": "APLICADO" if aplicar else "SOLO_REVISION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Descubre y guarda oposiciones libres A1-01/A2-01/C1-01/C2-01 de GVA.")
    parser.add_argument("--max-paginas", type=int, default=10)
    parser.add_argument("--max-detalles", type=int)
    parser.add_argument("--aplicar", action="store_true", help="Escribe en la BD. Sin esta opción solo hace rollback.")
    args = parser.parse_args()
    resultado = run(
        max_paginas=args.max_paginas,
        max_detalles=args.max_detalles,
        aplicar=args.aplicar,
    )
    print(resultado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
