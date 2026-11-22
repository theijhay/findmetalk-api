"""FR-AUTH-1, FR-AUTH-4: email privacy and trusted client addresses."""

import base64
import logging
import secrets
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

from api.core.auth_crypto import canonical_email, decrypt_email, encrypt_email, keyed_hash
from api.core.client_ip import get_client_ip
from api.utils.settings import Settings, settings
from api.v1.schemas.auth.auth import CodeRequest, VerifyRequest
from api.v1.services.auth.email import ConsoleEmailSender


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Ada.Lovelace+Talk@EXAMPLE.COM  ", "ada.lovelace+talk@example.com"),
        ("ADA@xn--bcher-kva.de", "ada@bücher.de"),
    ],
)
def test_email_canonicalization_preserves_dots_and_tags(value: str, expected: str) -> None:
    """FR-AUTH-1: the same canonical address drives validation, lookup and encrypted storage."""
    assert canonical_email(value) == expected
    assert CodeRequest(email=value).email == expected
    assert keyed_hash("email", canonical_email(value).encode()) == keyed_hash("email", expected.encode())


@pytest.mark.parametrize("value", ["not-an-email", "a@", "a\n@example.com", "a b@example.com"])
def test_invalid_email_has_a_translatable_validation_key(value: str) -> None:
    """FR-AUTH-1: invalid addresses use a stable field error without validator prose."""
    with pytest.raises(ValidationError) as error:
        CodeRequest(email=value)
    assert error.value.errors()[0]["type"] == "auth.email_invalid"


@pytest.mark.parametrize(
    "value", [123456, "12345", "1234567", "abcdef", "１２３４５６", "123456\n", " 123456"]
)
def test_codes_require_six_ascii_digits(value: Any) -> None:
    """FR-AUTH-1: malformed verification input is refused before accessing storage."""
    with pytest.raises(ValidationError):
        VerifyRequest.model_validate({"email": "ada@example.com", "code": value})


def test_email_encryption_is_randomized_authenticated_and_reversible() -> None:
    """FR-AUTH-1: ciphertext hides addresses and rejects modified data."""
    email = "ada@example.com"
    first, second = encrypt_email(email), encrypt_email(email)
    assert first != second
    assert email.encode() not in first
    assert decrypt_email(first) == email
    damaged = first[:-1] + bytes([first[-1] ^ 1])
    with pytest.raises(InvalidTag):
        decrypt_email(damaged)


def test_lookup_hashes_are_keyed_and_domain_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-AUTH-1, FR-AUTH-4: identifiers in storage cannot be compared across purposes or keys."""
    value = b"ada@example.com"
    first = keyed_hash("email", value)
    assert len(first) == 32
    assert first != keyed_hash("ip", value)
    monkeypatch.setattr(
        settings, "EMAIL_HASH_KEY", SecretStr(base64.b64encode(secrets.token_bytes(32)).decode())
    )
    assert first != keyed_hash("email", value)


@pytest.mark.parametrize("proxy_case", ["matching", "wrong", "missing", "unset"])
def test_only_an_authenticated_proxy_can_select_the_client_ip(
    proxy_case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-4: unauthenticated forwarded headers cannot evade a per-IP send cap."""
    configured = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setattr(
        settings, "TRUSTED_PROXY_SECRET", None if proxy_case == "unset" else SecretStr(configured)
    )
    headers = [(b"x-fmt-client-ip", b"2001:db8::1"), (b"x-forwarded-for", b"192.0.2.99")]
    if proxy_case != "missing":
        supplied = configured if proxy_case in {"matching", "unset"} else secrets.token_urlsafe(32)
        headers.append((b"x-fmt-proxy-secret", supplied.encode()))
    request = Request({"type": "http", "headers": headers, "client": ("192.0.2.1", 1234)})
    assert get_client_ip(request) == ("2001:db8::1" if proxy_case == "matching" else "192.0.2.1")


def test_authenticated_proxy_with_malformed_ip_falls_back_to_the_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-AUTH-4: even authenticated proxy input must be one valid address."""
    secret = secrets.token_urlsafe(32)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_SECRET", SecretStr(secret))
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-fmt-proxy-secret", secret.encode()),
                (b"x-fmt-client-ip", b"192.0.2.1, 192.0.2.2"),
            ],
            "client": ("192.0.2.3", 1234),
        }
    )
    assert get_client_ip(request) == "192.0.2.3"


@pytest.mark.parametrize("name", ["ENCRYPTION_KEY", "EMAIL_HASH_KEY", "TRUSTED_PROXY_SECRET", "JWT_SECRET"])
def test_invalid_auth_key_settings_are_refused_without_printing_the_value(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1: configuration errors refuse invalid keys without exposing key material."""
    invalid = secrets.token_hex(8)
    monkeypatch.setenv(name, invalid)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert invalid not in str(error.value)


async def test_development_sender_logs_no_address_or_code(caplog: pytest.LogCaptureFixture) -> None:
    """FR-AUTH-1: the development email adapter logs only the delivery outcome."""
    email, code = "ada@example.com", f"{secrets.randbelow(1000000):06d}"
    with caplog.at_level(logging.INFO, logger="findmetalk"):
        await ConsoleEmailSender().send_code(email=email, code=code)
    assert email not in caplog.text
    assert code not in caplog.text
    assert any(record.getMessage() == "development email delivery" for record in caplog.records)


@pytest.mark.parametrize("name", ["ENCRYPTION_KEY", "EMAIL_HASH_KEY", "JWT_SECRET"])
def test_service_cannot_start_with_missing_authentication_keys(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-AUTH-1: missing runtime keys fail startup before the service can accept sign-in requests."""
    settings.require_auth_keys()
    monkeypatch.setattr(settings, name, None)
    with pytest.raises(ValueError, match=r"auth\.keys_missing"):
        settings.require_auth_keys()
