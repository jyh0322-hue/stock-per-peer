import os, OpenDartReader, requests, time
import pandas as pd
from io import StringIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

dart = OpenDartReader(os.environ["OPENDART_API_KEY"])

def fetch(url):
    for _ in range(4):
        try:
            return requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        except Exception:
            time.sleep(1)
    return ""

def ad_expense(yr):
    """해당 연도 반기보고서 주석에서 광고선전비 당반기(누적) 추출 -> 억원"""
    fs = dart.finstate_all("00150165", yr, "11012", fs_div="CFS")
    rcept = fs.iloc[0]["rcept_no"]
    sd = dart.sub_docs(rcept)
    url = sd[sd["title"].str.contains("연결재무제표 주석")]["url"].iloc[0]
    tables = pd.read_html(StringIO(fetch(url)))
    for t in tables:
        txt = t.to_string()
        if "광고선전비" in txt and ("판매수수료" in txt or "판매촉진비" in txt):
            # 누적 열 인덱스 결정: '누적' > '당반기' > 기본 1열
            cols = ["".join(str(x) for x in (c if isinstance(c, tuple) else (c,))) for c in t.columns]
            ci = next((i for i, c in enumerate(cols) if "누적" in c), None)
            if ci is None:
                ci = next((i for i, c in enumerate(cols) if "당반기" in c or "당기" in c), 1)
            for _, r in t.iterrows():
                if "광고선전비" in str(r.iloc[0]):
                    v = str(r.iloc[ci]).replace(",", "")
                    return float(v) / 1e5
    return float("nan")

REV = {2022: 1145, 2023: 1226, 2024: 2152, 2025: 2120, 2026: 2460}
years = [2022, 2023, 2024, 2025, 2026]
ad = {y: ad_expense(y) for y in years}

print(f"{'연도(반기누적)':<12}{'광고선전비':>10}{'매출액':>9}{'광고비/매출':>11}{'YoY':>9}")
print("-" * 52)
prev = None
ratio = {}
for y in years:
    r = ad[y] / REV[y] * 100
    ratio[y] = r
    yoy = (ad[y] - prev) / prev * 100 if prev else None
    ytxt = f"{yoy:>8.1f}%" if yoy is not None else f"{'-':>9}"
    print(f"{str(y)+' 반기':<12}{ad[y]:>10,.0f}{REV[y]:>9,.0f}{r:>10.1f}%{ytxt}")
    prev = ad[y]

# ── 차트 ──
for cand in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic"]:
    try:
        font_manager.findfont(cand, fallback_to_default=False)
        plt.rcParams["font.family"] = cand
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

fig, ax1 = plt.subplots(figsize=(9, 5.2))
xs = [str(y) for y in years]
vals = [ad[y] for y in years]
bars = ax1.bar(xs, vals, color="#4C78A8", width=0.55, label="광고선전비(억원)")
ax1.set_ylabel("광고선전비 (억원)", color="#4C78A8", fontsize=11)
ax1.tick_params(axis="y", labelcolor="#4C78A8")
ax1.set_ylim(0, max(vals) * 1.25)
for b, v in zip(bars, vals):
    ax1.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, f"{v:,.0f}", ha="center", fontsize=10, color="#2b2b2b")

ax2 = ax1.twinx()
rs = [ratio[y] for y in years]
ax2.plot(xs, rs, color="#E45756", marker="o", linewidth=2.3, label="광고비/매출(%)")
ax2.set_ylabel("광고비 / 매출 (%)", color="#E45756", fontsize=11)
ax2.tick_params(axis="y", labelcolor="#E45756")
ax2.set_ylim(0, max(rs) * 1.35)
for y, rv in zip(xs, rs):
    ax2.text(y, rv + max(rs) * 0.04, f"{rv:.1f}%", ha="center", fontsize=9.5, color="#E45756")

plt.title("브이티 광고선전비 추이 (연결, 반기 누적)  2022~2026", fontsize=13, pad=12)
ax1.set_xlabel("연도 (각 상반기 누적)")
fig.tight_layout()
out = "/Users/yoonhocheon/Documents/주식/브이티/vt_ad_trend.png"
fig.savefig(out, dpi=150)
print("\n차트 저장:", out)
