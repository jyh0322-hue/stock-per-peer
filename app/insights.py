"""뉴스·블로그 기반 투자포인트/리스크 요약 — Claude 호출 1회, 출처 매핑 포함.

`claude(prompt) -> str`(JSON 문자열)을 주입 가능. 기본 러너는 anthropic SDK로
ANTHROPIC_API_KEY를 사용해 claude-haiku-4-5-20251001 모델을 1회 호출한다.
"""
import os
import json

SYSTEM = (
    "너는 한국 주식 뉴스 요약 애널리스트다. 아래 번호가 매겨진 뉴스/블로그 항목만 근거로 "
    "투자포인트, 리스크, 증권가 의견을 한국어로 정리한다. 규칙: (1) 제공 항목에만 근거 "
    "(2) 각 포인트/리스크/증권가 의견에 근거 항목 번호를 sources 배열로 명기 "
    "(3) 자료에 없으면 항목을 만들지 말 것 (4) 매수/매도 권유·목표주가 단정 금지, 사실·전망을 중립 서술 "
    "(5) 매수/매도 권유를 하지 말 것. 증권사 목표주가·투자의견이 기사에 있으면 "
    "'어느 매체/증권사가 무엇을 말했다'는 형태의 인용으로만 전달할 것. "
    "(6) 제공된 항목에 없는 수치나 전망을 만들어내지 말 것. "
    "(7) 반드시 JSON만 출력."
)


def _src_obj(item, n):
    return {"n": n, "title": item.get("title"), "source": item.get("source"),
            "date": item["published"].strftime("%Y-%m-%d") if item.get("published") else "",
            "url": item.get("url"), "kind": item.get("kind", "news")}


def build_prompt(company, items):
    lines = ["[대상 종목] %s" % company, "", "[항목]"]
    for i, it in enumerate(items, 1):
        lines.append("%d. (%s) %s — %s" % (i, it.get("source"), it.get("title"), it.get("snippet")))
    lines.append("")
    lines.append('출력 JSON 스키마: {"investment_points":[{"text","sources":[번호]}],'
                 '"risks":[{"text","sources":[번호]}],'
                 '"analyst_views":[{"text","sources":[번호]}](증권가/애널리스트 의견이 '
                 '항목에 언급된 경우만, 없으면 빈 배열),'
                 '"overall":"2~3문장"}')
    return "\n".join(lines)


def _default_claude(prompt):
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1500,
        system=SYSTEM, messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _all_sources(items):
    return [_src_obj(it, i) for i, it in enumerate(items, 1)]


def summarize(items, company, claude=None, as_of=None):
    sources = _all_sources(items)
    base = {"as_of": as_of, "window_days": 30, "investment_points": [], "risks": [],
            "analyst_views": [], "overall": "", "sources": sources}
    if not items:
        base["status"] = "no_data"
        return base
    runner = claude or _default_claude
    try:
        raw = runner(build_prompt(company, items))
        parsed = json.loads(raw)
    except Exception:
        base["status"] = "disabled"
        return base

    def _map(points):
        out = []
        for p in points or []:
            srcs = [sources[n - 1] for n in p.get("sources", []) if 1 <= n <= len(sources)]
            out.append({"text": p.get("text", ""), "sources": srcs})
        return out

    base["investment_points"] = _map(parsed.get("investment_points"))
    base["risks"] = _map(parsed.get("risks"))
    # analyst_views는 애초 없던 필드(구버전 프롬프트/키 부재 응답 등)일 수 있으므로
    # parsed에 키 자체가 없어도(.get이 None) _map이 빈 배열로 정상 처리한다.
    base["analyst_views"] = _map(parsed.get("analyst_views"))
    base["overall"] = parsed.get("overall", "")
    base["status"] = "ok"
    return base
