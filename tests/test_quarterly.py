import pandas as pd
from app import quarterly


def _df(op_thstrm, op_add=None):
    # DART finstate_all 유사 스키마
    return pd.DataFrame([
        {"account_id": "dart_OperatingIncomeLoss",
         "thstrm_amount": op_thstrm, "thstrm_add_amount": op_add},
        {"account_id": "ifrs-full_Revenue",
         "thstrm_amount": "9,999", "thstrm_add_amount": "9,999"},
    ])


def test_half_report_uses_3month_field():
    # 반기: thstrm_amount = Q2 3개월치 = 20억
    df = _df("2,000,000,000", "5,000,000,000")
    assert quarterly.op_3m_from_df(df, "HALF") == 20.0


def test_q1_report_add_equals_3month():
    # 1Q: 3개월=누적, thstrm_amount 사용
    df = _df("3,000,000,000", "3,000,000,000")
    assert quarterly.op_3m_from_df(df, "Q1") == 30.0


def test_annual_report_q4_is_annual_minus_3q_cum():
    # 사업보고서 연간누적 100억, 직전 3Q 누적 70억 -> Q4 = 30억
    annual = _df("10,000,000,000", "10,000,000,000")
    prev_3q = _df("1,000,000,000", "7,000,000,000")  # 3Q 누적은 add 필드
    assert quarterly.op_3m_from_df(annual, "ANNUAL", prev_cum_df=prev_3q) == 30.0


def test_missing_op_returns_none():
    df = pd.DataFrame([{"account_id": "ifrs-full_Revenue",
                        "thstrm_amount": "1", "thstrm_add_amount": "1"}])
    assert quarterly.op_3m_from_df(df, "HALF") is None
