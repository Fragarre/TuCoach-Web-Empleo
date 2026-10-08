from __future__ import annotations

import os
from urllib.parse import quote

import httpx


GVA_PROXY_ENV = "GVA_PROXY_URL"
GVA_PROXY_USER_ENV = "GVA_PROXY_USER"
GVA_PROXY_PASSWORD_ENV = "GVA_PROXY_PASSWORD"

# Estados que justifican intentar de nuevo por el proxy español.
# Un 404, por ejemplo, es un problema de recurso y no de procedencia de IP.
ESTADOS_PROXY = {403, 429, 500, 502, 503, 504}


def _proxy_url() -> str | None:
    url = os.getenv(GVA_PROXY_ENV)
    user = os.getenv(GVA_PROXY_USER_ENV)
    password = os.getenv(GVA_PROXY_PASSWORD_ENV)
    if not any((url, user, password)):
        return None
    if not (url and user and password):
        raise RuntimeError("Configuración incompleta del proxy GVA")
    return url.replace(
        "://",
        f"://{quote(user, safe='')}:{quote(password, safe='')}@",
        1,
    )


class GVAResilientClient:
    """Cliente GVA directo con fallback selectivo al proxy español.

    El tráfico normal sale directamente. El proxy solo se utiliza cuando
    el acceso directo falla por red/timeout o devuelve un estado compatible
    con bloqueo o indisponibilidad transitoria.
    """

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | int | float = 45.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._direct = httpx.Client(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )
        self._proxy = None
        proxy = _proxy_url()
        if proxy:
            self._proxy = httpx.Client(
                timeout=timeout,
                headers=headers,
                follow_redirects=True,
                proxy=proxy,
            )
        self.used_proxy = False
        self.proxy_reason: str | None = None

    def get(self, url: str, *, params: dict | None = None, **kwargs):
        try:
            response = self._direct.get(url, params=params, **kwargs)
            if response.status_code not in ESTADOS_PROXY:
                return response
            if self._proxy is None:
                return response
            self.used_proxy = True
            self.proxy_reason = f"HTTP {response.status_code}"
            response.close()
            return self._proxy.get(url, params=params, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if self._proxy is None:
                raise
            self.used_proxy = True
            self.proxy_reason = type(exc).__name__
            return self._proxy.get(url, params=params, **kwargs)

    def close(self) -> None:
        self._direct.close()
        if self._proxy is not None:
            self._proxy.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def nuevo_cliente_gva(
    *,
    timeout: httpx.Timeout | int | float = 45.0,
    headers: dict[str, str] | None = None,
) -> GVAResilientClient:
    return GVAResilientClient(timeout=timeout, headers=headers)
