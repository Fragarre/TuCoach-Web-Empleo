from __future__ import annotations

import io
import os
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import psycopg
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pypdf import PdfReader

from auth import UsuarioAutenticado, usuario_actual
from .database import get_connection

REVISION_ESTADOS = ("PENDIENTE_REVISION", "PUBLICADA", "DESCARTADA")
ORIGENES = ("AUTOMATICO", "MANUAL")
TEMARIO_ESTADOS = ("PENDIENTE_REVISION", "VERIFICADO", "DESCARTADO")
TEMARIO_ORIGENES = ("AUTOMATICO", "MANUAL")

router = APIRouter(prefix="/admin/gestion", tags=["admin-empleo"])


class ProcesoAdminRequest(BaseModel):
    organismo_id: int
    denominacion: str = Field(min_length=1)
    codigo_externo: str | None = None
    identificador_estable: str | None = None
    cuerpo_escala: str | None = None
    grupo: str | None = None
    subgrupo: str | None = None
    tipo_proceso: str | None = None
    sistema_selectivo: str | None = None
    turno: str | None = None
    plazas: int | None = Field(default=None, ge=0)
    estado: str = "EN_CURSO"
    anio_oep: int | None = None
    anio_convocatoria: int | None = None
    fecha_convocatoria: str | None = None
    fecha_apertura: str | None = None
    fecha_cierre: str | None = None
    fecha_examen: str | None = None
    lugar_examen: str | None = None
    fuente_principal_id: int | None = None
    datos_json: dict[str, Any] | None = None
    es_oportunidad: bool = True
    revision_estado: str = "PENDIENTE_REVISION"
    observaciones_internas: str | None = None


class ProcesoManualAltaRequest(ProcesoAdminRequest):
    """Alta manual limitada al ámbito cubierto por este producto.

    El identificador del organismo se resuelve en el servidor: el cliente no puede
    asociar una convocatoria manual a un organismo arbitrario ya existente.
    """

    organismo_id: int | None = None
    administracion: Literal["GENERALITAT", "DIPUTACION", "AYUNTAMIENTO"]
    provincia: Literal["Alicante", "Castellón", "Valencia"] | None = None
    municipio: str | None = Field(default=None, max_length=120)


class RevisionRequest(BaseModel):
    estado: str
    observaciones: str | None = None


class CorreccionManualRequest(BaseModel):
    """Campos que un administrador puede corregir sin reescribir la fuente automática."""

    grupo: str | None = None
    subgrupo: str | None = None
    cuerpo_escala: str | None = None
    fecha_apertura: str | None = None
    fecha_cierre: str | None = None
    evidencia_url: str = Field(min_length=8)
    observaciones_internas: str | None = None


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


def _admin_empleo(usuario: UsuarioAutenticado = Depends(usuario_actual)) -> UsuarioAutenticado:
    """Autoriza Empleo contra el registro administrativo central de Tu Coach."""
    database_url = os.getenv("TUCOACH_DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(
            status_code=503,
            detail="Autorización administrativa central no configurada.",
        )

    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT activo
                    FROM public.admin_users
                    WHERE user_id = %s
                    LIMIT 1
                    """,
                    (usuario.id,),
                )
                admin = cursor.fetchone()
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="No se ha podido verificar la autorización administrativa.",
        ) from exc

    if not admin or not bool(admin["activo"]):
        raise HTTPException(status_code=403, detail="No autorizado para la gestión de Empleo.")
    return usuario


def listar_pendientes_revision(limite: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT p.id, p.organismo_id, o.nombre AS organismo_nombre,
                   p.codigo_externo, p.denominacion, p.grupo, p.tipo_proceso,
                   p.sistema_selectivo, p.turno, p.plazas, p.estado,
                   p.origen_dato, p.revision_estado, p.fecha_apertura,
                   p.fecha_cierre, p.fecha_examen, p.updated_at
            FROM procesos p
            LEFT JOIN organismos o ON o.id = p.organismo_id
            WHERE p.revision_estado = 'PENDIENTE_REVISION'
            ORDER BY p.updated_at DESC
            LIMIT %s
            """,
            (limite,),
        )
        return cursor.fetchall()


def actualizar_revision(
    proceso_id: int,
    estado: str,
    usuario_id: str | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id FROM procesos WHERE id=%s", (proceso_id,))
        if cursor.fetchone() is None:
            raise ValueError("Proceso no encontrado")
        cursor.execute(
            """
            UPDATE procesos
            SET revision_estado=%s,
                revisado_at=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN NOW() ELSE revisado_at END,
                revisado_por=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN %s::uuid ELSE revisado_por END,
                observaciones_internas=COALESCE(%s, observaciones_internas),
                updated_at=NOW()
            WHERE id=%s
            RETURNING id, revision_estado, revisado_at, revisado_por, observaciones_internas
            """,
            (estado, estado, estado, usuario_id, observaciones, proceso_id),
        )
        return cursor.fetchone()


def guardar_temario(
    proceso_id: int,
    contenido_texto: str,
    origen: str = "MANUAL",
    estado: str = "PENDIENTE_REVISION",
    fuente_url: str | None = None,
    fuente_publicacion_id: int | None = None,
    observaciones: str | None = None,
) -> dict[str, Any]:
    if origen not in TEMARIO_ORIGENES:
        raise ValueError("Origen de temario no válido")
    if estado not in TEMARIO_ESTADOS:
        raise ValueError("Estado de temario no válido")
    if not contenido_texto.strip():
        raise ValueError("El temario no puede estar vacío")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO temarios_empleo
              (proceso_id, contenido_texto, origen, estado, fuente_url,
               fuente_publicacion_id, fecha_extraccion, observaciones, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
            ON CONFLICT (proceso_id) DO UPDATE SET
              contenido_texto=EXCLUDED.contenido_texto,
              origen=EXCLUDED.origen,
              estado=EXCLUDED.estado,
              fuente_url=EXCLUDED.fuente_url,
              fuente_publicacion_id=EXCLUDED.fuente_publicacion_id,
              fecha_extraccion=NOW(),
              observaciones=EXCLUDED.observaciones,
              updated_at=NOW()
            RETURNING *
            """,
            (proceso_id, contenido_texto.strip(), origen, estado, fuente_url,
             fuente_publicacion_id, observaciones),
        )
        return cursor.fetchone()


def obtener_temario(proceso_id: int) -> dict[str, Any] | None:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM temarios_empleo WHERE proceso_id=%s", (proceso_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        temario = row
        cursor.execute(
            "SELECT id, numero, titulo, contenido_texto, orden FROM temas_empleo WHERE temario_id=%s ORDER BY orden",
            (temario["id"],),
        )
        temario["temas"] = cursor.fetchall()
        return temario


def guardar_temas(proceso_id: int, temas: list[dict[str, Any]]) -> dict[str, Any]:
    temario = obtener_temario(proceso_id)
    if temario is None:
        raise ValueError("Primero debe existir el temario")
    normalizados = []
    for i, tema in enumerate(temas, start=1):
        contenido = str(tema.get("contenido_texto") or "").strip()
        if not contenido:
            raise ValueError(f"El tema {i} no puede estar vacío")
        normalizados.append((tema.get("numero"), tema.get("titulo"), contenido, i))
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("DELETE FROM temas_empleo WHERE temario_id=%s", (temario["id"],))
        for numero, titulo, contenido, orden in normalizados:
            cursor.execute(
                "INSERT INTO temas_empleo(temario_id,numero,titulo,contenido_texto,orden) VALUES(%s,%s,%s,%s,%s)",
                (temario["id"], numero, titulo, contenido, orden),
            )
    return obtener_temario(proceso_id) or temario


def _row_proceso(cursor, proceso_id: int) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT p.*, o.nombre AS organismo_nombre
        FROM procesos p LEFT JOIN organismos o ON o.id=p.organismo_id
        WHERE p.id=%s
        """,
        (proceso_id,),
    )
    row = cursor.fetchone()
    return row if row else None


_DIPUTACIONES = {
    "Alicante": "Diputación Provincial de Alicante",
    "Castellón": "Diputación Provincial de Castellón",
    "Valencia": "Diputación Provincial de Valencia",
}


def _nombre_ayuntamiento(municipio: str) -> str:
    limpio = " ".join(municipio.split()).strip()
    if len(limpio) < 2:
        raise ValueError("Indica el municipio del ayuntamiento")
    if any(caracter.isdigit() for caracter in limpio):
        raise ValueError("El municipio no puede contener números")
    return f"Ayuntamiento de {limpio}"


def _resolver_organismo_manual(cursor, payload: ProcesoManualAltaRequest) -> int:
    """Resuelve o crea únicamente los organismos admitidos en el alta manual.

    Los ayuntamientos se introducen con provincia para evitar ambigüedades y se
    crean como pendientes de revisión junto con su primera convocatoria manual.
    """
    if payload.administracion == "GENERALITAT":
        if payload.provincia or payload.municipio:
            raise ValueError("La Generalitat Valenciana no requiere provincia ni municipio")
        nombre, tipo, provincia, municipio = (
            "Generalitat Valenciana", "ADMINISTRACION_AUTONOMICA", None, None,
        )
    elif payload.administracion == "DIPUTACION":
        if payload.provincia not in _DIPUTACIONES or payload.municipio:
            raise ValueError("Selecciona una de las tres provincias para la diputación")
        nombre, tipo, provincia, municipio = (
            _DIPUTACIONES[payload.provincia], "DIPUTACION", payload.provincia, None,
        )
    else:
        if payload.provincia not in _DIPUTACIONES:
            raise ValueError("Selecciona la provincia del ayuntamiento")
        if not payload.municipio:
            raise ValueError("Indica el municipio del ayuntamiento")
        nombre, tipo, provincia, municipio = (
            _nombre_ayuntamiento(payload.municipio), "AYUNTAMIENTO", payload.provincia,
            " ".join(payload.municipio.split()).strip(),
        )

    cursor.execute(
        """
        SELECT id FROM organismos
        WHERE tipo=%s
          AND COALESCE(provincia, '')=COALESCE(%s, '')
          AND (
            LOWER(nombre)=LOWER(%s)
            OR (%s IS NOT NULL AND LOWER(COALESCE(municipio, ''))=LOWER(%s))
          )
        ORDER BY id ASC
        LIMIT 1
        """,
        (tipo, provincia, nombre, municipio, municipio),
    )
    existente = cursor.fetchone()
    if existente:
        return int(existente["id"])

    cursor.execute(
        """
        INSERT INTO organismos (nombre, tipo, provincia, municipio, activo, created_at, updated_at)
        VALUES (%s,%s,%s,%s,TRUE,NOW(),NOW())
        RETURNING id
        """,
        (nombre, tipo, provincia, municipio),
    )
    return int(cursor.fetchone()["id"])


def crear_proceso_manual_alta(payload: ProcesoManualAltaRequest, usuario: UsuarioAutenticado) -> dict[str, Any]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        organismo_id = _resolver_organismo_manual(cursor, payload)
    # La conexión anterior confirma la posible creación del organismo antes de
    # insertar el proceso mediante la rutina común, que abre su propia conexión.
    return crear_proceso_manual(
        payload.model_copy(update={"organismo_id": organismo_id, "revision_estado": "PENDIENTE_REVISION"}),
        usuario,
    )


def crear_proceso_manual(payload: ProcesoAdminRequest, usuario: UsuarioAutenticado) -> dict[str, Any]:
    if payload.revision_estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id FROM organismos WHERE id=%s", (payload.organismo_id,))
        if cursor.fetchone() is None:
            raise ValueError("Organismo no encontrado")
        cursor.execute(
            """
            INSERT INTO procesos (
              organismo_id,codigo_externo,identificador_estable,denominacion,cuerpo_escala,
              grupo,subgrupo,tipo_proceso,sistema_selectivo,turno,plazas,estado,anio_oep,
              anio_convocatoria,fecha_convocatoria,fecha_apertura,fecha_cierre,fecha_examen,
              lugar_examen,fuente_principal_id,datos_json,es_oportunidad,origen_dato,
              revision_estado,revisado_at,revisado_por,observaciones_internas,updated_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL',%s,NULL,NULL,%s,NOW()
            ) RETURNING id
            """,
            (
                payload.organismo_id,payload.codigo_externo,payload.identificador_estable,payload.denominacion,
                payload.cuerpo_escala,payload.grupo,payload.subgrupo,payload.tipo_proceso,payload.sistema_selectivo,
                payload.turno,payload.plazas,payload.estado,payload.anio_oep,payload.anio_convocatoria,
                payload.fecha_convocatoria,payload.fecha_apertura,payload.fecha_cierre,payload.fecha_examen,
                payload.lugar_examen,payload.fuente_principal_id,Jsonb(payload.datos_json or {}),payload.es_oportunidad,
                payload.revision_estado,payload.observaciones_internas,
            ),
        )
        proceso_id = cursor.fetchone()["id"]
        if payload.revision_estado in ("PUBLICADA", "DESCARTADA"):
            cursor.execute(
                "UPDATE procesos SET revisado_at=NOW(), revisado_por=%s::uuid WHERE id=%s",
                (str(usuario.id), proceso_id),
            )
        return _row_proceso(cursor, proceso_id) or {"id": proceso_id}


def actualizar_proceso_manual(proceso_id: int, payload: ProcesoAdminRequest, usuario: UsuarioAutenticado) -> dict[str, Any]:
    if payload.revision_estado not in REVISION_ESTADOS:
        raise ValueError("Estado de revisión no válido")
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id, origen_dato FROM procesos WHERE id=%s", (proceso_id,))
        proceso = cursor.fetchone()
        if proceso is None:
            raise ValueError("Proceso no encontrado")
        if proceso["origen_dato"] != "MANUAL":
            raise ValueError("La edición completa solo está disponible para convocatorias creadas manualmente")
        cursor.execute(
            """
            UPDATE procesos SET organismo_id=%s,codigo_externo=%s,identificador_estable=%s,denominacion=%s,
              cuerpo_escala=%s,grupo=%s,subgrupo=%s,tipo_proceso=%s,sistema_selectivo=%s,turno=%s,plazas=%s,
              estado=%s,anio_oep=%s,anio_convocatoria=%s,fecha_convocatoria=%s,fecha_apertura=%s,fecha_cierre=%s,
              fecha_examen=%s,lugar_examen=%s,fuente_principal_id=%s,datos_json=%s,es_oportunidad=%s,
              origen_dato='MANUAL',revision_estado=%s,
              revisado_at=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN NOW() ELSE revisado_at END,
              revisado_por=CASE WHEN %s IN ('PUBLICADA','DESCARTADA') THEN %s::uuid ELSE revisado_por END,
              observaciones_internas=%s,updated_at=NOW()
            WHERE id=%s
            """,
            (
                payload.organismo_id,payload.codigo_externo,payload.identificador_estable,payload.denominacion,
                payload.cuerpo_escala,payload.grupo,payload.subgrupo,payload.tipo_proceso,payload.sistema_selectivo,
                payload.turno,payload.plazas,payload.estado,payload.anio_oep,payload.anio_convocatoria,
                payload.fecha_convocatoria,payload.fecha_apertura,payload.fecha_cierre,payload.fecha_examen,
                payload.lugar_examen,payload.fuente_principal_id,Jsonb(payload.datos_json or {}),payload.es_oportunidad,
                payload.revision_estado,payload.revision_estado,payload.revision_estado,str(usuario.id),
                payload.observaciones_internas,proceso_id,
            ),
        )
        return _row_proceso(cursor, proceso_id) or {"id": proceso_id}


def _texto_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = valor.strip()
    return texto or None


def _validar_evidencia(url: str) -> str:
    evidencia = url.strip()
    parsed = urlparse(evidencia)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Indica un enlace oficial válido como evidencia")
    return evidencia


def _validar_clasificacion(grupo: str | None, subgrupo: str | None, escala: str | None) -> tuple[str | None, str | None, str | None]:
    grupo = _texto_opcional(grupo)
    subgrupo = _texto_opcional(subgrupo)
    escala = _texto_opcional(escala)
    if grupo:
        grupo = grupo.upper()
        if grupo not in {"A", "B", "C", "D", "E"}:
            raise ValueError("Grupo no válido")
    if subgrupo:
        subgrupo = subgrupo.upper()
        if subgrupo not in {"A1", "A2", "B", "C1", "C2", "D", "E"}:
            raise ValueError("Subgrupo no válido")
        if grupo and subgrupo[0] != grupo:
            raise ValueError("El grupo y el subgrupo no son coherentes")
        grupo = grupo or subgrupo[0]
    if escala:
        escalas = {
            "administracion general": "Administración General",
            "administración general": "Administración General",
            "administracion especial": "Administración Especial",
            "administración especial": "Administración Especial",
        }
        normalizada = escalas.get(escala.casefold())
        if normalizada is None:
            raise ValueError("La escala debe ser Administración General o Administración Especial")
        escala = normalizada
    return grupo, subgrupo, escala


def corregir_proceso_manual(
    proceso_id: int,
    payload: CorreccionManualRequest,
    usuario: UsuarioAutenticado,
) -> dict[str, Any]:
    """Registra una corrección humana puntual, sin modificar la fuente ni el origen."""
    evidencia = _validar_evidencia(payload.evidencia_url)
    grupo, subgrupo, escala = _validar_clasificacion(payload.grupo, payload.subgrupo, payload.cuerpo_escala)
    apertura = _texto_opcional(payload.fecha_apertura)
    cierre = _texto_opcional(payload.fecha_cierre)
    if (apertura is None) != (cierre is None):
        raise ValueError("Indica conjuntamente la fecha de apertura y la de cierre")
    if apertura and cierre:
        try:
            if date.fromisoformat(apertura) > date.fromisoformat(cierre):
                raise ValueError("La fecha de cierre no puede ser anterior a la apertura")
        except ValueError as exc:
            if str(exc).startswith("La fecha"):
                raise
            raise ValueError("Las fechas deben tener formato AAAA-MM-DD") from exc

    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, grupo, subgrupo, cuerpo_escala, fecha_apertura, fecha_cierre
            FROM procesos WHERE id=%s FOR UPDATE
            """,
            (proceso_id,),
        )
        anterior = cursor.fetchone()
        if anterior is None:
            raise ValueError("Proceso no encontrado")

        campos = {
            "grupo": grupo,
            "subgrupo": subgrupo,
            "cuerpo_escala": escala,
            "fecha_apertura": apertura,
            "fecha_cierre": cierre,
        }
        cambios = [
            (campo, anterior[campo], valor)
            for campo, valor in campos.items()
            if (None if anterior[campo] is None else str(anterior[campo])) != valor
        ]
        if not cambios:
            raise ValueError("No hay cambios que guardar")

        cursor.execute(
            """
            UPDATE procesos
            SET grupo=%s, subgrupo=%s, cuerpo_escala=%s,
                fecha_apertura=%s, fecha_cierre=%s,
                revisado_at=NOW(), revisado_por=%s::uuid,
                observaciones_internas=COALESCE(%s, observaciones_internas), updated_at=NOW()
            WHERE id=%s
            """,
            (grupo, subgrupo, escala, apertura, cierre, str(usuario.id), _texto_opcional(payload.observaciones_internas), proceso_id),
        )
        resumen = f"Corrección manual verificada. Evidencia: {evidencia}"
        for campo, valor_anterior, valor_nuevo in cambios:
            cursor.execute(
                """
                INSERT INTO cambios (proceso_id, tipo, campo, valor_anterior, valor_nuevo, resumen, significativo, detectado_at)
                VALUES (%s, 'CORRECCION_MANUAL', %s, %s, %s, %s, FALSE, NOW())
                """,
                (proceso_id, campo, None if valor_anterior is None else str(valor_anterior), None if valor_nuevo is None else str(valor_nuevo), resumen),
            )
        resultado = _row_proceso(cursor, proceso_id) or {"id": proceso_id}
        resultado["campos_corregidos"] = [campo for campo, _, _ in cambios]
        return resultado


def _normalizar_texto(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    lineas = []
    for linea in texto.split("\n"):
        linea = re.sub(r"[ \t]+", " ", linea).strip()
        if linea:
            lineas.append(linea)
    return "\n".join(lineas)


def _extraer_texto_fuente(url: str) -> tuple[str, str]:
    headers = {
        "User-Agent": "NetReto-Empleo/0.1 (https://netexamenes.com)",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    with httpx.Client(timeout=45, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        contenido = response.content
        if "application/pdf" in content_type or contenido[:4] == b"%PDF":
            reader = PdfReader(io.BytesIO(contenido))
            paginas = []
            for pagina in reader.pages:
                paginas.append(pagina.extract_text() or "")
            return _normalizar_texto("\n".join(paginas)), str(response.url)
        soup = BeautifulSoup(contenido, "html.parser")
        for elemento in soup(["script", "style", "noscript"]):
            elemento.decompose()
        return _normalizar_texto(soup.get_text("\n")), str(response.url)


def _extraer_bloque_temario(texto: str) -> str | None:
    """Busca el apartado oficial sin reinterpretar su contenido."""
    lineas = texto.splitlines()
    patrones_inicio = (
        r"^\s*(?:ANEXO\s+[^\n]{0,80}\s*)?(?:TEMARIO|PROGRAMA|PROGRAMA DE MATERIAS|MATERIAS)\s*:?.*$",
        r"^\s*(?:ANEXO\s+[^\n]{0,80}\s*)?(?:TEMARIO|PROGRAMA|MATERIAS)\s*$",
    )
    inicio = None
    for i, linea in enumerate(lineas):
        if any(re.match(p, linea, re.I) for p in patrones_inicio):
            inicio = i
            break
    if inicio is None:
        # Segunda oportunidad para PDFs con títulos pegados al cuerpo.
        m = re.search(r"(?im)\b(?:TEMARIO|PROGRAMA DE MATERIAS|PROGRAMA|MATERIAS)\b", texto)
        if not m:
            return None
        inicio = texto[:m.start()].count("\n")

    fin = len(lineas)
    patrones_fin = (
        r"^\s*(?:ANEXO|BASES|PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|SEPTIMA|OCTAVA|NOVENA|DÉCIMA|DECIMA)\b",
        r"^\s*(?:SOLICITUDES|TRIBUNAL|COMISIÓN DE SELECCIÓN|COMISION DE SELECCION|RECURSOS)\b",
    )
    for j in range(inicio + 1, len(lineas)):
        if any(re.match(p, lineas[j], re.I) for p in patrones_fin):
            if j - inicio >= 3:
                fin = j
                break
    bloque = "\n".join(lineas[inicio:fin]).strip()
    if len(bloque) < 80:
        return None
    return bloque


def _puntuacion_fuente(pub: dict[str, Any]) -> int:
    tipo = str(pub.get("tipo") or "").lower()
    titulo = str(pub.get("titulo") or "").lower()
    url = str(pub.get("url") or "").lower()
    score = 0
    if tipo in {"convocatoria", "bases"}:
        score += 100
    if "convoc" in tipo:
        score += 60
    if "base" in tipo or "bases" in titulo:
        score += 50
    if "convoc" in titulo:
        score += 30
    if "boe.es" in url:
        score += 20
    if "bop" in url:
        score += 15
    if "tribunal" in titulo or "admit" in titulo or "resultado" in titulo or "nombr" in titulo:
        score -= 100
    return score


def extraer_temario_oficial(proceso_id: int) -> dict[str, Any]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id, denominacion FROM procesos WHERE id=%s", (proceso_id,))
        proceso = cursor.fetchone()
        if proceso is None:
            raise ValueError("Proceso no encontrado")
        cursor.execute(
            """
            SELECT id, referencia, tipo, titulo, fecha_publicacion, url
            FROM publicaciones
            WHERE proceso_id=%s AND url IS NOT NULL AND TRIM(url) <> ''
            ORDER BY fecha_publicacion DESC NULLS LAST, id DESC
            """,
            (proceso_id,),
        )
        publicaciones = cursor.fetchall()

    candidatas = sorted(publicaciones, key=_puntuacion_fuente, reverse=True)
    errores: list[str] = []
    for pub in candidatas[:12]:
        try:
            texto, url_final = _extraer_texto_fuente(str(pub["url"]))
            bloque = _extraer_bloque_temario(texto)
            if bloque:
                return {
                    "proceso_id": proceso_id,
                    "denominacion": proceso["denominacion"],
                    "contenido_texto": bloque,
                    "fuente_url": url_final,
                    "fuente_publicacion_id": pub["id"],
                    "fuente_referencia": pub["referencia"],
                    "fuente_titulo": pub["titulo"],
                    "caracteres_fuente": len(texto),
                }
        except Exception as exc:
            errores.append(f"{pub['id']}: {exc}")

    detalle = "; ".join(errores[-4:])
    raise ValueError(
        "No se ha localizado automáticamente un apartado de temario en las publicaciones oficiales disponibles."
        + (f" Detalles: {detalle}" if detalle else "")
    )


@router.get("/me")
def admin_me(usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    return {"id": str(usuario.id), "email": usuario.email, "admin": True}


@router.get("/pendientes")
def admin_pendientes(limite: int = 100, _: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    return listar_pendientes_revision(limite=limite)


@router.get("/organismos")
def admin_organismos(_: UsuarioAutenticado = Depends(_admin_empleo)) -> list[dict[str, Any]]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT id, nombre FROM organismos ORDER BY nombre ASC")
        return cursor.fetchall()


@router.get("/procesos/{proceso_id}")
def admin_proceso(proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    with get_connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        row = _row_proceso(cursor, proceso_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return row


@router.post("/procesos")
def admin_crear_proceso(payload: ProcesoAdminRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return crear_proceso_manual(payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/procesos/alta-manual")
def admin_crear_proceso_alta_manual(
    payload: ProcesoManualAltaRequest,
    usuario: UsuarioAutenticado = Depends(_admin_empleo),
) -> dict[str, Any]:
    try:
        return crear_proceso_manual_alta(payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/procesos/{proceso_id}")
def admin_actualizar_proceso(proceso_id: int, payload: ProcesoAdminRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return actualizar_proceso_manual(proceso_id, payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/procesos/{proceso_id}/correccion-manual")
def admin_corregir_proceso(
    proceso_id: int,
    payload: CorreccionManualRequest,
    usuario: UsuarioAutenticado = Depends(_admin_empleo),
) -> dict[str, Any]:
    try:
        return corregir_proceso_manual(proceso_id, payload, usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/procesos/{proceso_id}/revision")
def admin_revision(proceso_id: int, payload: RevisionRequest, usuario: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return actualizar_revision(proceso_id, payload.estado, usuario_id=str(usuario.id), observaciones=payload.observaciones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/procesos/{proceso_id}/temario")
def admin_temario(proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    return obtener_temario(proceso_id) or {"proceso_id": proceso_id, "temario": None}


@router.post("/procesos/{proceso_id}/temario/extraer")
def admin_extraer_temario(proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return extraer_temario_oficial(proceso_id)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "Proceso no encontrado" in str(exc) else 422, detail=str(exc)) from exc


@router.put("/procesos/{proceso_id}/temario")
def admin_guardar_temario(payload: TemarioRequest, proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return guardar_temario(proceso_id, payload.contenido_texto, origen=payload.origen, estado=payload.estado,
                               fuente_url=payload.fuente_url, fuente_publicacion_id=payload.fuente_publicacion_id,
                               observaciones=payload.observaciones)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/procesos/{proceso_id}/temario/temas")
def admin_guardar_temas(payload: TemasRequest, proceso_id: int, _: UsuarioAutenticado = Depends(_admin_empleo)) -> dict[str, Any]:
    try:
        return guardar_temas(proceso_id, [x.model_dump() for x in payload.temas])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
