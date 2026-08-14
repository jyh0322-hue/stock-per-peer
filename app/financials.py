"""손익계산서 추출, 마진 계산, 5개년 추이, TTM(최근 4개 분기 합산) 영업이익/순이익.

DART finstate_all() 응답 DataFrame(계정별 1행)을 다룬다. 계정명이 아니라
account_id로 매칭한다(회사/연도별로 계정명이 달라질 수 있어서다) — 이 로직은
stock_report.py(레거시 단독 스크립트)에서 실데이터로 검증된 것을 app.config.IS_ITEMS로
이식했다.

DART 응답의 열 구성(실측, 2026-08 기준):
  - Q1/HALF/Q3(11013/11012/11014): thstrm_amount=당분기 3개월,
    thstrm_add_amount=당해 누적, frmtrm_q_amount=전년동기 3개월, frmtrm_add_amount=전년동기 누적
  - ANNUAL(11011, 사업보고서): thstrm_amount=당해 전체, frmtrm_amount=전기 전체
    (3개월/누적 구분이 없다 — 사업보고서는 연간 총계만 제공)
"""
from datetime import date
from typing import Optional

from app import config, metrics, quarterly

_EMPTY_ITEM = {"cur_3m": None, "prev_3m": None, "cur_cum": None, "prev_cum": None}


def _val(row, col):
    if col not in row:
        return None
    v = quarterly.to_eok(row[col])
    return v if v == v else None  # NaN -> None


def income_statement(df, reprt_key):
    """{항목명: {"cur_3m","prev_3m","cur_cum","prev_cum"}} (억원). df가 없거나
    항목이 없으면 해당 값은 None."""
    out = {}
    for label, ids in config.IS_ITEMS:
        if df is None or len(df) == 0:
            out[label] = dict(_EMPTY_ITEM)
            continue
        m = df[df["account_id"].isin(ids)]
        if len(m) == 0:
            out[label] = dict(_EMPTY_ITEM)
            continue
        r = m.iloc[0]
        if reprt_key == "ANNUAL":
            out[label] = {
                "cur_3m": None,
                "prev_3m": None,
                "cur_cum": _val(r, "thstrm_amount"),
                "prev_cum": _val(r, "frmtrm_amount"),
            }
        else:
            out[label] = {
                "cur_3m": _val(r, "thstrm_amount"),
                "prev_3m": _val(r, "frmtrm_q_amount"),
                "cur_cum": _val(r, "thstrm_add_amount"),
                "prev_cum": _val(r, "frmtrm_add_amount"),
            }
    return out


def margins(is_data):
    """누적(cur_cum/prev_cum) 기준 마진 4종 + 전년 대비 %p 변화."""
    rev = is_data.get("매출액", _EMPTY_ITEM)

    def _one(label):
        d = is_data.get(label, _EMPTY_ITEM)
        cur = metrics.margin(d.get("cur_cum"), rev.get("cur_cum"))
        prev = metrics.margin(d.get("prev_cum"), rev.get("prev_cum"))
        delta = (cur - prev) if (cur is not None and prev is not None) else None
        return {"cur": cur, "prev": prev, "delta": delta}

    return {
        "gross_margin": _one("매출총이익"),
        "op_margin": _one("영업이익"),
        "net_margin": _one("당기순이익"),
        "sga_ratio": _one("판매관리비"),
    }


def five_year_trend(dart, corp_code, latest_year, reprt_key, fs_div="CFS"):
    """최근 5개년(latest_year 포함), 동일 reprt_key 기준(비교 가능하도록) 추이.
    데이터 없는 연도는 건너뛴다. finstate()는 캐시되므로 호출 5회 내외."""
    out = []
    for y in range(latest_year - 4, latest_year + 1):
        try:
            df = dart.finstate(corp_code, y, reprt_key, fs_div=fs_div)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        is_data = income_statement(df, reprt_key)
        rev = is_data.get("매출액", _EMPTY_ITEM).get("cur_cum")
        op = is_data.get("영업이익", _EMPTY_ITEM).get("cur_cum")
        net = is_data.get("당기순이익", _EMPTY_ITEM).get("cur_cum")
        if rev is None and op is None and net is None:
            continue
        out.append({
            "year": y,
            "revenue": rev,
            "operating_income": op,
            "net_income": net,
            "op_margin": metrics.margin(op, rev),
        })
    return out


def _pick_amount(df, ids, field):
    if df is None or len(df) == 0:
        return None
    m = df[df["account_id"].isin(ids)]
    if len(m) == 0:
        return None
    v = quarterly.to_eok(m.iloc[0].get(field))
    return v if v == v else None


def _amount_3m_from_df(df, reprt_key, ids, prev_cum_df=None):
    """quarterly.op_3m_from_df와 동일한 로직을 임의의 account_id 집합에 대해
    수행하는 일반화 버전(당기순이익 등 영업이익이 아닌 항목의 TTM 계산용)."""
    if reprt_key in ("Q1", "HALF", "Q3"):
        return _pick_amount(df, ids, "thstrm_amount")
    if reprt_key == "ANNUAL":
        annual_cum = _pick_amount(df, ids, "thstrm_add_amount")
        if annual_cum is None:
            annual_cum = _pick_amount(df, ids, "thstrm_amount")
        prev_cum = _pick_amount(prev_cum_df, ids, "thstrm_add_amount") if prev_cum_df is not None else None
        if annual_cum is None or prev_cum is None:
            return None
        return annual_cum - prev_cum
    return None


def _walk_discrete_quarters(dart, corp_code, fs_div, amount_fn, want=4, lookback_years=3):
    """최근 연도부터 과거로 config.REPRT_ORDER(ANNUAL,Q3,HALF,Q1) 순서로 훑으며
    "이산(discrete) 분기" 값을 최대 want개 모은다. REPRT_ORDER는 한 해 안에서
    Q4(ANNUAL 계산값)→Q3→Q2(HALF)→Q1 순으로 시간 역행 순서와 일치한다
    (DartClient._scan_quarters와 동일한 탐색 패턴)."""
    latest_year = date.today().year
    quarters = []
    for year in range(latest_year, latest_year - lookback_years - 1, -1):
        for reprt_key in config.REPRT_ORDER:
            try:
                df = dart.finstate(corp_code, year, reprt_key, fs_div=fs_div)
            except Exception:
                df = None
            if df is None or len(df) == 0:
                continue
            prev = None
            if reprt_key == "ANNUAL":
                try:
                    prev = dart.finstate(corp_code, year, "Q3", fs_div=fs_div)
                except Exception:
                    prev = None
            val = amount_fn(df, reprt_key, prev)
            if val is None:
                continue
            quarters.append({"year": year, "reprt_key": reprt_key, "value": val})
            if len(quarters) >= want:
                return quarters
    return quarters


def ttm_operating_income(dart, corp_code, fs_div="CFS"):
    """최근 4개 이산 분기 영업이익 합(TTM). 4개 미만이면 complete=False, op_ttm=None."""
    quarters = _walk_discrete_quarters(
        dart, corp_code, fs_div,
        lambda df, rk, prev: quarterly.op_3m_from_df(df, rk, prev_cum_df=prev),
        want=4,
    )
    complete = len(quarters) >= 4
    op_ttm = sum(q["value"] for q in quarters) if complete else None
    out_quarters = [{"year": q["year"], "reprt_key": q["reprt_key"], "op_3m": q["value"]} for q in quarters]
    return {"op_ttm": op_ttm, "quarters": out_quarters, "complete": complete}


def ttm_net_income(dart, corp_code, fs_div="CFS"):
    """최근 4개 이산 분기 당기순이익 합(TTM). 4개 미만이면 None."""
    quarters = _walk_discrete_quarters(
        dart, corp_code, fs_div,
        lambda df, rk, prev: _amount_3m_from_df(df, rk, config.NET_INCOME_ACCOUNT_IDS, prev_cum_df=prev),
        want=4,
    )
    if len(quarters) < 4:
        return None
    return sum(q["value"] for q in quarters)
