"""Only authenticated server proxies may supply browser client addresses (FR-AUTH-4)."""

import hmac
from ipaddress import ip_address

from starlette.requests import Request

from api.utils.settings import settings


def get_client_ip(request: Request) -> str:
    configured = settings.TRUSTED_PROXY_SECRET
    supplied = request.headers.get("x-fmt-proxy-secret", "")
    forwarded = request.headers.get("x-fmt-client-ip")
    if (
        configured is not None
        and hmac.compare_digest(supplied.encode(), configured.get_secret_value().encode())
        and forwarded is not None
    ):
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    return request.client.host if request.client is not None else "unknown"
