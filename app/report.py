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


def build_result(target, peers, stats, disclosures, deepdive, insights=None):
    return {
        "target": target,
        "peers": peers,
        "stats": stats,
        "disclosures": disclosures or [],
        "deepdive": deepdive,
        "insights": insights or {"status": "disabled"},
        "chart_per_b64": per_bar_chart_b64(peers, target["stock_code"], stats.get("median")),
    }


