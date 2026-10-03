from typing import Any

from .database import get_connection
from .estado_proceso import estado_inscripcion


TIPOS_EXCLUIDOS = (
    "Promoción interna",
    "Libre designación",
    "Concurso general de méritos",
    "Concurso de traslados",
    "Comisiones de servicio",
    "Difícil cobertura",
    "Anuncio difícil cobertura",
    "Acto único telemático",
)

PATRONES_TITULO_EXCLUIDOS = (
    "promoción interna", "promocion interna", "promoció interna", "promocio interna",
    "concurso de traslados", "concurso de traslado", "libre designación", "libre designacion",
    "comisiones de servicio", "comissions de servei", "acto único telemático",
    "acto unico telematico", "acte unic telematic", "acte únic telemàtic",
    "concurs de mèrits per a la provisió", "concurs de merits per a la provisio",
    "concurso de méritos para la provisión", "concurso de meritos para la provision",
    "concurs de mèrits per a cobrir", "concurs de merits per a cobrir",
    "concurso de méritos para cubrir", "concurso de meritos para cubrir",
)

ESTADOS_TERMINALES = (
    "finalizado", "finalitzado", "finalitzat",
    "cancelado", "cancel·lado", "cancel·lat",
    "desistido", "desistit", "anulado", "anul·lat",
)

TIPOS_EMPLEO_PRIVADO = {
    "bolsa de trabajo",
    "difícil cobertura",
    "anuncio difícil cobertura",
    "anuncio difícil cobertura (adc)",
}


def es_proceso_privado(proceso: dict[str, Any]) -> bool:
    tipo = str(proceso.get("tipo_proceso") or "").strip().lower()
    datos = proceso.get("datos_json") or {}
    categoria = str(datos.get("categoria_gva") or "").strip().upper() if isinstance(datos, dict) else ""
    return tipo in TIPOS_EMPLEO_PRIVADO or categoria in {"BOLSA", "ADC"}


def _condiciones_catalogo() -> tuple[str, list[Any]]:
    placeholders_tipo = ", ".join(["%s"] * len(TIPOS_EXCLUIDOS))
    placeholders_estado = ", ".join(["%s"] * len(ESTADOS_TERMINALES))
    condiciones = ["p.es_oportunidad = TRUE", "p.ambito_administrativo = 'SI'"]
    condiciones.append(f"COALESCE(p.tipo_proceso, '') NOT IN ({placeholders_tipo})")
    condiciones.append("COALESCE(UPPER(p.turno), '') <> 'PROMOCION_INTERNA'")
    # El cierre de inscripción no finaliza el proceso selectivo. Solo se oculta
    # cuando una fuente oficial acredita un estado terminal del proceso.
    condiciones.append(f"COALESCE(LOWER(p.estado), '') NOT IN ({placeholders_estado})")
    params: list[Any] = list(TIPOS_EXCLUIDOS) + list(ESTADOS_TERMINALES)
    for patron in PATRONES_TITULO_EXCLUIDOS:
        condiciones.append("POSITION(%s IN LOWER(COALESCE(p.denominacion, ''))) = 0")
        params.append(patron)
    return " AND ".join(condiciones), params


SELECT_FIELDS = """
       p.id, p.organismo_id, o.nombre AS organismo_nombre,
       p.codigo_externo, p.identificador_estable, p.denominacion,
       p.cuerpo_escala, p.grupo, p.subgrupo, p.tipo_proceso,
       p.sistema_selectivo, p.turno, p.plazas, p.estado,
       p.es_oportunidad, p.ambito_administrativo,
       p.coaching_disponible, p.coaching_convocatoria_id,
       p.anio_oep, p.anio_convocatoria, p.fecha_convocatoria,
       p.fecha_apertura, p.fecha_cierre, p.fecha_examen,
       p.lugar_examen, p.ultima_publicacion_at,
       p.fuente_principal_id, p.datos_json,
       (SELECT UPPER(COALESCE(f.tipo, '')) FROM fuentes f WHERE f.id = p.fuente_principal_id) AS fuente_principal_tipo,
       (SELECT MAX(pub_boe.fecha_publicacion) FROM publicaciones pub_boe JOIN fuentes f_boe ON f_boe.id=pub_boe.fuente_id WHERE pub_boe.proceso_id=p.id AND UPPER(COALESCE(f_boe.tipo,''))='BOE') AS fecha_boe_publicacion,
       COALESCE(
           NULLIF(TRIM(COALESCE(p.datos_json->>'url_detalle','')), ''),
           NULLIF(TRIM(COALESCE(p.datos_json->>'url_oficial','')), ''),
           (
               SELECT pub.url FROM publicaciones pub
               WHERE pub.proceso_id = p.id AND pub.url IS NOT NULL AND TRIM(pub.url) <> ''
               ORDER BY CASE
                   WHEN UPPER(TRIM(COALESCE(pub.tipo, ''))) = 'BASES' THEN 0
                   WHEN UPPER(TRIM(COALESCE(pub.tipo, ''))) = 'CONVOCATORIA' THEN 1
                   WHEN LOWER(COALESCE(pub.tipo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 2
                   WHEN LOWER(COALESCE(pub.titulo, '')) LIKE CONCAT('%%', 'convoc', '%%') THEN 3
                   WHEN LOWER(pub.url) LIKE '%%bop.dival.es%%' THEN 4
                   WHEN LOWER(pub.url) LIKE '%%boe.es%%' THEN 6
                   ELSE 5 END,
                 pub.fecha_publicacion ASC NULLS LAST, pub.id ASC
               LIMIT 1
           )
       ) AS url_oficial
"""


def _enriquecer_proceso(fila: dict[str, Any]) -> dict[str, Any]:
    enriquecida = dict(fila)
    datos = dict(enriquecida.get("datos_json") or {})
    if not datos.get("origen") and str(enriquecida.get("fuente_principal_tipo") or "").upper() == "BOP":
        datos["origen"] = "BOP_VALENCIA_MUNICIPAL"
    enriquecida["datos_json"] = datos
    inscripcion = estado_inscripcion(enriquecida)
    enriquecida["estado_inscripcion"] = inscripcion["codigo"]
    enriquecida["inscripcion"] = inscripcion
    # ABIERTO era un estado histórico que mezclaba plazo de solicitud y ciclo
    # selectivo. De cara a la API se normaliza a EN_CURSO sin alterar la BD.
    if str(enriquecida.get("estado") or "").upper() == "ABIERTO":
        enriquecida["estado"] = "EN_CURSO"
    return enriquecida


def listar_procesos(*, organismo_id: int | None = None, estado: str | None = None, limite: int = 100, incluir_privados: bool = False) -> list[dict[str, Any]]:
    """Lista oportunidades administrativas cuyo proceso selectivo sigue activo."""
    limite = max(1, min(limite, 200))
    catalogo_sql, params = _condiciones_catalogo()
    query = f"SELECT {SELECT_FIELDS} FROM procesos p JOIN organismos o ON o.id=p.organismo_id WHERE {catalogo_sql}"
    if organismo_id is not None:
        query += " AND p.organismo_id = %s"; params.append(organismo_id)
    if estado is not None:
        query += " AND p.estado = %s"; params.append(estado)
    query += " ORDER BY COALESCE(p.fecha_examen,p.fecha_convocatoria,p.fecha_apertura) DESC NULLS LAST,p.id DESC LIMIT %s"
    params.append(limite)
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(query, tuple(params)); rows=cursor.fetchall(); columns=[d.name for d in cursor.description]
    procesos = [_enriquecer_proceso(dict(zip(columns,row))) for row in rows]
    if not incluir_privados:
        procesos = [proceso for proceso in procesos if not es_proceso_privado(proceso)]
    return procesos


def obtener_proceso(proceso_id: int) -> dict[str, Any] | None:
    """Devuelve el detalle incluso al finalizar, para no romper Mi seguimiento."""
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {SELECT_FIELDS} FROM procesos p JOIN organismos o ON o.id=p.organismo_id WHERE p.id=%s AND p.es_oportunidad=TRUE AND p.ambito_administrativo='SI'",
            (proceso_id,),
        )
        row=cursor.fetchone()
        if row is None: return None
        columns=[d.name for d in cursor.description]
    return _enriquecer_proceso(dict(zip(columns,row)))
