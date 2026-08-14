import base64

from app import report


def _sample():
    target = {"name": "브이티", "stock_code": "018290", "market_cap": 3000.0,
              "op_3m": 50.0, "op_annualized": 200.0, "per_op": 15.0, "krx_per": 14.0}
    peers = [
        {"name": "브이티", "stock_code": "018290", "market_cap": 3000.0,
         "op_3m": 50.0, "op_annualized": 200.0, "per_op": 15.0, "krx_per": 14.0, "is_target": True},
        {"name": "코스맥스", "stock_code": "192820", "market_cap": 5000.0,
         "op_3m": 100.0, "op_annualized": 400.0, "per_op": 12.5, "krx_per": 12.0, "is_target": False},
    ]
    stats = {"median": 13.75, "min": 12.5, "max": 15.0, "count": 2, "rank": 2, "percentile": 100.0}
    return target, peers, stats


def test_build_result_shape():
    t, p, s = _sample()
    res = report.build_result(t, p, s, disclosures=[], deepdive=None)
    assert res["target"]["per_op"] == 15.0
    assert len(res["peers"]) == 2
    assert res["stats"]["median"] == 13.75
    assert isinstance(res["chart_per_b64"], str)


def test_per_bar_chart_b64_handles_all_none_per():
    peers = [
        {"name": "브이티", "stock_code": "018290", "per_op": None},
        {"name": "코스맥스", "stock_code": "192820", "per_op": None},
    ]
    b64 = report.per_bar_chart_b64(peers, "018290", None)
    assert isinstance(b64, str)
    assert len(b64) > 0
    # must decode to valid PNG bytes
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
