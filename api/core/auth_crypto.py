"""Authenticated email encryption and domain-separated lookup hashes (FR-AUTH-1)."""

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from email_validator import EmailNotValidError, validate_email

from api.utils.settings import settings


def canonical_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("auth.email_invalid") from None


def _encryption_key() -> bytes:
    key = settings.ENCRYPTION_KEY
    if key is None:
        raise RuntimeError("auth.encryption_key_missing")
    return base64.b64decode(key.get_secret_value(), altchars=b"-_", validate=True)


def keyed_hash(purpose: str, value: bytes) -> bytes:
    key = settings.EMAIL_HASH_KEY
    if key is None:
        raise RuntimeError("auth.hash_key_missing")
    decoded = base64.b64decode(key.get_secret_value(), altchars=b"-_", validate=True)
    return hmac.new(decoded, purpose.encode() + b"\0" + value, hashlib.sha256).digest()


def encrypt_email(value: str) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(_encryption_key()).encrypt(nonce, value.encode(), b"email:v1")


def decrypt_email(value: bytes) -> str:
    return AESGCM(_encryption_key()).decrypt(value[:12], value[12:], b"email:v1").decode()
