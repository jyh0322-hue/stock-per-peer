from statistics import median
from typing import List, Optional


def annualize(op_3m):
    return op_3m * 4


def per_op(market_cap, op_annualized):
    if op_annualized is None or op_annualized <= 0:
        return None
    if market_cap is None:
        return None
    return market_cap / op_annualized


def _clean(pers):
    return [p for p in pers if p is not None]


def peer_stats(pers):
    vals = _clean(pers)
    if not vals:
        return {"median": None, "min": None, "max": None, "count": 0}
    return {"median": float(median(vals)), "min": min(vals), "max": max(vals), "count": len(vals)}


def rank_within(target_per, peer_pers):
    vals = _clean(peer_pers)
    if target_per is None or not vals:
        return {"rank": None, "total": len(vals), "percentile": None}
    universe = sorted(vals)  # target_per 는 vals 에 이미 포함된다고 가정
    rank = sum(1 for v in universe if v < target_per) + 1
    total = len(universe)
    percentile = (rank / total) * 100 if total else None
    return {"rank": rank, "total": total, "percentile": percentile}


def margin(numer, denom):
    if not denom or numer is None:
        return None
    return numer / denom * 100
