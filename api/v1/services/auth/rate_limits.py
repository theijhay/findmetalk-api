"""Atomic sliding windows shared by all API instances (FR-AUTH-4)."""

import math
from typing import Literal
from uuid import uuid4

from api.core.auth_crypto import keyed_hash
from api.core.redis_client import redis_client
from api.utils.settings import settings

# Both dimensions are checked before either is reserved; one request cannot
# race another instance past a threshold or partially consume the other cap.
_RESERVE = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local window = tonumber(ARGV[1])
local retry = 0
for i = 1, 2 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now - window)
    if redis.call('ZCARD', KEYS[i]) >= tonumber(ARGV[i + 1]) then
        local oldest = redis.call('ZRANGE', KEYS[i], 0, 0, 'WITHSCORES')
        retry = math.max(retry, tonumber(oldest[2]) + window - now)
    end
end
if retry > 0 then return retry end
for i = 1, 2 do
    redis.call('ZADD', KEYS[i], now, ARGV[4])
    redis.call('PEXPIRE', KEYS[i], window)
end
return 0
"""


async def _reserve(
    kind: Literal["send", "verify"], email_hash: bytes, client_ip: str, email_limit: int, ip_limit: int
) -> int:
    result = await redis_client.eval(
        _RESERVE,
        2,
        f"ratelimit:otp:{kind}:email:{email_hash.hex()}",
        f"ratelimit:otp:{kind}:ip:{keyed_hash('ip', client_ip.encode()).hex()}",
        3600 * 1000,
        email_limit,
        ip_limit,
        uuid4().hex,
    )
    return max(0, math.ceil(int(result) / 1000))


async def reserve_send(email_hash: bytes, client_ip: str) -> int:
    return await _reserve(
        "send", email_hash, client_ip, settings.OTP_EMAIL_LIMIT_PER_HOUR, settings.OTP_IP_LIMIT_PER_HOUR
    )


async def reserve_verification(email_hash: bytes, client_ip: str) -> int:
    return await _reserve(
        "verify",
        email_hash,
        client_ip,
        settings.OTP_VERIFY_PER_EMAIL_PER_HOUR,
        settings.OTP_VERIFY_PER_IP_PER_HOUR,
    )
