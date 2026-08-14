import time
from app import cache


def test_memoize_calls_producer_once_within_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"v": 42}

    a = cache.memoize("k1", ttl=60, producer=producer)
    b = cache.memoize("k1", ttl=60, producer=producer)
    assert a == b == {"v": 42}
    assert calls["n"] == 1  # 두 번째는 캐시 히트


def test_memoize_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    cache.memoize("k2", ttl=0, producer=producer)
    time.sleep(0.01)
    cache.memoize("k2", ttl=0, producer=producer)
    assert calls["n"] == 2  # ttl=0 이면 매번 재생성
