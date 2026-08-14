import math
from app import metrics


def test_annualize():
    assert metrics.annualize(25.0) == 100.0


def test_per_op_positive():
    # 시총 1000억, 연환산 영업이익 100억 -> PER 10
    assert metrics.per_op(1000.0, 100.0) == 10.0


def test_per_op_negative_op_returns_none():
    assert metrics.per_op(1000.0, -50.0) is None
    assert metrics.per_op(1000.0, 0.0) is None


def test_peer_stats_ignores_none():
    s = metrics.peer_stats([10.0, None, 20.0, 30.0])
    assert s["count"] == 3
    assert s["median"] == 20.0
    assert s["min"] == 10.0 and s["max"] == 30.0


def test_peer_stats_empty():
    s = metrics.peer_stats([None, None])
    assert s["count"] == 0
    assert s["median"] is None


def test_rank_within_lower_is_better():
    r = metrics.rank_within(10.0, [10.0, 20.0, 30.0, 40.0])
    assert r["rank"] == 1 and r["total"] == 4
    assert math.isclose(r["percentile"], 25.0)


def test_rank_within_none_target():
    r = metrics.rank_within(None, [10.0, 20.0])
    assert r["rank"] is None
