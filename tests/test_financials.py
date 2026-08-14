from datetime import date

import pandas as pd

from app import financials

CUR = date.today().year


def _is_row(account_id, thstrm=None, thstrm_add=None, frmtrm_q=None, frmtrm_add=None,
            frmtrm=None):
    row = {"account_id": account_id}
    if thstrm is not None:
        row["thstrm_amount"] = thstrm
    if thstrm_add is not None:
        row["thstrm_add_amount"] = thstrm_add
    if frmtrm_q is not None:
        row["frmtrm_q_amount"] = frmtrm_q
    if frmtrm_add is not None:
        row["frmtrm_add_amount"] = frmtrm_add
    if frmtrm is not None:
        row["frmtrm_amount"] = frmtrm
    return row


def _half_df(missing=None):
    """반기(HALF) 스키마 df. missing에 넣은 항목명은 행을 아예 빼서 '결측' 상황을 흉내낸다."""
    missing = missing or set()
    rows = []
    data = {
        "매출액": ("ifrs-full_Revenue", "10,000,000,000", "20,000,000,000", "9,000,000,000", "18,000,000,000"),
        "매출원가": ("ifrs-full_CostOfSales", "6,000,000,000", "12,000,000,000", "5,500,000,000", "11,000,000,000"),
        "매출총이익": ("ifrs-full_GrossProfit", "4,000,000,000", "8,000,000,000", "3,500,000,000", "7,000,000,000"),
        "판매관리비": ("dart_TotalSellingGeneralAdministrativeExpenses",
                    "1,500,000,000", "3,000,000,000", "1,400,000,000", "2,800,000,000"),
        "영업이익": ("dart_OperatingIncomeLoss", "2,500,000,000", "5,000,000,000", "2,100,000,000", "4,200,000,000"),
        "세전이익": ("ifrs-full_ProfitLossBeforeTax", "2,300,000,000", "4,600,000,000", "2,000,000,000", "4,000,000,000"),
        "당기순이익": ("ifrs-full_ProfitLoss", "1,800,000,000", "3,600,000,000", "1,600,000,000", "3,200,000,000"),
    }
    for label, (acc_id, t, ta, fq, fa) in data.items():
        if label in missing:
            continue
        rows.append(_is_row(acc_id, thstrm=t, thstrm_add=ta, frmtrm_q=fq, frmtrm_add=fa))
    return pd.DataFrame(rows)


def _annual_df():
    return pd.DataFrame([
        _is_row("ifrs-full_Revenue", thstrm="40,000,000,000", frmtrm="35,000,000,000"),
        _is_row("dart_OperatingIncomeLoss", thstrm="8,000,000,000", frmtrm="6,000,000,000"),
        _is_row("ifrs-full_ProfitLoss", thstrm="6,000,000,000", frmtrm="4,500,000,000"),
    ])


def test_income_statement_extracts_all_7_items_by_account_id():
    df = _half_df()
    out = financials.income_statement(df, "HALF")
    assert set(out.keys()) == {"매출액", "매출원가", "매출총이익", "판매관리비", "영업이익", "세전이익", "당기순이익"}
    rev = out["매출액"]
    assert rev["cur_3m"] == 100.0    # 10,000,000,000원 -> 100억
    assert rev["prev_3m"] == 90.0
    assert rev["cur_cum"] == 200.0
    assert rev["prev_cum"] == 180.0


def test_income_statement_missing_line_item_returns_none_fields():
    df = _half_df(missing={"세전이익"})
    out = financials.income_statement(df, "HALF")
    assert out["세전이익"] == {"cur_3m": None, "prev_3m": None, "cur_cum": None, "prev_cum": None}
    # 나머지 항목은 정상 추출됨
    assert out["매출액"]["cur_cum"] == 200.0


def test_income_statement_annual_uses_thstrm_frmtrm_as_cumulative_only():
    df = _annual_df()
    out = financials.income_statement(df, "ANNUAL")
    assert out["매출액"]["cur_3m"] is None
    assert out["매출액"]["prev_3m"] is None
    assert out["매출액"]["cur_cum"] == 400.0
    assert out["매출액"]["prev_cum"] == 350.0


def test_income_statement_handles_none_df():
    out = financials.income_statement(None, "HALF")
    assert all(v == {"cur_3m": None, "prev_3m": None, "cur_cum": None, "prev_cum": None}
               for v in out.values())


def test_margins_computes_ratios_and_deltas():
    df = _half_df()
    is_data = financials.income_statement(df, "HALF")
    m = financials.margins(is_data)
    # 영업이익률 cur = 5000/20000*100 = 25.0, prev = 4200/18000*100 = 23.33..
    assert round(m["op_margin"]["cur"], 2) == 25.0
    assert round(m["op_margin"]["prev"], 2) == round(4200 / 18000 * 100, 2)
    assert round(m["op_margin"]["delta"], 2) == round(25.0 - 4200 / 18000 * 100, 2)
    # 매출총이익률, 판관비율도 채워짐
    assert m["gross_margin"]["cur"] is not None
    assert m["sga_ratio"]["cur"] is not None
    assert m["net_margin"]["cur"] is not None


def test_margins_zero_revenue_returns_none_no_zerodivision():
    df = _half_df()
    is_data = financials.income_statement(df, "HALF")
    is_data["매출액"]["cur_cum"] = 0
    is_data["매출액"]["prev_cum"] = 0
    m = financials.margins(is_data)
    assert m["op_margin"]["cur"] is None
    assert m["op_margin"]["prev"] is None
    assert m["op_margin"]["delta"] is None


class _TrendFakeDart:
    """5개년 중 3개년(가장 최근, 2년 전, 4년 전)만 데이터가 있는 상황."""

    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        assert reprt_key == "HALF"  # 5년 내내 동일 reprt_key로 조회되어야 비교 가능
        offset = CUR - year
        if offset in (0, 2, 4):
            return _half_df()
        return None


def test_five_year_trend_skips_years_with_no_data():
    out = financials.five_year_trend(_TrendFakeDart(), "X", CUR, "HALF", fs_div="CFS")
    assert len(out) == 3
    years = [r["year"] for r in out]
    assert years == sorted(years)  # 오름차순
    for r in out:
        assert r["revenue"] == 200.0
        assert r["operating_income"] == 50.0
        assert r["op_margin"] is not None


class _TrendBoomDart:
    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        raise RuntimeError("network down")


def test_five_year_trend_survives_all_calls_failing():
    out = financials.five_year_trend(_TrendBoomDart(), "X", CUR, "HALF", fs_div="CFS")
    assert out == []


# ---- TTM(최근 4개 이산분기) ------------------------------------------------

def _op_df(thstrm=None, thstrm_add=None):
    row = {"account_id": "dart_OperatingIncomeLoss"}
    if thstrm is not None:
        row["thstrm_amount"] = thstrm
    if thstrm_add is not None:
        row["thstrm_add_amount"] = thstrm_add
    return pd.DataFrame([row])


class _FourQuarterDart:
    """당해 HALF/Q1과 전기 ANNUAL(Q4 역산용 Q3 포함)로 정확히 4개 이산분기가 모이는 상황.
    합계: 당해 HALF(20) + 당해 Q1(10) + 전기 Q4(연간100-3Q누적70=30) + 전기 Q3(25) = 85."""

    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        if year == CUR:
            if reprt_key == "HALF":
                return _op_df(thstrm="2,000,000,000")
            if reprt_key == "Q1":
                return _op_df(thstrm="1,000,000,000")
            return None
        if year == CUR - 1:
            if reprt_key == "ANNUAL":
                return _op_df(thstrm_add="10,000,000,000")
            if reprt_key == "Q3":
                return _op_df(thstrm="2,500,000,000", thstrm_add="7,000,000,000")
        return None


def test_ttm_operating_income_sums_4_discrete_quarters():
    out = financials.ttm_operating_income(_FourQuarterDart(), "X", fs_div="CFS")
    assert out["complete"] is True
    assert out["op_ttm"] == 85.0
    assert len(out["quarters"]) == 4
    for q in out["quarters"]:
        assert "op_3m" in q and "year" in q and "reprt_key" in q


class _TwoQuarterDart:
    """당해 HALF, Q1만 존재 — 4개를 채우지 못하는 상황."""

    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        if year == CUR:
            if reprt_key == "HALF":
                return _op_df(thstrm="2,000,000,000")
            if reprt_key == "Q1":
                return _op_df(thstrm="1,000,000,000")
        return None


def test_ttm_operating_income_incomplete_when_fewer_than_4():
    out = financials.ttm_operating_income(_TwoQuarterDart(), "X", fs_div="CFS")
    assert out["complete"] is False
    assert out["op_ttm"] is None
    assert len(out["quarters"]) == 2


class _NetIncomeFourQuarterDart:
    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        def row(thstrm=None, thstrm_add=None):
            r = {"account_id": "ifrs-full_ProfitLoss"}
            if thstrm is not None:
                r["thstrm_amount"] = thstrm
            if thstrm_add is not None:
                r["thstrm_add_amount"] = thstrm_add
            return pd.DataFrame([r])

        if year == CUR:
            if reprt_key == "HALF":
                return row(thstrm="1,000,000,000")
            if reprt_key == "Q1":
                return row(thstrm="500,000,000")
            return None
        if year == CUR - 1:
            if reprt_key == "ANNUAL":
                return row(thstrm_add="6,000,000,000")
            if reprt_key == "Q3":
                return row(thstrm="1,500,000,000", thstrm_add="4,000,000,000")
        return None


def test_ttm_net_income_sums_4_discrete_quarters():
    # 당해 HALF(10) + Q1(5) + 전기 Q4(60-40=20) + 전기 Q3(15) = 50
    val = financials.ttm_net_income(_NetIncomeFourQuarterDart(), "X", fs_div="CFS")
    assert val == 50.0


class _NetIncomeTwoQuarterDart:
    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        if year == CUR and reprt_key in ("HALF", "Q1"):
            return pd.DataFrame([{"account_id": "ifrs-full_ProfitLoss", "thstrm_amount": "1,000,000,000"}])
        return None


def test_ttm_net_income_none_when_fewer_than_4():
    assert financials.ttm_net_income(_NetIncomeTwoQuarterDart(), "X", fs_div="CFS") is None
