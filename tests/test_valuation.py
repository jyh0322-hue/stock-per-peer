from app import valuation


def _stats(median):
    return {"median": median, "min": None, "max": None, "count": None}


# ---- 등급 임계값 -----------------------------------------------------

def test_deep_discount_level():
    r = valuation.assess(7.0, [10.0, 10.0], _stats(10.0))
    assert r["level"] == "deep_discount"
    assert r["label"] == "업종 대비 크게 낮음"


def test_discount_level_boundary_minus_10():
    r = valuation.assess(9.0, [10.0, 10.0], _stats(10.0))  # discount_pct == -10.0
    assert r["level"] == "discount"
    assert r["label"] == "업종 대비 낮음"


def test_inline_level():
    r = valuation.assess(10.0, [10.0, 10.0], _stats(10.0))  # discount_pct == 0
    assert r["level"] == "inline"
    assert r["label"] == "업종 평균 수준"


def test_inline_level_just_under_plus10():
    r = valuation.assess(10.9, [10.0, 10.0], _stats(10.0))  # discount_pct == 9.0
    assert r["level"] == "inline"


def test_premium_level_boundary_10():
    r = valuation.assess(11.0, [10.0, 10.0], _stats(10.0))  # discount_pct == 10.0
    assert r["level"] == "premium"
    assert r["label"] == "업종 대비 높음"


def test_premium_level_boundary_30():
    r = valuation.assess(13.0, [10.0, 10.0], _stats(10.0))  # discount_pct == 30.0
    assert r["level"] == "premium"


def test_high_premium_level():
    r = valuation.assess(14.0, [10.0, 10.0], _stats(10.0))  # discount_pct == 40.0
    assert r["level"] == "high_premium"
    assert r["label"] == "업종 대비 크게 높음"


# ---- 비교 불가 --------------------------------------------------------

def test_unavailable_when_target_per_none():
    r = valuation.assess(None, [10.0, 12.0], _stats(11.0))
    assert r["level"] == "unavailable"
    assert r["label"] == "비교 불가"
    assert r["discount_pct"] is None
    assert r["median"] is None
    assert r["rank"] is None
    assert r["total"] is None


def test_unavailable_when_fewer_than_two_peers_have_per():
    r = valuation.assess(10.0, [12.0], _stats(12.0))
    assert r["level"] == "unavailable"


def test_unavailable_when_peer_pers_all_none():
    r = valuation.assess(10.0, [None, None], _stats(None))
    assert r["level"] == "unavailable"


# ---- caveats -----------------------------------------------------------

def test_caveat_ttm_incomplete_fires():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), ttm_complete=False)
    assert any("연환산 추정치" in c for c in r["caveats"])


def test_caveat_ttm_complete_does_not_fire():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), ttm_complete=True)
    assert not any("연환산 추정치" in c for c in r["caveats"])


def test_caveat_thin_peer_count_fires_under_three():
    r = valuation.assess(10.0, [10.0, 10.0], _stats(10.0), peer_count=2)
    assert any("비교 대상이 2개로 적어" in c for c in r["caveats"])


def test_caveat_thin_peer_count_does_not_fire_at_three():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), peer_count=3)
    assert not any("업종 대표성이 낮습니다" in c for c in r["caveats"])


def test_caveat_margin_decline_fires():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), margin_delta_pp=-3.2)
    assert any("3.2%p 하락" in c for c in r["caveats"])


def test_caveat_margin_improvement_does_not_fire():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), margin_delta_pp=1.5)
    assert not any("하락" in c for c in r["caveats"])


def test_caveat_major_disclosure_fires():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), has_major_disclosure=True)
    assert any("주요 공시" in c for c in r["caveats"])


def test_caveat_major_disclosure_absent_does_not_fire():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0), has_major_disclosure=False)
    assert not any("주요 공시" in c for c in r["caveats"])


def test_generic_caveat_always_present():
    r = valuation.assess(10.0, [10.0, 10.0, 10.0], _stats(10.0))
    assert any("밸류에이션 지표의 하나" in c for c in r["caveats"])


def test_caveats_populated_even_when_unavailable():
    r = valuation.assess(None, [10.0], _stats(None), ttm_complete=False)
    assert any("연환산 추정치" in c for c in r["caveats"])
    assert any("밸류에이션 지표의 하나" in c for c in r["caveats"])


# ---- note ---------------------------------------------------------------

def test_note_contains_median_and_pct():
    r = valuation.assess(6.8, [8.9, 8.9, 9.0], _stats(8.9))
    assert "8.9" in r["note"]
    pct = round((6.8 - 8.9) / 8.9 * 100, 1)
    assert ("%.1f" % pct) in r["note"]
    assert "낮은" in r["note"]


def test_note_says_higher_for_premium():
    r = valuation.assess(14.0, [10.0, 10.0], _stats(10.0))
    assert "높은" in r["note"]


# ---- rank/total ----------------------------------------------------------

def test_rank_and_total_reflect_position_among_peers_plus_target():
    r = valuation.assess(15.0, [25.0, 6.0], _stats(15.5))
    # peers cheaper than target: only 6.0 -> rank = 1(count) + 1 = 2, total = 2 peers + target = 3
    assert r["rank"] == 2
    assert r["total"] == 3


# ---- no buy/sell/hold language anywhere ----------------------------------

_FORBIDDEN_WORDS = ["매수", "매도", "보유", "sell", "buy", "hold"]


def test_no_advice_language_in_any_output():
    for target, peers, med in [
        (7.0, [10.0, 10.0], 10.0),
        (14.0, [10.0, 10.0], 10.0),
        (None, [10.0], None),
    ]:
        r = valuation.assess(target, peers, _stats(med))
        blob = (r["note"] + " ".join(r["caveats"]) + r["label"]).lower()
        for w in _FORBIDDEN_WORDS:
            assert w.lower() not in blob
