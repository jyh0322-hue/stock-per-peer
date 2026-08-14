from typing import Optional
from app import config


def to_eok(x):
    try:
        s = str(x).replace(",", "").replace("(", "-").replace(")", "").strip()
        return float(s) / config.EOK
    except Exception:
        return float("nan")


def pick_op_amount(df, field):
    m = df[df["account_id"].isin(config.OP_ACCOUNT_IDS)]
    if len(m) == 0:
        return None
    v = to_eok(m.iloc[0].get(field))
    return v if v == v else None  # NaN -> None


def op_3m_from_df(df, reprt_key, prev_cum_df=None):
    if reprt_key in ("Q1", "HALF", "Q3"):
        # thstrm_amount = 해당 분기 3개월 (1Q는 3개월=누적)
        return pick_op_amount(df, "thstrm_amount")
    if reprt_key == "ANNUAL":
        annual_cum = pick_op_amount(df, "thstrm_add_amount")
        if annual_cum is None:
            annual_cum = pick_op_amount(df, "thstrm_amount")
        prev_cum = pick_op_amount(prev_cum_df, "thstrm_add_amount") if prev_cum_df is not None else None
        if annual_cum is None or prev_cum is None:
            return None
        return annual_cum - prev_cum
    return None
