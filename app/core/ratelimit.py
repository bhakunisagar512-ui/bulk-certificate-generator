"""Fixed-window rate limiting backed by Redis. Fails open (with a warning) if Redis is down,
so a cache outage never takes the API with it."""

import logging
from collections.abc import Callable
from functools import lru_cache

import redis
from fastapi import Request

from app.config import get_settings
from app.core.errors import RateLimited

log = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1, decode_responses=True
    )


def hit(bucket: str, key: str, limit: int, window_seconds: int = 60) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    name = f"rl:{bucket}:{key}"
    try:
        r = get_redis()
        count = r.incr(name)
        if count == 1:
            r.expire(name, window_seconds)
        if count > limit:
            raise RateLimited("Too many requests, slow down")
    except redis.RedisError:
        log.warning("rate limiter unavailable, allowing request")


def client_ip(request: Request) -> str:
    # Only the direct peer is trusted. Behind a reverse proxy, configure uvicorn's
    # --proxy-headers with --forwarded-allow-ips instead of reading X-Forwarded-For here.
    return request.client.host if request.client else "unknown"


def limit_by_ip(bucket: str, limit_getter: Callable[[], int]) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        hit(bucket, client_ip(request), limit_getter())

    return dependency
