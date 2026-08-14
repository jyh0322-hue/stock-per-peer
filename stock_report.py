#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
종목 분석 보고서 생성기 (OpenDART 기반)
================================================
사용법:
    export OPENDART_API_KEY="발급받은40자리키"
    python3 stock_report.py "브이티"
    python3 stock_report.py "브이티" 2026          # 기준연도 지정
    python3 stock_report.py "브이티" 2026 OFS       # 별도재무제표

종목명(또는 종목코드/고유번호)을 넣으면
  - 반기 실적 전년동기 비교표(3개월/누적)
  - 판매관리비 세부 항목 분해(전년 대비)
  - 매출·영업이익·판관비 5개년 추이 + 판관비 최대 증가항목 5개년 추이
를 계산해 자기완결형 HTML 보고서(차트 내장)를 생성한다.
"""
import os
import sys
import time
import base64
from io import StringIO, BytesIO

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# ── 공통 상수 ──────────────────────────────────────────────
REPRT_HALF = "11012"   # 반기보고서
UA = {"User-Agent": "Mozilla/5.0"}
EOK = 1e8              # 원 -> 억원
CHEON_TO_EOK = 1e5    # 천원 -> 억원 (주석 표는 천원 단위)

# 표준계정코드(account_id) 기반 매칭 — 계정명이 회사/연도별로 달라도 안전
IS_ITEMS = [
    ("매출액",       ["ifrs-full_Revenue"]),
    ("매출원가",     ["ifrs-full_CostOfSales"]),
    ("매출총이익",   ["ifrs-full_GrossProfit"]),
    ("판매관리비",   ["dart_TotalSellingGeneralAdministrativeExpenses",
                    "ifrs-full_SellingGeneralAndAdministrativeExpense"]),
    ("영업이익",     ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"]),
    ("세전이익",     ["ifrs-full_ProfitLossBeforeTax"]),
    ("당기순이익",   ["ifrs-full_ProfitLoss"]),
]
ID_REVENUE = "ifrs-full_Revenue"


# ── 유틸 ───────────────────────────────────────────────────
def to_num(x):
    try:
        s = str(x).replace(",", "").replace("(", "-").replace(")", "").strip()
        return float(s)
    except Exception:
        return float("nan")


def eok(x, unit=EOK):
    v = to_num(x)
    return v / unit if v == v else float("nan")


def fetch(url, tries=5):
    for i in range(tries):
        try:
            return requests.get(url, headers=UA, timeout=30).text
        except Exception:
            time.sleep(1.5 * (i + 1))
    return ""


def retry(fn, *a, tries=5, **k):
    """DART 호출용 재시도 래퍼 (연결 리셋/일시 오류 대비)"""
    last = None
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def pct(cur, prev):
    if prev and prev == prev and prev != 0:
        return (cur - prev) / abs(prev) * 100
    return float("nan")


def fmt(v, dp=0):
    return f"{v:,.{dp}f}" if v == v else "-"


# ── 데이터 수집 ────────────────────────────────────────────
def resolve_corp(dart, name_or_code):
    """종목명/코드 -> (corp_code, 회사명, 종목코드)"""
    code = dart.find_corp_code(name_or_code)
    if not code:
        raise SystemExit(f"'{name_or_code}' 에 해당하는 기업을 DART에서 찾지 못했습니다.")
    info = {}
    try:
        info = dart.company(code) or {}
    except Exception:
        pass
    return code, info.get("corp_name", name_or_code), info.get("stock_code", "")


def latest_half_year(dart, corp_code, start_year, fs_div):
    """데이터가 존재하는 가장 최근 반기 연도를 찾음"""
    for y in range(start_year, start_year - 4, -1):
        try:
            df = retry(dart.finstate_all, corp_code, y, REPRT_HALF, fs_div=fs_div)
            if df is not None and len(df):
                return y, df
        except Exception:
            continue
    raise SystemExit("최근 반기 재무제표를 찾지 못했습니다.")


def is_by_id(df, ids, col):
    m = df[df["account_id"].isin(ids)]
    return eok(m.iloc[0][col]) if len(m) else float("nan")


def income_statement(df):
    """반기 손익 주요항목: 3개월/누적, 전년동기 3개월/누적"""
    out = {}
    for label, ids in IS_ITEMS:
        m = df[df["account_id"].isin(ids)]
        if len(m):
            r = m.iloc[0]
            out[label] = dict(
                q_cur=eok(r["thstrm_amount"]),  q_prev=eok(r.get("frmtrm_q_amount")),
                a_cur=eok(r["thstrm_add_amount"]), a_prev=eok(r.get("frmtrm_add_amount")),
            )
        else:
            out[label] = dict(q_cur=float("nan"), q_prev=float("nan"),
                              a_cur=float("nan"), a_prev=float("nan"))
    return out


def sga_note_url(dart, rcept, fs_div):
    sd = retry(dart.sub_docs, rcept)
    key = "연결재무제표 주석" if fs_div == "CFS" else "재무제표 주석"
    m = sd[sd["title"].str.contains(key)]
    if len(m) == 0:  # 폴백: 아무 주석
        m = sd[sd["title"].str.contains("주석")]
    return m["url"].iloc[0] if len(m) else None


def sga_breakdown(dart, corp_code, year, fs_div):
    """해당 연도 반기 주석에서 판관비 세부항목(누적, 억원) dict + 총계 반환"""
    df = retry(dart.finstate_all, corp_code, year, REPRT_HALF, fs_div=fs_div)
    if df is None or len(df) == 0:
        return {}, float("nan")
    time.sleep(0.4)
    url = sga_note_url(dart, df.iloc[0]["rcept_no"], fs_div)
    if not url:
        return {}, float("nan")
    try:
        tables = pd.read_html(StringIO(fetch(url)))
    except Exception:
        return {}, float("nan")

    SGA_HINTS = ["판매수수료", "판매촉진비", "지급수수료", "광고선전비"]
    for t in tables:
        txt = t.to_string()
        if sum(h in txt for h in SGA_HINTS) < 2:
            continue
        # 누적 열 결정: '누적' > '당반기'/'당기' > 기본 1열
        cols = ["".join(str(x) for x in (c if isinstance(c, tuple) else (c,))) for c in t.columns]
        ci = next((i for i, c in enumerate(cols) if "누적" in c), None)
        if ci is None:
            ci = next((i for i, c in enumerate(cols) if "당반기" in c or "당기" in c), 1)
        items, total = {}, float("nan")
        for _, r in t.iterrows():
            name = str(r.iloc[0]).replace(", 판관비", "").replace(" ", "").strip()
            val = eok(r.iloc[ci], CHEON_TO_EOK)
            if name in ("nan", ""):
                continue
            if "총계" in name or "합계" in name:
                total = val
            else:
                items[name] = val
        if items:
            return items, total
    return {}, float("nan")


# ── 차트 ───────────────────────────────────────────────────
def set_korean_font():
    for cand in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic"]:
        try:
            font_manager.findfont(cand, fallback_to_default=False)
            plt.rcParams["font.family"] = cand
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def fig_to_b64(fig):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def chart_perf(years, rev, op, sga, name):
    fig, ax1 = plt.subplots(figsize=(8.6, 4.8))
    x = range(len(years))
    w = 0.26
    ax1.bar([i - w for i in x], rev, w, label="매출액", color="#4C78A8")
    ax1.bar([i for i in x], sga, w, label="판관비", color="#F58518")
    ax1.bar([i + w for i in x], op, w, label="영업이익", color="#54A24B")
    ax1.set_xticks(list(x)); ax1.set_xticklabels([str(y) for y in years])
    ax1.set_ylabel("억원")
    ax1.legend(loc="upper left", fontsize=9)
    ax2 = ax1.twinx()
    opm = [o / r * 100 if r else float("nan") for o, r in zip(op, rev)]
    ax2.plot(list(x), opm, color="#E45756", marker="o", lw=2, label="영업이익률(%)")
    ax2.set_ylabel("영업이익률 (%)", color="#E45756")
    ax2.tick_params(axis="y", labelcolor="#E45756")
    for i, v in zip(x, opm):
        ax2.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=8.5, color="#E45756")
    ax1.set_title(f"{name} 실적 추이 (반기 누적)", fontsize=12)
    fig.tight_layout()
    return fig_to_b64(fig)


def chart_driver(years, vals, rev, driver, name):
    fig, ax1 = plt.subplots(figsize=(8.6, 4.8))
    x = [str(y) for y in years]
    bars = ax1.bar(x, vals, 0.55, color="#4C78A8", label=f"{driver}(억원)")
    ax1.set_ylabel(f"{driver} (억원)", color="#4C78A8")
    ax1.tick_params(axis="y", labelcolor="#4C78A8")
    top = max([v for v in vals if v == v] or [1])
    ax1.set_ylim(0, top * 1.25)
    for b, v in zip(bars, vals):
        if v == v:
            ax1.text(b.get_x() + b.get_width() / 2, v + top * 0.02, f"{v:,.0f}",
                     ha="center", fontsize=9)
    ax2 = ax1.twinx()
    ratio = [v / r * 100 if r else float("nan") for v, r in zip(vals, rev)]
    ax2.plot(x, ratio, color="#E45756", marker="o", lw=2.2)
    ax2.set_ylabel(f"{driver}/매출 (%)", color="#E45756")
    ax2.tick_params(axis="y", labelcolor="#E45756")
    rmax = max([v for v in ratio if v == v] or [1])
    ax2.set_ylim(0, rmax * 1.35)
    for xi, rv in zip(x, ratio):
        if rv == rv:
            ax2.text(xi, rv + rmax * 0.04, f"{rv:.1f}%", ha="center", fontsize=8.5, color="#E45756")
    ax1.set_title(f"{name} '{driver}' 추이 (반기 누적)", fontsize=12)
    fig.tight_layout()
    return fig_to_b64(fig)


# ── HTML 생성 ──────────────────────────────────────────────
def yoy_cell(v):
    if v != v:
        return '<td class="num">-</td>'
    cls = "up" if v >= 0 else "down"
    sign = "+" if v >= 0 else ""
    return f'<td class="num {cls}">{sign}{v:.1f}%</td>'


def build_html(ctx):
    name, stock, basis, year = ctx["name"], ctx["stock"], ctx["basis"], ctx["year"]
    isx = ctx["isd"]
    py = year - 1

    # 손익 표
    rows = ""
    for label in ["매출액", "매출원가", "매출총이익", "판매관리비", "영업이익", "세전이익", "당기순이익"]:
        d = isx[label]
        strong = label in ("매출액", "영업이익", "당기순이익", "판매관리비")
        nm = f"<strong>{label}</strong>" if strong else label
        rows += (
            f"<tr><td>{nm}</td>"
            f'<td class="num">{fmt(d["q_cur"])}</td><td class="num">{fmt(d["q_prev"])}</td>{yoy_cell(pct(d["q_cur"], d["q_prev"]))}'
            f'<td class="num">{fmt(d["a_cur"])}</td><td class="num">{fmt(d["a_prev"])}</td>{yoy_cell(pct(d["a_cur"], d["a_prev"]))}</tr>\n'
        )

    # 마진 표
    def margin(numer, denom_key="매출액"):
        c = isx[numer]["a_cur"] / isx[denom_key]["a_cur"] * 100 if isx[denom_key]["a_cur"] else float("nan")
        p = isx[numer]["a_prev"] / isx[denom_key]["a_prev"] * 100 if isx[denom_key]["a_prev"] else float("nan")
        return c, p
    mrows = ""
    for lbl, key in [("매출총이익률", "매출총이익"), ("영업이익률", "영업이익"), ("판관비율", "판매관리비")]:
        c, p = margin(key)
        dpp = c - p
        cls = "up" if dpp >= 0 else "down"
        mrows += (f'<tr><td>{lbl}</td><td class="num">{fmt(c,1)}%</td>'
                  f'<td class="num">{fmt(p,1)}%</td>'
                  f'<td class="num {cls}">{"+" if dpp>=0 else ""}{fmt(dpp,1)}%p</td></tr>\n')

    # 판관비 분해 표
    sga_rows = ""
    for it in ctx["sga_sorted"][:12]:
        cls = "up" if it["inc"] >= 0 else "down"
        yv = f'{"+" if it["yoy"]>=0 else ""}{it["yoy"]:.1f}%' if it["yoy"] == it["yoy"] else "신규/흑전"
        sga_rows += (f'<tr><td>{it["name"]}</td><td class="num">{fmt(it["cur"])}</td>'
                     f'<td class="num">{fmt(it["prev"])}</td>'
                     f'<td class="num {cls}">{"+" if it["inc"]>=0 else ""}{fmt(it["inc"])}</td>'
                     f'<td class="num">{yv}</td>'
                     f'<td class="num">{fmt(it["contrib"],1)}%</td></tr>\n')

    # 추이 표
    tr_rows = ""
    T = ctx["trend"]
    prev_sga = None
    for i, y in enumerate(T["years"]):
        r, s, o = T["rev"][i], T["sga"][i], T["op"][i]
        ratio = s / r * 100 if r else float("nan")
        opm = o / r * 100 if r else float("nan")
        g = pct(s, prev_sga) if prev_sga else float("nan")
        gtxt = f"{'+' if g>=0 else ''}{g:.1f}%" if g == g else "-"
        tr_rows += (f'<tr><td>{y} 반기</td><td class="num">{fmt(r)}</td>'
                    f'<td class="num">{fmt(s)}</td><td class="num">{fmt(o)}</td>'
                    f'<td class="num">{fmt(ratio,1)}%</td><td class="num">{fmt(opm,1)}%</td>'
                    f'<td class="num">{gtxt}</td></tr>\n')
        prev_sga = s

    narrative = ctx["narrative"]
    driver = ctx["driver"]

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} 종목 분석 보고서</title>
<style>
:root{{--bg:#ffffff;--fg:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--head:#f8fafc;--accent:#1d4ed8;--up:#c0392b;--down:#1e6fbf;--card:#f9fafb;}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14171c;--fg:#e6e9ef;--muted:#9aa3b2;--line:#2a2f38;--head:#1c212a;--accent:#5b8cff;--up:#ff6b6b;--down:#4da3ff;--card:#1a1f27;}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",Roboto,sans-serif;line-height:1.6}}
.wrap{{max-width:960px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:1.7rem;margin:0 0 4px}}h2{{font-size:1.2rem;margin:38px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--accent)}}
.sub{{color:var(--muted);font-size:.9rem}}
.badge{{display:inline-block;background:var(--accent);color:#fff;border-radius:6px;padding:2px 9px;font-size:.8rem;margin-right:6px}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}
.kpi{{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.kpi .lab{{font-size:.8rem;color:var(--muted)}}.kpi .val{{font-size:1.35rem;font-weight:700;margin-top:2px}}
.kpi .chg{{font-size:.85rem;font-weight:600}}
.up{{color:var(--up)}}.down{{color:var(--down)}}
table{{width:100%;border-collapse:collapse;font-size:.9rem;margin:8px 0;overflow-x:auto;display:block}}
@media(min-width:640px){{table{{display:table}}}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{background:var(--head);font-weight:600;font-size:.82rem;color:var(--muted)}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.note{{background:var(--card);border-left:4px solid var(--accent);border-radius:8px;padding:14px 18px;margin:14px 0;font-size:.92rem}}
.note ul{{margin:6px 0 0;padding-left:18px}}.note li{{margin:5px 0}}
img.chart{{width:100%;border:1px solid var(--line);border-radius:12px;margin:10px 0;background:#fff}}
.disc{{margin-top:40px;font-size:.8rem;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}}
.small{{color:var(--muted);font-size:.82rem}}
</style></head><body><div class="wrap">

<h1>{name} 종목 분석 보고서</h1>
<div class="sub">
  <span class="badge">종목코드 {stock or 'N/A'}</span>
  <span class="badge">{basis}</span>
  기준: {year}년 반기보고서 · 생성일 {ctx['today']} · 출처 금융감독원 OpenDART
</div>

<div class="kpis">{ctx['kpi_html']}</div>

<h2>1. 반기 실적 요약 ({year} vs {py})</h2>
<p class="small">단위: 억원 · 3M = 당분기(3개월) · 누적 = 상반기 누적(6개월)</p>
<table>
<thead><tr><th>항목</th><th class="num">{year} 3M</th><th class="num">{py} 3M</th><th class="num">YoY</th>
<th class="num">{year} 누적</th><th class="num">{py} 누적</th><th class="num">YoY</th></tr></thead>
<tbody>{rows}</tbody></table>

<table>
<thead><tr><th>수익성(누적)</th><th class="num">{year}</th><th class="num">{py}</th><th class="num">변화</th></tr></thead>
<tbody>{mrows}</tbody></table>

<h2>2. 판매관리비 세부 분해 ({year} vs {py}, 누적)</h2>
<p class="small">판관비 총계 {fmt(ctx['sga_total_prev'])}억 → {fmt(ctx['sga_total_cur'])}억
(증감 {'+' if ctx['sga_inc']>=0 else ''}{fmt(ctx['sga_inc'])}억) · 증가액順 상위 항목</p>
<table>
<thead><tr><th>항목</th><th class="num">{year}</th><th class="num">{py}</th>
<th class="num">증감</th><th class="num">YoY</th><th class="num">증가 기여도</th></tr></thead>
<tbody>{sga_rows}</tbody></table>

<h2>3. 5개년 추이</h2>
<img class="chart" alt="실적 추이" src="data:image/png;base64,{ctx['chart_perf']}">
<img class="chart" alt="{driver} 추이" src="data:image/png;base64,{ctx['chart_driver']}">
<table>
<thead><tr><th>연도</th><th class="num">매출액</th><th class="num">판관비</th><th class="num">영업이익</th>
<th class="num">판관비율</th><th class="num">영업이익률</th><th class="num">판관비 YoY</th></tr></thead>
<tbody>{tr_rows}</tbody></table>

<h2>4. 종합 분석</h2>
<div class="note"><ul>{narrative}</ul></div>

<div class="disc">
※ 본 보고서는 금융감독원 OpenDART 공시 데이터를 자동 집계·정리한 자료입니다.
수치의 단순 정리·해석을 목적으로 하며, 특정 종목의 매수/매도 등 투자 판단이나
투자 자문을 제공하지 않습니다. 투자의 최종 판단과 책임은 이용자 본인에게 있습니다.
</div>
</div></body></html>"""


# ── 오케스트레이션 ─────────────────────────────────────────
def make_report(name_or_code, base_year=None, fs_div="CFS", outdir="."):
    import OpenDartReader
    from datetime import date
    api = os.environ.get("OPENDART_API_KEY")
    if not api:
        raise SystemExit("환경변수 OPENDART_API_KEY 가 필요합니다.")
    dart = OpenDartReader(api)

    corp_code, corp_name, stock = resolve_corp(dart, name_or_code)
    start = base_year or date.today().year
    year, df = latest_half_year(dart, corp_code, start, fs_div)
    py = year - 1

    # 1) 손익 요약
    isx = income_statement(df)

    # 2) 판관비 분해 (당해 vs 전년)
    sga_cur, tot_cur = sga_breakdown(dart, corp_code, year, fs_div)
    sga_prev, tot_prev = sga_breakdown(dart, corp_code, py, fs_div)
    inc_total = (tot_cur - tot_prev) if (tot_cur == tot_cur and tot_prev == tot_prev) else float("nan")
    sga_sorted = []
    for k, cur in sga_cur.items():
        prev = sga_prev.get(k, float("nan"))
        inc = cur - prev if prev == prev else float("nan")
        sga_sorted.append(dict(name=k, cur=cur, prev=prev, inc=inc,
                               yoy=pct(cur, prev),
                               contrib=(inc / inc_total * 100 if inc_total and inc == inc else float("nan"))))
    sga_sorted.sort(key=lambda d: (d["inc"] if d["inc"] == d["inc"] else -9e9), reverse=True)
    driver = sga_sorted[0]["name"] if sga_sorted else "광고선전비"

    # 3) 5개년 추이 (매출/판관비/영업이익 + driver)
    years, rev_s, sga_s, op_s, drv_s = [], [], [], [], []
    for y in range(year - 4, year + 1):
        try:
            d = retry(dart.finstate_all, corp_code, y, REPRT_HALF, fs_div=fs_div)
        except Exception:
            d = None
        if d is None or len(d) == 0:
            continue
        rev = is_by_id(d, [ID_REVENUE], "thstrm_add_amount")
        sga = is_by_id(d, IS_ITEMS[3][1], "thstrm_add_amount")
        op = is_by_id(d, IS_ITEMS[4][1], "thstrm_add_amount")
        items, _ = sga_breakdown(dart, corp_code, y, fs_div)
        years.append(y); rev_s.append(rev); sga_s.append(sga); op_s.append(op)
        drv_s.append(items.get(driver.replace(" ", ""), float("nan")))

    # 4) 차트
    set_korean_font()
    c_perf = chart_perf(years, rev_s, op_s, sga_s, corp_name)
    c_drv = chart_driver(years, drv_s, rev_s, driver, corp_name)

    # 5) KPI 카드
    def kpi(lbl, key):
        d = isx[key]
        y = pct(d["a_cur"], d["a_prev"])
        cls = "up" if y >= 0 else "down"
        s = "+" if y >= 0 else ""
        chg = f'<span class="chg {cls}">{s}{y:.1f}% YoY</span>' if y == y else ""
        return f'<div class="kpi"><div class="lab">{lbl} (누적)</div><div class="val">{fmt(d["a_cur"])}억</div>{chg}</div>'
    kpi_html = "".join(kpi(l, l) for l in ["매출액", "영업이익", "당기순이익", "판매관리비"])

    # 6) 자동 서술
    op = isx["영업이익"]; rev = isx["매출액"]
    op_yoy = pct(op["a_cur"], op["a_prev"]); rev_yoy = pct(rev["a_cur"], rev["a_prev"])
    opm_c = op["a_cur"] / rev["a_cur"] * 100 if rev["a_cur"] else float("nan")
    opm_p = op["a_prev"] / rev["a_prev"] * 100 if rev["a_prev"] else float("nan")
    top = sga_sorted[0] if sga_sorted else None
    nn = []
    nn.append(f"<li>{year} 상반기 누적 매출은 <b>{fmt(rev['a_cur'])}억원</b>"
              f"({'+' if rev_yoy>=0 else ''}{fmt(rev_yoy,1)}% YoY), "
              f"영업이익은 <b>{fmt(op['a_cur'])}억원</b>"
              f"({'+' if op_yoy>=0 else ''}{fmt(op_yoy,1)}% YoY), "
              f"영업이익률은 {fmt(opm_p,1)}% → <b>{fmt(opm_c,1)}%</b>로 변동.</li>")
    if top and top["inc"] == top["inc"]:
        nn.append(f"<li>판관비는 {fmt(tot_prev)}억 → <b>{fmt(tot_cur)}억</b>"
                  f"({'+' if inc_total>=0 else ''}{fmt(inc_total)}억) 변동했고, "
                  f"이 중 <b>{top['name']}</b>가 {'+' if top['inc']>=0 else ''}{fmt(top['inc'])}억"
                  f"({fmt(top['yoy'],1)}%)으로 판관비 증감의 <b>{fmt(top['contrib'],1)}%</b>를 설명.</li>")
    if len(sga_sorted) >= 3:
        t3 = sga_sorted[:3]
        s3 = sum(x["contrib"] for x in t3 if x["contrib"] == x["contrib"])
        nn.append(f"<li>상위 3개 항목({', '.join(x['name'] for x in t3)})이 "
                  f"판관비 증가의 <b>{fmt(s3,1)}%</b>를 차지.</li>")
    if drv_s and drv_s[0] == drv_s[0] and drv_s[-1] == drv_s[-1] and rev_s[0] and rev_s[-1]:
        r0 = drv_s[0] / rev_s[0] * 100; r1 = drv_s[-1] / rev_s[-1] * 100
        nn.append(f"<li>'{driver}'의 매출 대비 비중은 {years[0]}년 {fmt(r0,1)}% → "
                  f"{years[-1]}년 <b>{fmt(r1,1)}%</b>로 변화(5개년).</li>")

    ctx = dict(
        name=corp_name, stock=stock, basis=("연결" if fs_div == "CFS" else "별도"),
        year=year, today=str(date.today()), isd=isx,
        kpi_html=kpi_html,
        sga_sorted=sga_sorted, sga_total_cur=tot_cur, sga_total_prev=tot_prev, sga_inc=inc_total,
        trend=dict(years=years, rev=rev_s, sga=sga_s, op=op_s),
        chart_perf=c_perf, chart_driver=c_drv, driver=driver, narrative="".join(nn),
    )
    html = build_html(ctx)
    safe = corp_name.replace("/", "_").replace(" ", "")
    path = os.path.join(outdir, f"{safe}_분석보고서_{year}반기.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[완료] {corp_name}({stock}) — {year} 반기 · 항목 {len(sga_cur)}개 · driver={driver}")
    print(f"[저장] {path}")
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('사용법: python3 stock_report.py "종목명" [연도] [CFS|OFS]')
        sys.exit(1)
    nm = sys.argv[1]
    yr = int(sys.argv[2]) if len(sys.argv) > 2 else None
    fd = sys.argv[3] if len(sys.argv) > 3 else "CFS"
    make_report(nm, yr, fd, outdir=os.path.dirname(os.path.abspath(__file__)))
