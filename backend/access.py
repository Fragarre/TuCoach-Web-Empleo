"""Control de acceso del módulo de empleo.

La identidad y la suscripción se consultan contra el mismo proyecto de Supabase
que utiliza NetExamenes. La tabla public.subscriptions permanece centralizada;
la base de datos de Empleo se reserva para los datos propios del módulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx

from auth import obtener_supabase_public_key, obtener_supabase_url

ESTADOS_CON_ACCESO = {"active", "trialing", "past_due"}

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
    )


def exigir_employment_access(user_id: UUID, access_token: str) -> EmploymentAccess:
    """Exige únicamente que el usuario esté autenticado para Empleo."""
    return obtener_acceso_employment(user_id, access_token)
