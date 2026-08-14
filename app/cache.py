import os
import json
import time

from app import config

CACHE_DIR = config.CACHE_DIR
_mem = {}  # key -> (expires_at, value)


def _path(key):
    safe = key.replace(":", "_").replace("/", "_")
    return os.path.join(CACHE_DIR, safe + ".json")


def day_key(prefix):
    return prefix + ":" + time.strftime("%Y%m%d")


def memoize(key, ttl, producer):
    now = time.time()
    hit = _mem.get(key)
    if hit and hit[0] > now:
        return hit[1]
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _path(key)
    if ttl > 0 and os.path.exists(p) and (now - os.path.getmtime(p)) < ttl:
        try:
            with open(p, "r", encoding="utf-8") as f:
                value = json.load(f)
            _mem[key] = (now + ttl, value)
            return value
        except Exception:
            pass
    value = producer()
    _mem[key] = (now + ttl, value)
    if ttl > 0:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except (TypeError, ValueError):
            pass  # JSON 직렬화 불가한 값은 메모리 캐시만
    return value
