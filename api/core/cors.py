"""Keep CORS rejection responses within the shared error contract (TR-8)."""

from uuid import uuid4

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from api.core.errors import error_response
from api.utils.logger import request_id_var


class ErrorCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code < 400:
            return response
        request_id = request_id_var.get()
        if request_id is None:
            request_id = uuid4().hex
        # Preserve the browser's CORS policy headers, replacing only the
        # plain-text body's content headers.
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["X-Request-ID"] = request_id
        return error_response(request_id, 400, "bad_request", headers=headers)
