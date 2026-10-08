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
    """Cliente GVA con proxy preferente y fallback directo.

    Desde Render, el acceso directo a la Sede GVA queda bloqueado/colgado.
    Cuando existe configuración de proxy, se utiliza primero para evitar
    esperar el timeout del acceso directo. Si el proxy falla por red/timeout
    o devuelve un estado transitorio, se intenta el acceso directo.
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
        if self._proxy is not None:
            try:
                response = self._proxy.get(url, params=params, **kwargs)
                if response.status_code not in ESTADOS_PROXY:
                    self.used_proxy = True
                    self.proxy_reason = "proxy_preferente"
                    return response
                response.close()
                self.used_proxy = True
                self.proxy_reason = f"proxy_HTTP_{response.status_code}"
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self.used_proxy = True
                self.proxy_reason = f"proxy_{type(exc).__name__}"
        try:
            response = self._direct.get(url, params=params, **kwargs)
            if response.status_code not in ESTADOS_PROXY:
                return response
            if self._proxy is None:
                return response
            response.close()
            self.used_proxy = True
            self.proxy_reason = f"HTTP_{response.status_code}"
            return self._proxy.get(url, params=params, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if self._proxy is None:
                raise
            # Si el proxy ya fue probado arriba, no repetir la petición.
            if self.used_proxy and self.proxy_reason and self.proxy_reason.startswith("proxy_"):
                raise
            raise

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
