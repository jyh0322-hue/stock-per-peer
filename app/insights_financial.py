"""매출/비용/이익 흐름에 대한 규칙 기반 사실 서술 생성기.

app.deepdive의 income_statement/margins/trend(모두 이미 계산되어 있는 순수 데이터)만
입력으로 받아 순수 함수로 동작한다. 네트워크·LLM 호출이 전혀 없어 ANTHROPIC_API_KEY가
없어도(또는 완전 오프라인이어도) 항상 완전한 결과를 낸다.

원칙:
  - 모든 문장의 수치는 입력에서 직접 계산된 값이어야 한다(추측/과장 금지).
  - 매수/매도/보유 등 투자 판단 언어는 어디에도 쓰지 않는다(valuation.py와 동일한 원칙).
  - 결측치·0으로 나누기 등은 예외를 던지지 않고 해당 finding을 조용히 생략한다.
"""


def _item(income_statement, label):
    if not income_statement:
        return {}
    return income_statement.get(label) or {}


def _pct_change(cur, prev):
    """(cur-prev)/|prev|*100. prev가 None/0이거나 cur가 None이면 None."""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def _ratio(numer, denom):
    """numer/denom*100 (margin과 동일한 정의). denom이 None/0이거나 numer가 None이면 None."""
    if numer is None or denom is None or denom == 0:
        return None
    return numer / denom * 100


def _fmt_eok(v):
    if v is None:
        return "-"
    return "{:,.1f}".format(v)


def _fmt_pct(v):
    if v is None:
        return "-"
    return "{:+.1f}%".format(v)


# ---- revenue --------------------------------------------------------------

def _revenue_finding(income_statement):
    rev = _item(income_statement, "매출액")
    cur, prev = rev.get("cur_cum"), rev.get("prev_cum")
    pct = _pct_change(cur, prev)
    if pct is None:
        return None

    if pct > 5:
        severity = "positive"
    elif pct < -5:
        severity = "negative"
    else:
        severity = "neutral"

    verb = "증가" if pct >= 0 else "감소"
    text = "매출액은 전년동기 %s억 → %s억으로 %s %s했습니다." % (
        _fmt_eok(prev), _fmt_eok(cur), _fmt_pct(pct), verb,
    )
    return {
        "kind": "revenue",
        "severity": severity,
        "text": text,
        "metric": {"label": "매출액 YoY", "value": round(pct, 1), "unit": "%"},
    }


# ---- profitability ----------------------------------------------------------

def _profitability_finding(income_statement, margins):
    op = _item(income_statement, "영업이익")
    op_cur, op_prev = op.get("cur_cum"), op.get("prev_cum")
    op_pct = _pct_change(op_cur, op_prev)

    margin_delta = margin_cur = margin_prev = None
    if margins:
        om = margins.get("op_margin") or {}
        margin_delta = om.get("delta")
        margin_cur, margin_prev = om.get("cur"), om.get("prev")

    if op_pct is None and margin_delta is None:
        return None

    parts = []
    if op_pct is not None:
        verb = "증가" if op_pct >= 0 else "감소"
        parts.append("영업이익은 전년동기 %s억 → %s억으로 %s %s했습니다." % (
            _fmt_eok(op_prev), _fmt_eok(op_cur), _fmt_pct(op_pct), verb,
        ))
    if margin_delta is not None and margin_cur is not None and margin_prev is not None:
        direction = "상승" if margin_delta >= 0 else "하락"
        parts.append("영업이익률은 %.1f%% → %.1f%%로 %.1f%%p %s했습니다." % (
            margin_prev, margin_cur, abs(margin_delta), direction,
        ))
    if not parts:
        return None

    if margin_delta is not None and margin_delta < -1:
        severity = "negative"
    elif margin_delta is not None and margin_delta > 1:
        severity = "positive"
    elif margin_delta is None and op_pct is not None:
        severity = "positive" if op_pct > 5 else "negative" if op_pct < -5 else "neutral"
    else:
        severity = "neutral"

    if margin_delta is not None:
        metric = {"label": "영업이익률 변화", "value": round(margin_delta, 1), "unit": "%p"}
    else:
        metric = {"label": "영업이익 YoY", "value": round(op_pct, 1), "unit": "%"}

    return {"kind": "profitability", "severity": severity, "text": " ".join(parts), "metric": metric}


# ---- cost decomposition ------------------------------------------------------

def _cost_breakdown(income_statement):
    """매출원가율/판관비율의 전년 대비 %p 변화와, 둘 중 더 크게 움직인 쪽(driver)."""
    rev = _item(income_statement, "매출액")
    cogs = _item(income_statement, "매출원가")
    sga = _item(income_statement, "판매관리비")

    cogs_cur = _ratio(cogs.get("cur_cum"), rev.get("cur_cum"))
    cogs_prev = _ratio(cogs.get("prev_cum"), rev.get("prev_cum"))
    sga_cur = _ratio(sga.get("cur_cum"), rev.get("cur_cum"))
    sga_prev = _ratio(sga.get("prev_cum"), rev.get("prev_cum"))

    cogs_delta = (cogs_cur - cogs_prev) if (cogs_cur is not None and cogs_prev is not None) else None
    sga_delta = (sga_cur - sga_prev) if (sga_cur is not None and sga_prev is not None) else None

    driver = None
    if cogs_delta is not None and sga_delta is not None:
        driver = "판관비" if abs(sga_delta) >= abs(cogs_delta) else "매출원가"
    elif sga_delta is not None:
        driver = "판관비"
    elif cogs_delta is not None:
        driver = "매출원가"

    return {
        "cogs_ratio_delta_pp": round(cogs_delta, 1) if cogs_delta is not None else None,
        "sga_ratio_delta_pp": round(sga_delta, 1) if sga_delta is not None else None,
        "driver": driver,
    }


def _cost_finding(cost_breakdown, margins):
    """cost_breakdown(driver)과 영업이익률 변화를 묶어 원인-결과 문장을 만든다.
    영업이익률 변화(margins) 없이는 "무엇의 원인"인지 말할 수 없으므로 생략한다."""
    driver = cost_breakdown.get("driver")
    if driver is None:
        return None

    margin_delta = None
    if margins:
        margin_delta = (margins.get("op_margin") or {}).get("delta")
    if margin_delta is None:
        return None

    driver_delta = (
        cost_breakdown.get("sga_ratio_delta_pp") if driver == "판관비"
        else cost_breakdown.get("cogs_ratio_delta_pp")
    )
    if driver_delta is None:
        return None

    if margin_delta < 0:
        margin_dir = "하락"
    elif margin_delta > 0:
        margin_dir = "상승"
    else:
        return None  # 마진 변화가 없으면 "원인" 서술 자체가 성립하지 않음

    driver_dir = "상승" if driver_delta > 0 else "하락" if driver_delta < 0 else "보합"
    text = "영업이익률 %s(%.1f%%p)의 주된 원인은 %s율 %s(%+.1f%%p)입니다." % (
        margin_dir, abs(margin_delta), driver, driver_dir, driver_delta,
    )

    if margin_delta < -1:
        severity = "negative"
    elif margin_delta > 1:
        severity = "positive"
    else:
        severity = "neutral"

    return {
        "kind": "cost",
        "severity": severity,
        "text": text,
        "metric": {"label": "%s율 변화" % driver, "value": driver_delta, "unit": "%p"},
    }


# ---- quality (영업외 손익 영향) ------------------------------------------------

def _quality_finding(income_statement):
    op = _item(income_statement, "영업이익")
    net = _item(income_statement, "당기순이익")
    op_cur, op_prev = op.get("cur_cum"), op.get("prev_cum")
    net_cur, net_prev = net.get("cur_cum"), net.get("prev_cum")
    if None in (op_cur, op_prev, net_cur, net_prev):
        return None

    op_up, op_down = op_cur > op_prev, op_cur < op_prev
    net_up, net_down = net_cur > net_prev, net_cur < net_prev

    if op_down and net_up:
        text = "영업이익은 감소했으나 순이익은 증가해 영업외 손익의 영향이 있습니다."
    elif op_up and net_down:
        text = "영업이익은 증가했으나 순이익은 감소해 영업외 손익의 영향이 있습니다."
    else:
        return None

    net_pct = _pct_change(net_cur, net_prev)
    metric = {"label": "당기순이익 YoY", "value": round(net_pct, 1) if net_pct is not None else None, "unit": "%"}
    return {"kind": "quality", "severity": "neutral", "text": text, "metric": metric}


# ---- trend (5개년) ------------------------------------------------------------

def _trend_finding(trend):
    pts = [t for t in (trend or []) if t.get("year") is not None]
    if len(pts) < 2:
        return None
    pts = sorted(pts, key=lambda t: t["year"])
    first, last = pts[0], pts[-1]
    n = last["year"] - first["year"]

    parts = []
    cagr = None
    if n > 0 and first.get("revenue") not in (None, 0) and first["revenue"] > 0 and last.get("revenue") is not None:
        cagr = ((last["revenue"] / first["revenue"]) ** (1.0 / n) - 1) * 100
        parts.append("매출액은 %d년~%d년 연평균(CAGR) %s 성장했습니다." % (
            first["year"], last["year"], _fmt_pct(cagr),
        ))

    margin_dir = None
    if first.get("op_margin") is not None and last.get("op_margin") is not None:
        fm, lm = first["op_margin"], last["op_margin"]
        if lm > fm + 0.5:
            margin_dir = "상승"
        elif lm < fm - 0.5:
            margin_dir = "하락"
        else:
            margin_dir = "보합"
        parts.append("영업이익률은 %d년 %.1f%% → %d년 %.1f%%로 %s 추세입니다." % (
            first["year"], fm, last["year"], lm, margin_dir,
        ))

    if not parts:
        return None

    if margin_dir == "하락":
        severity = "negative"
    elif margin_dir == "상승":
        severity = "positive"
    elif cagr is not None:
        severity = "positive" if cagr > 5 else "negative" if cagr < -5 else "neutral"
    else:
        severity = "neutral"

    if cagr is not None:
        metric = {"label": "매출액 CAGR", "value": round(cagr, 1), "unit": "%"}
    else:
        metric = {"label": "영업이익률 변화(%d개년)" % n,
                   "value": round(last["op_margin"] - first["op_margin"], 1), "unit": "%p"}

    return {"kind": "trend", "severity": severity, "text": " ".join(parts), "metric": metric}


# ---- headline -----------------------------------------------------------------

def _headline(revenue_f, profit_f, cost_breakdown):
    rev_sev = revenue_f["severity"] if revenue_f else None
    prof_sev = profit_f["severity"] if profit_f else None
    driver = (cost_breakdown or {}).get("driver")

    driver_delta = None
    if driver == "판관비":
        driver_delta = (cost_breakdown or {}).get("sga_ratio_delta_pp")
    elif driver == "매출원가":
        driver_delta = (cost_breakdown or {}).get("cogs_ratio_delta_pp")

    rev_word = {"positive": "매출은 늘었", "negative": "매출은 줄었", "neutral": "매출은 보합이었"}.get(rev_sev)

    margin_clause = ""
    if prof_sev == "negative":
        if driver and driver_delta is not None and driver_delta > 0:
            margin_clause = "%s 증가로 영업이익률이 하락했습니다." % driver
        else:
            margin_clause = "영업이익률이 하락했습니다."
    elif prof_sev == "positive":
        if driver and driver_delta is not None and driver_delta < 0:
            margin_clause = "%s 감소로 영업이익률이 개선되었습니다." % driver
        else:
            margin_clause = "영업이익률이 개선되었습니다."
    elif prof_sev == "neutral":
        margin_clause = "영업이익률은 큰 변화가 없었습니다."

    if rev_word and margin_clause:
        contrast = "지만" if (
            (rev_sev == "positive" and prof_sev == "negative")
            or (rev_sev == "negative" and prof_sev == "positive")
        ) else "고"
        return "%s%s %s" % (rev_word, contrast, margin_clause)
    if rev_word:
        return "%s습니다." % rev_word
    if margin_clause:
        return margin_clause
    return ""


# ---- entry point ----------------------------------------------------------------

def analyze(income_statement, margins, trend):
    """매출/비용/이익 흐름에 대한 규칙 기반 사실 서술. 순수 함수, 네트워크/LLM 없음.

    income_statement/margins가 None이거나 결측치투성이여도 예외를 던지지 않고
    findings가 비어있는(또는 일부만 채워진) 결과를 반환한다.
    """
    income_statement = income_statement or {}
    margins = margins or {}
    trend = trend or []

    findings = []

    revenue_f = _revenue_finding(income_statement)
    if revenue_f:
        findings.append(revenue_f)

    profit_f = _profitability_finding(income_statement, margins)
    if profit_f:
        findings.append(profit_f)

    cost_breakdown = _cost_breakdown(income_statement)
    cost_f = _cost_finding(cost_breakdown, margins)
    if cost_f:
        findings.append(cost_f)

    quality_f = _quality_finding(income_statement)
    if quality_f:
        findings.append(quality_f)

    trend_f = _trend_finding(trend)
    if trend_f:
        findings.append(trend_f)

    headline = _headline(revenue_f, profit_f, cost_breakdown)

    return {"headline": headline, "findings": findings, "cost_breakdown": cost_breakdown}
