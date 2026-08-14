import os
import OpenDartReader
import pandas as pd

dart = OpenDartReader(os.environ["OPENDART_API_KEY"])

def num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return float("nan")

def eok(x):
    v = num(x)
    return v / 1e8 if v == v else float("nan")  # 억원

# 계정명으로 한 행 찾기 (포괄손익계산서 우선)
def pick(df, contains, sj="포괄손익계산서"):
    sub = df[df["sj_nm"] == sj]
    m = sub[sub["account_nm"].str.replace(" ", "").str.contains(contains.replace(" ", ""), na=False)]
    return m.iloc[0] if len(m) else None

KEY = [
    ("매출액", "매출액"),
    ("매출원가", "매출원가"),
    ("매출총이익", "매출총이익"),
    ("판매및일반관리비", "판매 및 일반관리비"),
    ("영업이익", "영업이익"),
    ("법인세차감전순이익", "법인세비용차감전순이익"),
    ("당기순이익", "당기순이익"),
]

# ── 1) 2026 반기 리포트로 전년동기 비교표 (3개월 / 누적 6개월) ──
df26 = dart.finstate_all(corp="브이티", bsns_year=2026, reprt_code="11012", fs_div="CFS")

print("=" * 78)
print("[1] 2026 반기 vs 2025 반기 (연결) — 단위: 억원")
print("=" * 78)
hdr = f"{'항목':<14}{'26 3M':>10}{'25 3M':>10}{'YoY%':>8}   {'26 누적':>10}{'25 누적':>10}{'YoY%':>8}"
print(hdr)
print("-" * 78)
def yoy(cur, prev):
    c, p = num(cur), num(prev)
    if p and p == p and p != 0:
        return (c - p) / abs(p) * 100
    return float("nan")
for label, key in KEY:
    r = pick(df26, key)
    if r is None:
        continue
    q_cur, q_prev = eok(r["thstrm_amount"]), eok(r["frmtrm_q_amount"])
    a_cur, a_prev = eok(r["thstrm_add_amount"]), eok(r["frmtrm_add_amount"])
    print(f"{label:<14}{q_cur:>10,.0f}{q_prev:>10,.0f}{yoy(r['thstrm_amount'], r['frmtrm_q_amount']):>8.1f}   "
          f"{a_cur:>10,.0f}{a_prev:>10,.0f}{yoy(r['thstrm_add_amount'], r['frmtrm_add_amount']):>8.1f}")

# 마진율 (누적 기준)
def add_eok(df, key):
    r = pick(df, key); return eok(r["thstrm_add_amount"]) if r is not None else float("nan")
def add_eok_prev(df, key):
    r = pick(df, key); return eok(r["frmtrm_add_amount"]) if r is not None else float("nan")
rev_c, rev_p = add_eok(df26, "매출액"), add_eok_prev(df26, "매출액")
op_c, op_p = add_eok(df26, "영업이익"), add_eok_prev(df26, "영업이익")
sga_c, sga_p = add_eok(df26, "판매 및 일반관리비"), add_eok_prev(df26, "판매 및 일반관리비")
gp_c, gp_p = add_eok(df26, "매출총이익"), add_eok_prev(df26, "매출총이익")
print("-" * 78)
print(f"{'매출총이익률':<14}{'':>10}{'':>10}{'':>8}   {gp_c/rev_c*100:>10.1f}{gp_p/rev_p*100:>10.1f}{'%p':>8}")
print(f"{'영업이익률':<14}{'':>10}{'':>10}{'':>8}   {op_c/rev_c*100:>10.1f}{op_p/rev_p*100:>10.1f}{'%p':>8}")
print(f"{'판관비율':<14}{'':>10}{'':>10}{'':>8}   {sga_c/rev_c*100:>10.1f}{sga_p/rev_p*100:>10.1f}{'%p':>8}")

# ── 2) 판관비 추이 (H1 누적, 2023~2026) — 여러 반기보고서에서 수집 ──
print()
print("=" * 78)
print("[2] 판관비 추이 (반기 누적 기준) — 단위: 억원")
print("=" * 78)
trend = {}  # year -> (매출, 판관비)
for yr in (2024, 2025, 2026):
    try:
        d = dart.finstate_all(corp="브이티", bsns_year=yr, reprt_code="11012", fs_div="CFS")
    except Exception:
        continue
    rev = pick(d, "매출액"); sga = pick(d, "판매 및 일반관리비")
    if rev is not None and sga is not None:
        trend[yr] = (eok(rev["thstrm_add_amount"]), eok(sga["thstrm_add_amount"]))
        # 전년 누적도 확보 (전년도 report가 없을 때 대비)
        py = yr - 1
        if py not in trend:
            rp, sp = eok(rev["frmtrm_add_amount"]), eok(sga["frmtrm_add_amount"])
            if rp == rp and sp == sp:
                trend[py] = (rp, sp)

print(f"{'연도(반기누적)':<14}{'매출액':>12}{'판관비':>12}{'판관비율':>10}{'판관비YoY%':>12}")
print("-" * 78)
prev_sga = None
for yr in sorted(trend):
    rev, sga = trend[yr]
    ratio = sga / rev * 100 if rev else float("nan")
    g = (sga - prev_sga) / prev_sga * 100 if prev_sga else float("nan")
    gtxt = f"{g:>12.1f}" if g == g else f"{'-':>12}"
    print(f"{str(yr)+' 반기':<14}{rev:>12,.0f}{sga:>12,.0f}{ratio:>9.1f}%{gtxt}")
    prev_sga = sga
