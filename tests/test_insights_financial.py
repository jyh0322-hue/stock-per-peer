from app import insights_financial as ifin


def _item(cur_cum, prev_cum, cur_3m=None, prev_3m=None):
    return {"cur_3m": cur_3m, "prev_3m": prev_3m, "cur_cum": cur_cum, "prev_cum": prev_cum}


def _empty_item():
    return {"cur_3m": None, "prev_3m": None, "cur_cum": None, "prev_cum": None}


def _is_none():
    return {
        "매출액": _empty_item(), "매출원가": _empty_item(), "매출총이익": _empty_item(),
        "판매관리비": _empty_item(), "영업이익": _empty_item(), "세전이익": _empty_item(),
        "당기순이익": _empty_item(),
    }


# ---- revenue up + margin down + sga-driven -> headline mentions 판관비 --------

def test_revenue_up_margin_down_sga_driver_headline_and_cost_driver():
    income_statement = {
        "매출액": _item(2459.6, 2043.0),
        "매출원가": _item(1200.0, 1000.0),
        "매출총이익": _item(1259.6, 1043.0),
        "판매관리비": _item(979.5, 486.5),
        "영업이익": _item(280.1, 559.8),
        "세전이익": _item(280.1, 559.8),
        "당기순이익": _item(230.0, 450.0),
    }
    margins = {
        "gross_margin": {"cur": 51.2, "prev": 51.1, "delta": 0.1},
        "op_margin": {"cur": 11.4, "prev": 27.4, "delta": -16.0},
        "net_margin": {"cur": 9.3, "prev": 22.0, "delta": -12.7},
        "sga_ratio": {"cur": 39.8, "prev": 23.8, "delta": 16.0},
    }
    res = ifin.analyze(income_statement, margins, trend=[])

    assert "판관비" in res["headline"]
    assert res["cost_breakdown"]["driver"] == "판관비"

    cost_findings = [f for f in res["findings"] if f["kind"] == "cost"]
    assert len(cost_findings) == 1
    assert "판관비" in cost_findings[0]["text"]
    assert cost_findings[0]["severity"] == "negative"

    revenue_findings = [f for f in res["findings"] if f["kind"] == "revenue"]
    assert revenue_findings[0]["severity"] == "positive"


# ---- revenue down -> negative severity, correct % --------------------------

def test_revenue_down_gives_negative_finding_with_correct_pct():
    income_statement = _is_none()
    income_statement["매출액"] = _item(800.0, 1000.0)  # -20%
    res = ifin.analyze(income_statement, margins=None, trend=[])

    revenue_findings = [f for f in res["findings"] if f["kind"] == "revenue"]
    assert len(revenue_findings) == 1
    f = revenue_findings[0]
    assert f["severity"] == "negative"
    assert f["metric"]["value"] == -20.0
    assert "-20.0%" in f["text"]
    assert "800.0억" in f["text"] and "1,000.0억" in f["text"]


# ---- 영업이익 down / 순이익 up -> quality finding ---------------------------

def test_quality_finding_when_op_down_but_net_up():
    income_statement = _is_none()
    income_statement["영업이익"] = _item(100.0, 150.0)  # down
    income_statement["당기순이익"] = _item(200.0, 120.0)  # up
    res = ifin.analyze(income_statement, margins=None, trend=[])

    quality = [f for f in res["findings"] if f["kind"] == "quality"]
    assert len(quality) == 1
    assert "영업이익은 감소했으나 순이익은 증가" in quality[0]["text"]
    assert quality[0]["severity"] == "neutral"


def test_quality_finding_when_op_up_but_net_down():
    income_statement = _is_none()
    income_statement["영업이익"] = _item(150.0, 100.0)  # up
    income_statement["당기순이익"] = _item(90.0, 120.0)  # down
    res = ifin.analyze(income_statement, margins=None, trend=[])

    quality = [f for f in res["findings"] if f["kind"] == "quality"]
    assert len(quality) == 1
    assert "영업이익은 증가했으나 순이익은 감소" in quality[0]["text"]


def test_no_quality_finding_when_both_move_same_direction():
    income_statement = _is_none()
    income_statement["영업이익"] = _item(150.0, 100.0)  # up
    income_statement["당기순이익"] = _item(140.0, 100.0)  # up
    res = ifin.analyze(income_statement, margins=None, trend=[])
    assert [f for f in res["findings"] if f["kind"] == "quality"] == []


# ---- trend: 5 points -> CAGR finding; 1 point -> no crash, no finding ------

def test_trend_with_five_points_gives_cagr_finding():
    trend = [
        {"year": 2022, "revenue": 1000.0, "operating_income": 182.0, "net_income": 150.0, "op_margin": 18.2},
        {"year": 2023, "revenue": 1200.0, "operating_income": 200.0, "net_income": 160.0, "op_margin": 16.7},
        {"year": 2024, "revenue": 1500.0, "operating_income": 210.0, "net_income": 170.0, "op_margin": 14.0},
        {"year": 2025, "revenue": 1800.0, "operating_income": 220.0, "net_income": 175.0, "op_margin": 12.2},
        {"year": 2026, "revenue": 2459.6, "operating_income": 280.1, "net_income": 230.0, "op_margin": 11.4},
    ]
    res = ifin.analyze(income_statement=None, margins=None, trend=trend)
    trend_findings = [f for f in res["findings"] if f["kind"] == "trend"]
    assert len(trend_findings) == 1
    assert "CAGR" in trend_findings[0]["text"]
    assert "2022년" in trend_findings[0]["text"] and "2026년" in trend_findings[0]["text"]
    assert trend_findings[0]["severity"] == "negative"  # op_margin trending down


def test_trend_with_one_point_no_finding_no_crash():
    trend = [{"year": 2026, "revenue": 2459.6, "operating_income": 280.1,
              "net_income": 230.0, "op_margin": 11.4}]
    res = ifin.analyze(income_statement=None, margins=None, trend=trend)
    assert [f for f in res["findings"] if f["kind"] == "trend"] == []


def test_trend_empty_list_no_crash():
    res = ifin.analyze(income_statement=None, margins=None, trend=[])
    assert [f for f in res["findings"] if f["kind"] == "trend"] == []


# ---- all-None income statement -> empty findings, no exception ------------

def test_all_none_income_statement_returns_empty_findings_no_exception():
    res = ifin.analyze(_is_none(), margins=None, trend=[])
    assert res["findings"] == []
    assert res["headline"] == ""
    assert res["cost_breakdown"] == {"cogs_ratio_delta_pp": None, "sga_ratio_delta_pp": None, "driver": None}


def test_none_inputs_do_not_raise():
    res = ifin.analyze(None, None, None)
    assert res["findings"] == []
    assert isinstance(res["headline"], str)


# ---- no buy/sell/hold language anywhere ------------------------------------

_FORBIDDEN_WORDS = ["매수", "매도", "보유", "sell", "buy", "hold"]


def test_no_advice_language_anywhere():
    income_statement = {
        "매출액": _item(2459.6, 2043.0),
        "매출원가": _item(1200.0, 1000.0),
        "매출총이익": _item(1259.6, 1043.0),
        "판매관리비": _item(979.5, 486.5),
        "영업이익": _item(280.1, 559.8),
        "세전이익": _item(280.1, 559.8),
        "당기순이익": _item(230.0, 450.0),
    }
    margins = {
        "gross_margin": {"cur": 51.2, "prev": 51.1, "delta": 0.1},
        "op_margin": {"cur": 11.4, "prev": 27.4, "delta": -16.0},
        "net_margin": {"cur": 9.3, "prev": 22.0, "delta": -12.7},
        "sga_ratio": {"cur": 39.8, "prev": 23.8, "delta": 16.0},
    }
    trend = [
        {"year": 2022, "revenue": 1000.0, "operating_income": 182.0, "net_income": 150.0, "op_margin": 18.2},
        {"year": 2026, "revenue": 2459.6, "operating_income": 280.1, "net_income": 230.0, "op_margin": 11.4},
    ]
    res = ifin.analyze(income_statement, margins, trend)
    blob = res["headline"] + " ".join(f["text"] for f in res["findings"])
    blob = blob.lower()
    for w in _FORBIDDEN_WORDS:
        assert w.lower() not in blob
