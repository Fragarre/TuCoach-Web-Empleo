"""Control de acceso del módulo de empleo.

La identidad y la suscripción se consultan contra el mismo proyecto de Supabase
que utiliza NetExamenes. La tabla public.subscriptions permanece centralizada;
la base de datos de Empleo se reserva para los datos propios del módulo.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from uuid import UUID

import httpx

from auth import obtener_supabase_public_key, obtener_supabase_url

ESTADOS_CON_ACCESO = {"active", "trialing", "past_due"}

# Acceso temporal a Bolsas de trabajo y anuncios de difícil cobertura.
# Se usan UUID de Supabase para que la autorización no dependa del correo.
PRIVATE_EMPLOYMENT_SEPARATED = os.getenv("EMPLOYMENT_PRIVATE_CATEGORIES", "true").strip().lower() == "true"

PRIVATE_EMPLOYMENT_USER_IDS = {
    UUID("335c6064-b23a-4215-829f-782e955e484d"),
    UUID("d2091861-f10d-4800-897c-c67c0da2bf68"),
}

def es_categoria_empleo_privada(tipo_proceso: str | None, categoria_gva: str | None) -> bool:
    """Clasifica Bolsas/ADC como categoría temporalmente restringida."""
    tipo = str(tipo_proceso or "").strip().lower()
    categoria = str(categoria_gva or "").strip().upper()
    return tipo in {
        "bolsa de trabajo",
        "difícil cobertura",
        "anuncio difícil cobertura",
        "anuncio difícil cobertura (adc)",
    } or categoria in {"BOLSA", "ADC"}


def puede_acceder_categoria_privada(user_id: UUID) -> bool:
    """Interruptor único para retirar en el futuro la separación comercial."""
    return not PRIVATE_EMPLOYMENT_SEPARATED or user_id in PRIVATE_EMPLOYMENT_USER_IDS


_supabase_http = httpx.Client(
    timeout=10.0,
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=30.0,
    ),
)


@dataclass(frozen=True)
class EmploymentAccess:
    user_id: UUID
    authenticated: bool
    subscribed: bool
    employment_access: bool
    private_employment: bool


def obtener_acceso_employment(user_id: UUID, access_token: str) -> EmploymentAccess:
    """Devuelve el estado del usuario en Empleo.

    El módulo Empleo está abierto a cualquier usuario autenticado. La suscripción
    central se sigue consultando para conservar el estado comercial disponible en
    ``subscribed``, pero ya no condiciona el acceso al módulo.
    """
    url = (
        f"{obtener_supabase_url()}/rest/v1/subscriptions"
        "?select=status"
        f"&user_id=eq.{user_id}"
        "&proveedor=eq.STRIPE"
        "&order=updated_at.desc,id.desc"
        "&limit=1"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": obtener_supabase_public_key(),
    }

    subscribed = False
    try:
        respuesta = _supabase_http.get(url, headers=headers)
        if respuesta.status_code == 200:
            datos = respuesta.json()
            fila = datos[0] if isinstance(datos, list) and datos else None
            subscribed = bool(fila and fila.get("status") in ESTADOS_CON_ACCESO)

        # TuCoach considera el acceso total interno equivalente a una
        # suscripción activa. Empleo debe aplicar el mismo criterio.
        if not subscribed:
            admin_url = (
                f"{obtener_supabase_url()}/rest/v1/admin_users"
                "?select=activo,acceso_total"
                f"&user_id=eq.{user_id}"
                "&activo=eq.true"
                "&acceso_total=eq.true"
                "&limit=1"
            )
            admin_respuesta = _supabase_http.get(admin_url, headers=headers)
            if admin_respuesta.status_code == 200:
                admins = admin_respuesta.json()
                subscribed = bool(isinstance(admins, list) and admins)
    except (httpx.HTTPError, ValueError):
        # El estado comercial no debe impedir el acceso general a Empleo.
        # El seguimiento sí continúa exigiendo subscribed=True.
        subscribed = False

    return EmploymentAccess(
        user_id=user_id,
        authenticated=True,
        subscribed=subscribed,
        employment_access=True,
        private_employment=puede_acceder_categoria_privada(user_id),
    )


def exigir_employment_access(user_id: UUID, access_token: str) -> EmploymentAccess:
    """Exige únicamente que el usuario esté autenticado para Empleo."""
    return obtener_acceso_employment(user_id, access_token)
