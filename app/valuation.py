"""밸류에이션 위치 지표 — 사실 서술 전용, 투자의견이 아니다.

타깃 종목의 PER이 업종 PEER 대비 어디에 위치하는지를 중립적인 사실 문장으로
서술한다. 매수/매도/보유 등 투자 판단 언어는 어디에도 쓰지 않는다.

순수 함수(네트워크·I/O 없음)로 유지해 완전히 단위테스트 가능하게 한다.
"""

_GENERIC_CAVEAT = "PER은 밸류에이션 지표의 하나일 뿐이며, 업종·성장성·재무구조를 함께 봐야 합니다."

_MIN_PEERS = 2  # 이 미만이면 "업종 대비 위치"를 판단하지 않는다(비교 불가)


def _clean(pers):
    return [p for p in (pers or []) if p is not None]


def _level(discount_pct):
    if discount_pct <= -30:
        return "deep_discount", "업종 대비 크게 낮음"
    if discount_pct <= -10:
        return "discount", "업종 대비 낮음"
    if discount_pct < 10:
        return "inline", "업종 평균 수준"
    if discount_pct <= 30:
        return "premium", "업종 대비 높음"
    return "high_premium", "업종 대비 크게 높음"


def _direction_word(discount_pct):
    if discount_pct < 0:
        return "낮은"
    if discount_pct > 0:
        return "높은"
    return "같은"


def _rank_and_total(target_per, peer_pers):
    """peer_pers는 타깃을 제외한 PEER PER 리스트로 가정한다. rank는 저평가(PER 낮음)
    순으로 1부터 매기며, 타깃 자신을 포함해 순위를 매긴다(metrics.rank_within과 동일한
    관례: 값이 낮을수록 rank가 낮다)."""
    vals = _clean(peer_pers)
    total = len(vals) + 1  # 타깃 포함
    rank = sum(1 for v in vals if v < target_per) + 1
    return rank, total


def _build_caveats(ttm_complete, peer_count, margin_delta_pp, has_major_disclosure):
    caveats = []
    if not ttm_complete:
        caveats.append("최근 4개 분기 실적이 모두 확보되지 않아 연환산 추정치입니다.")
    if peer_count is not None and peer_count < 3:
        caveats.append("비교 대상이 %d개로 적어 업종 대표성이 낮습니다." % peer_count)
    if margin_delta_pp is not None and margin_delta_pp < 0:
        caveats.append(
            "영업이익률이 전년 대비 %.1f%%p 하락해 이익의 지속성 확인이 필요합니다."
            % abs(margin_delta_pp)
        )
    if has_major_disclosure:
        caveats.append("최근 증자·계약 등 주요 공시가 있어 실적에 영향을 줄 수 있습니다.")
    caveats.append(_GENERIC_CAVEAT)
    return caveats


def _unavailable(note, caveats):
    return {
        "level": "unavailable",
        "label": "비교 불가",
        "discount_pct": None,
        "median": None,
        "rank": None,
        "total": None,
        "note": note,
        "caveats": caveats,
    }


def assess(target_per, peer_pers, stats, ttm_complete=True, peer_count=None,
           margin_delta_pp=None, has_major_disclosure=False):
    """target_per: 타깃 PER(영업이익 기준, per_op_ttm 우선/없으면 per_op_fwd).
    peer_pers: 타깃을 제외한 PEER PER 리스트(None 섞여 있어도 됨).
    stats: metrics.peer_stats() 결과(median 등)를 담은 dict.

    데이터가 얇으면(target_per 결측, 또는 PER이 있는 peer 2개 미만) 업종 대비 위치를
    판단하지 않고 level="unavailable"을 반환한다 — 근거 없는 저평가/고평가 판정을
    내리지 않기 위함이다.
    """
    valid_peers = _clean(peer_pers)
    if peer_count is None:
        peer_count = len(valid_peers)

    caveats = _build_caveats(ttm_complete, peer_count, margin_delta_pp, has_major_disclosure)

    if target_per is None or len(valid_peers) < _MIN_PEERS:
        return _unavailable(
            "타깃 PER 또는 비교 가능한 업종 PER 데이터가 부족해 업종 대비 위치를 판단할 수 없습니다.",
            caveats,
        )

    median = (stats or {}).get("median")
    if median is None or median == 0:
        return _unavailable(
            "업종 중앙값 PER을 계산할 수 없어 업종 대비 위치를 판단할 수 없습니다.",
            caveats,
        )

    discount_pct = (target_per - median) / median * 100
    level, label = _level(discount_pct)
    rank, total = _rank_and_total(target_per, peer_pers)

    note = "업종 중앙값 %.1f배 대비 %.1f%% %s 수준입니다." % (
        median, discount_pct, _direction_word(discount_pct)
    )

    return {
        "level": level,
        "label": label,
        "discount_pct": discount_pct,
        "median": median,
        "rank": rank,
        "total": total,
        "note": note,
        "caveats": caveats,
    }
