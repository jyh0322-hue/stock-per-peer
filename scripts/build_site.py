#!/usr/bin/env python3
"""GitHub Pages용 정적 리포트 생성기.

종목명을 하나 이상 받아 기존 6단계 파이프라인(app/pipeline.py: run_analysis)을
그대로 돌리고, 그 결과를 web/render.js + web/styles.css를 인라인한 자기완결형
(self-contained) HTML 리포트로 저장한다. 인덱스(index.html)는 출력 디렉터리에
있는 모든 <safe-name>.json 메타데이터로부터 매번 다시 만들어지므로, 이전 실행에서
만들어진 리포트도 계속 목록에 남는다(누적).

사용법:
    python3 scripts/build_site.py --out site 종목명 [종목명...]
    python3 scripts/build_site.py --out site --watchlist watchlist.txt
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# 프로젝트 루트를 sys.path에 넣어 `python3 scripts/build_site.py`로 직접 실행해도
# `app` 패키지를 import할 수 있게 한다.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import pipeline  # noqa: E402  (app.config가 여기서 .env를 로드한다)
from app.dart_client import DartClient  # noqa: E402
from app.krx_client import KrxClient  # noqa: E402
from app.news_client import NewsClient  # noqa: E402
from app import insights  # noqa: E402

WEB_DIR = os.path.join(ROOT_DIR, "web")
KST = timezone(timedelta(hours=9))

_WS_RE = re.compile(r"\s+")
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')
_MULTI_US_RE = re.compile(r"_+")


def safe_filename(name):
    """URL/파일시스템에 안전한 파일명으로 변환한다. 한글은 그대로 유지하고,
    공백은 밑줄로, `/ \\ : * ? " < > |`는 밑줄로 치환한다."""
    s = _WS_RE.sub("_", (name or "").strip())
    s = _UNSAFE_RE.sub("_", s)
    s = _MULTI_US_RE.sub("_", s).strip("_")
    return s or "unnamed"


def escape_html(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;") \
        .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_watchlist(path):
    names = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            names.append(line)
    return names


def per_display(per_op, per_status):
    if isinstance(per_op, (int, float)):
        return "%.1f배" % per_op
    return "N/A(적자)" if per_status == "loss" else "데이터 없음"


def build_report_html(res, sector, generated_at):
    styles = read_text(os.path.join(WEB_DIR, "styles.css"))
    render_js = read_text(os.path.join(WEB_DIR, "render.js"))
    target = res.get("target") or {}
    name = target.get("name") or "?"
    stock_code = target.get("stock_code") or ""
    title = "%s(%s) PER·PEER 분석 리포트 · %s" % (name, stock_code, generated_at)
    # <script> 태그 안에 JSON을 그대로 심을 때 문자열 안에 "</script"가 섞여 있으면
    # 태그가 조기 종료될 수 있으므로 방어적으로 이스케이프한다.
    result_json = json.dumps(res, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{styles}
</style>
</head>
<body>
<div class="page">
  <p><a href="index.html">&larr; 전체 리포트 목록</a></p>
  <section id="view-result" class="view">
    <div id="result"></div>
  </section>
  <p class="disclaimer">생성 시각: {generated_at} KST{sector_line}</p>
</div>
<script>
{render_js}
</script>
<script>
window.__RESULT__ = {result_json};
render(window.__RESULT__);
</script>
</body>
</html>
""".format(
        title=escape_html(title),
        styles=styles,
        generated_at=escape_html(generated_at),
        sector_line=(" · 업종: " + escape_html(sector)) if sector else "",
        render_js=render_js,
        result_json=result_json,
    )


def load_metadata(out_dir):
    """out_dir의 모든 <safe-name>.json을 읽어 리스트로 반환한다. 이번 실행에서
    새로 만든 것뿐 아니라 이전 실행에서 만들어진 것도 포함된다(누적의 핵심)."""
    items = []
    for fn in sorted(os.listdir(out_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(out_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        meta["_html_file"] = fn[:-5] + ".html"
        if os.path.exists(os.path.join(out_dir, meta["_html_file"])):
            items.append(meta)
    items.sort(key=lambda m: m.get("generated_at") or "", reverse=True)
    return items


def build_index_html(items):
    styles = read_text(os.path.join(WEB_DIR, "styles.css"))
    rows = []
    for m in items:
        per_txt = per_display(m.get("per_op"), m.get("per_status"))
        sector_txt = (" · " + escape_html(m["sector"])) if m.get("sector") else ""
        rows.append(
            '<li><span class="d">{date}</span><span class="t">'
            '<a href="{href}">{name}</a> <span class="code">({code})</span>{sector}</span>'
            '<span class="type reg">PER {per}</span></li>'.format(
                date=escape_html(m.get("generated_at") or "-"),
                href=escape_html(m.get("_html_file", "#")),
                name=escape_html(m.get("name") or "?"),
                code=escape_html(m.get("stock_code") or ""),
                sector=sector_txt,
                per=escape_html(per_txt),
            )
        )
    body = "\n".join(rows) if rows else "<li>아직 생성된 리포트가 없습니다.</li>"
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DART 종목 PER·PEER 분석 — 리포트 목록</title>
<style>
{styles}
.disc a{{color:inherit;font-weight:700;text-decoration:none}}
.disc a:hover{{color:var(--brand)}}
</style>
</head>
<body>
<div class="page">
  <div class="brand-row"><h1>📊 종목 PER·PEER 분석 — 리포트 목록</h1></div>
  <p class="sub">GitHub Actions로 생성된 정적 리포트 모음입니다. 최신 순으로 정렬됩니다.</p>
  <ul class="disc">
{body}
  </ul>
  <div class="disclaimer">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.</div>
</div>
</body>
</html>
""".format(styles=styles, body=body)


def write_report(out_dir, res, sector, generated_at):
    target = res.get("target") or {}
    name = target.get("name") or "unknown"
    safe = safe_filename(name)
    html = build_report_html(res, sector, generated_at)
    meta = {
        "name": name,
        "stock_code": target.get("stock_code"),
        "per_op": target.get("per_op"),
        "per_status": target.get("per_status"),
        "generated_at": generated_at,
        "sector": sector,
    }
    with open(os.path.join(out_dir, safe + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(out_dir, safe + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return safe


def run(names, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    dart = DartClient()
    krx = KrxClient()
    news = NewsClient()
    insights_fn = insights.summarize

    ok, fail = 0, 0
    for name in names:
        print("[build_site] %s 분석 시작..." % name, flush=True)
        try:
            res = pipeline.run_analysis(name, dart, krx, news=news, insights_fn=insights_fn)
            sector = None
            try:
                sector = krx.sector_of(res["target"]["stock_code"])
            except Exception:
                sector = None
            generated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            safe = write_report(out_dir, res, sector, generated_at)
            print("[build_site] %s 완료 -> %s.html (PER=%s)" % (
                name, safe, per_display(res["target"].get("per_op"), res["target"].get("per_status"))))
            ok += 1
        except Exception as e:
            print("[build_site] %s 실패: %s" % (name, e), file=sys.stderr)
            fail += 1

    items = load_metadata(out_dir)
    index_html = build_index_html(items)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    print("[build_site] index.html 갱신 완료 (총 %d개 리포트, 이번 실행 성공 %d / 실패 %d)" % (
        len(items), ok, fail))

    if ok == 0 and fail > 0:
        print("[build_site] 모든 종목 분석이 실패했습니다.", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="DART PER·PEER 정적 리포트 생성기")
    parser.add_argument("names", nargs="*", help="종목명 (복수 가능)")
    parser.add_argument("--out", default="site", help="출력 디렉터리 (기본: site)")
    parser.add_argument("--watchlist", help="종목명 목록 파일 경로 (한 줄에 하나, # 주석 무시)")
    args = parser.parse_args(argv)

    names = list(args.names)
    if args.watchlist:
        names.extend(read_watchlist(args.watchlist))
    # 중복 제거(순서 유지) — 같은 이름이 여러 번 들어와도 한 번만 분석한다.
    seen = set()
    uniq_names = []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq_names.append(n)

    if not uniq_names:
        parser.error("종목명을 하나 이상 지정하거나 --watchlist를 사용하세요.")

    return run(uniq_names, args.out)


if __name__ == "__main__":
    sys.exit(main())
