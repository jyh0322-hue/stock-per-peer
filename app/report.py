import base64
from io import BytesIO
from typing import List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


def _set_korean_font():
    for cand in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic"]:
        try:
            font_manager.findfont(cand, fallback_to_default=False)
            plt.rcParams["font.family"] = cand
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def _fig_b64(fig):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def per_bar_chart_b64(peers, target_code, median):
    _set_korean_font()
    labels, vals, colors = [], [], []
    for p in peers:
        if p.get("per_op") is None:
            continue
        labels.append(p["name"])
        vals.append(p["per_op"])
        colors.append("#E45756" if p["stock_code"] == target_code else "#4C78A8")
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.bar(range(len(vals)), vals, 0.6, color=colors)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("PER(영업이익 기준, 연환산, 배)")
    if median is not None:
        ax.axhline(median, ls="--", color="#888", lw=1.2)
        ax.text(len(vals) - 0.5, median, "  업종 중앙값 %.1f" % median, va="bottom", fontsize=8.5)
    for i, v in enumerate(vals):
        ax.text(i, v, "%.1f" % v, ha="center", va="bottom", fontsize=9)
    ax.set_title("업종 PEER PER 비교", fontsize=12)
    fig.tight_layout()
    return _fig_b64(fig)


def build_result(target, peers, stats, disclosures, deepdive):
    return {
        "target": target,
        "peers": peers,
        "stats": stats,
        "disclosures": disclosures or [],
        "deepdive": deepdive,
        "chart_per_b64": per_bar_chart_b64(peers, target["stock_code"], stats.get("median")),
    }


def _fmt(v, dp=1):
    if isinstance(v, (int, float)) and v == v:  # NaN 제외
        return format(v, ",.%df" % dp)
    return "-"


def _per_cell(v):
    return format(v, ",.1f") if isinstance(v, (int, float)) else "N/A(적자)"


def render_html(result):
    t = result["target"]
    rows = ""
    for p in result["peers"]:
        hl = ' style="font-weight:700;background:#fff3f0"' if p.get("is_target") else ""
        rows += (
            "<tr%s><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>\n"
            % (hl, p["name"], _fmt(p["market_cap"], 0), _fmt(p["op_3m"], 0),
               _fmt(p["op_annualized"], 0), _per_cell(p.get("per_op")), _per_cell(p.get("krx_per")))
        )
    disc = "".join("<li>[%s] <b>%s</b> <span class='tag'>%s</span></li>"
                   % (d["date"], d["title"], d["type"]) for d in result["disclosures"][:20])
    s = result["stats"]
    return """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} PER·PEER 분석</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;max-width:960px;margin:0 auto;padding:24px;line-height:1.6}}
table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{padding:8px 10px;border-bottom:1px solid #e5e7eb;white-space:nowrap}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
th{{background:#f8fafc;color:#6b7280;font-size:.82rem}}
img.chart{{width:100%;border:1px solid #eee;border-radius:12px;margin:10px 0}}
.tag{{font-size:.72rem;color:#fff;background:#5b8cff;border-radius:5px;padding:1px 6px}}
.kpi{{display:inline-block;border:1px solid #e5e7eb;border-radius:12px;padding:12px 16px;margin:6px}}
.disc{{margin-top:32px;font-size:.8rem;color:#6b7280;border-top:1px solid #eee;padding-top:12px}}
</style></head><body>
<h1>{name} <small>({code})</small> PER·PEER 분석</h1>
<div>
  <span class="kpi">시총 <b>{mcap}</b>억</span>
  <span class="kpi">연환산 영업이익 <b>{opa}</b>억</span>
  <span class="kpi">PER(영업이익 기준, 연환산) <b>{per}</b></span>
  <span class="kpi">업종 중앙값 <b>{med}</b> · 순위 {rank}/{total}</span>
</div>
<h2>업종 PEER 비교 (시총 상위 {peern})</h2>
<img class="chart" src="data:image/png;base64,{chart}">
<table><thead><tr><th>종목</th><th class="num">시총(억)</th><th class="num">최근분기 영업익(억)</th>
<th class="num">연환산(억)</th><th class="num">PER(영업이익)</th><th class="num">KRX PER</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>최근 공시</h2><ul>{disc}</ul>
<div class="disc">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.
PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4).</div>
</body></html>""".format(
        name=t["name"], code=t.get("stock_code", ""),
        mcap=_fmt(t["market_cap"], 0), opa=_fmt(t.get("op_annualized"), 0),
        per=_per_cell(t.get("per_op")), med=_fmt(s.get("median")),
        rank=s.get("rank") or "-", total=s.get("count") or "-",
        peern=len(result["peers"]), chart=result["chart_per_b64"], rows=rows, disc=disc)
