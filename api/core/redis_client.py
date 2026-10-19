import redis.asyncio as redis

from api.utils.settings import settings

# Creating the client opens no connection; the first command does.
redis_client: "redis.Redis" = redis.from_url(settings.REDIS_URL, decode_responses=True)
