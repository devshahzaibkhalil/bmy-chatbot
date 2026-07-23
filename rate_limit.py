"""
rate_limit.py
Rate limiter for the public-facing chat endpoints, to blunt basic
abuse/spam.

Backend selection is automatic:
- If BMY_REDIS_URL is set AND the `redis` package is installed, uses Redis
  (correct choice once you run more than one app process/worker, since an
  in-memory counter is per-process and won't see requests handled by
  other workers).
- Otherwise falls back to an in-memory counter - fine for a single-process
  deployment (which is the default for this project), zero extra setup.
"""

import time
from collections import defaultdict, deque
from functools import wraps

from flask import request, jsonify

from config import Config

_WINDOW_SECONDS = 60
_hits = defaultdict(deque)

_redis_client = None
_redis_checked = False


def _get_redis():
    """Lazily connects to Redis on first use. Returns None (and stays None)
    if BMY_REDIS_URL isn't set or the redis package/server isn't available -
    callers fall back to the in-memory limiter in that case."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True

    if not Config.REDIS_URL:
        return None
    try:
        import redis
        client = redis.from_url(Config.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None  # redis not installed, or server unreachable
    return _redis_client


def _client_key():
    return request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"


def _check_redis(client, key):
    """Fixed-window counter in Redis: INCR + EXPIRE on first hit in the window."""
    redis_key = f"bmy_ratelimit:{key}"
    count = client.incr(redis_key)
    if count == 1:
        client.expire(redis_key, _WINDOW_SECONDS)
    return count <= Config.RATE_LIMIT_PER_MINUTE


def _check_memory(key):
    now = time.time()
    bucket = _hits[key]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= Config.RATE_LIMIT_PER_MINUTE:
        return False
    bucket.append(now)
    return True


def rate_limited(view_func):
    """Caps requests per client IP to Config.RATE_LIMIT_PER_MINUTE per rolling/fixed minute."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        key = _client_key()
        client = _get_redis()

        allowed = _check_redis(client, key) if client else _check_memory(key)

        if not allowed:
            return jsonify({"error": "Too many requests - please slow down and try again shortly."}), 429
        return view_func(*args, **kwargs)
    return wrapped
