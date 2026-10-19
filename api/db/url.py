"""Database URL normalization shared by the app and Alembic."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ASYNCPG_SSL_MODES = frozenset(
    {
        "disable",
        "allow",
        "prefer",
        "require",
        "verify-ca",
        "verify-full",
    }
)


def build_async_url(url: str) -> tuple[str, str | None]:
    """Return an asyncpg SQLAlchemy URL and its exact ``sslmode`` value.

    ``asyncpg`` accepts PostgreSQL's SSL modes through its ``ssl`` connection
    argument. SQLAlchemy must therefore not forward ``sslmode`` in the URL as
    a separate keyword. ``channel_binding`` is removed for the same reason: it
    is accepted by libpq-style URLs but is not an asyncpg connection option.

    All unrelated query pairs are retained, including repeated keys and blank
    values.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("Database URL must be a non-empty string")

    parsed = urlsplit(url)
    if parsed.scheme in {"postgres", "postgresql"}:
        parsed = parsed._replace(scheme="postgresql+asyncpg")
    elif parsed.scheme != "postgresql+asyncpg":
        raise ValueError("Database URL must use postgres, postgresql, or postgresql+asyncpg")

    ssl_modes: list[str] = []
    retained_params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "sslmode":
            ssl_modes.append(value)
        elif key != "channel_binding":
            retained_params.append((key, value))

    if len(ssl_modes) > 1:
        raise ValueError("Database URL must contain at most one sslmode value")

    ssl_mode = ssl_modes[0] if ssl_modes else None
    if ssl_mode is not None and ssl_mode not in ASYNCPG_SSL_MODES:
        allowed = ", ".join(sorted(ASYNCPG_SSL_MODES))
        raise ValueError(f"Unsupported PostgreSQL sslmode {ssl_mode!r}; use {allowed}")

    clean_query = urlencode(retained_params, doseq=True)
    clean_url = urlunsplit(parsed._replace(query=clean_query))
    return clean_url, ssl_mode
