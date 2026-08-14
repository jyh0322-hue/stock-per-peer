import os
import json
import time
from collections import OrderedDict

from app import config

CACHE_DIR = config.CACHE_DIR
# key -> (expires_at, value). OrderedDict + 상한으로 LRU 축출을 한다.
# 무제한이면 검색할수록 재무 DataFrame records 가 계속 쌓여 512MB 급
# 소형 인스턴스에서 OOM 으로 죽는다.
_mem = OrderedDict()
MEM_MAX_ENTRIES = getattr(config, "MEM_CACHE_MAX_ENTRIES", 200)


def _path(key):
    safe = key.replace(":", "_").replace("/", "_")
    return os.path.join(CACHE_DIR, safe + ".json")


def _remember(key, expires_at, value):
    """메모리 캐시에 저장하고 상한을 넘으면 가장 오래된 항목부터 버린다."""
    if key in _mem:
        del _mem[key]
    _mem[key] = (expires_at, value)
    while len(_mem) > MEM_MAX_ENTRIES:
        _mem.popitem(last=False)  # LRU: 가장 오래 참조되지 않은 것부터


def _touch(key):
    """조회된 항목을 최근 사용으로 이동(LRU 갱신)."""
    if key in _mem:
        _mem.move_to_end(key)


def day_key(prefix):
    return prefix + ":" + time.strftime("%Y%m%d")


def memoize(key, ttl, producer):
    now = time.time()
    hit = _mem.get(key)
    if hit and hit[0] > now:
        _touch(key)
        return hit[1]
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _path(key)
    if ttl > 0 and os.path.exists(p) and (now - os.path.getmtime(p)) < ttl:
        try:
            with open(p, "r", encoding="utf-8") as f:
                value = json.load(f)
            _remember(key, now + ttl, value)
            return value
        except Exception:
            pass
    value = producer()
    _remember(key, now + ttl, value)
    if ttl > 0:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except (TypeError, ValueError):
            pass  # JSON 직렬화 불가한 값은 메모리 캐시만
    return value


def peek(key):
    """캐시에 유효한 값이 있으면 반환, 없으면 None. (TTL은 저장 시점에 결정됨)"""
    now = time.time()
    hit = _mem.get(key)
    if hit and hit[0] > now:
        _touch(key)
        return hit[1]
    return None


def put(key, value, ttl):
    """값을 메모리+디스크에 ttl 로 저장."""
    now = time.time()
    _remember(key, now + ttl, value)
    if ttl > 0:
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            with open(_path(key), "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except (TypeError, ValueError):
            pass

