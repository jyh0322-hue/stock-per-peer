"""뉴스 수집: 구글 뉴스 RSS(기본) + 네이버 뉴스 검색 HTML(보조), 최근 1개월 필터.

네이버 검색 API 키 발급이 불가하여 API 키가 필요 없는 두 소스를 사용한다.
- 구글 뉴스 RSS: https://news.google.com/rss/search?q=...  (표준 RSS, stdlib로 파싱)
- 네이버 뉴스 검색 HTML: https://search.naver.com/search.naver?where=news&query=...
  (마크업이 자주 바뀌므로 방어적으로 파싱: 실패 시 예외를 삼키고 빈 리스트 반환)
"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

try:
    from lxml import html as lxml_html
except Exception:  # pragma: no cover - lxml는 requirements에 포함되어 항상 설치됨
    lxml_html = None

from app import config

KST = timezone(timedelta(hours=9))

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
NAVER_NEWS_SEARCH_URL = "https://search.naver.com/search.naver?where=news&query={q}"

_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _strip(s):
    """HTML 태그 제거 + 엔티티 언이스케이프 + 공백 트림."""
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _norm(s):
    """공백 제거 정규화(중복 판정/관련성 판정용)."""
    return re.sub(r"\s+", "", s or "")


_LEGAL_ENTITY_PREFIXES = ("주식회사", "(주)")


def _clean_company_name(name):
    """DART 법인 정식명칭에서 "(주)"/"주식회사"를 제거해 매칭용 키를 만든다.
    뉴스 헤드라인은 거의 항상 법인 정식명칭이 아니라 이 형태로 회사를 지칭한다."""
    out = name or ""
    for token in _LEGAL_ENTITY_PREFIXES:
        out = out.replace(token, "")
    return out.strip()


def parse_rss_date(pubdate):
    """RFC1123 형식(예: 'Mon, 11 Aug 2026 09:30:00 GMT') 파싱. 실패 시 None."""
    if not pubdate:
        return None
    try:
        d = parsedate_to_datetime(pubdate)
        if d is None:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


_REL_MIN = re.compile(r"(\d+)\s*분\s*전")
_REL_HOUR = re.compile(r"(\d+)\s*시간\s*전")
_REL_DAY = re.compile(r"(\d+)\s*일\s*전")
_ABS_DOT = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?")


def parse_relative_date(text, now):
    """네이버 표기('N분 전'/'N시간 전'/'N일 전'/'YYYY.MM.DD.') 파싱. 실패 시 None."""
    if not text:
        return None
    text = text.strip()

    m = _REL_MIN.search(text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    m = _REL_HOUR.search(text)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    m = _REL_DAY.search(text)
    if m:
        return now - timedelta(days=int(m.group(1)))

    m = _ABS_DOT.search(text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            tzinfo = now.tzinfo or KST
            return datetime(y, mo, d, tzinfo=tzinfo)
        except Exception:
            return None

    return None


def filter_recent(items, days, now):
    """published가 None이거나 cutoff 이전인 항목 제외."""
    cutoff = now - timedelta(days=days)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=KST)
    out = []
    for it in items:
        p = it.get("published")
        if p is None:
            continue
        if p.tzinfo is None:
            p = p.replace(tzinfo=KST)
        if p >= cutoff:
            out.append(it)
    return out


def dedup(items):
    """정규화된 title+url 기준 중복 제거(첫 항목 유지)."""
    seen = set()
    out = []
    for it in items:
        key = _norm(it.get("title")) + "|" + (it.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def parse_google_rss(xml_text):
    """구글 뉴스 RSS XML 파싱. 파싱 실패 시 빈 리스트."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    out = []
    try:
        for item in root.iter("item"):
            title_raw = item.findtext("title") or ""
            title = _strip(title_raw)
            link = (item.findtext("link") or "").strip()
            published = parse_rss_date(item.findtext("pubDate"))
            snippet = _strip(item.findtext("description"))

            source_el = item.find("source")
            if source_el is not None and (source_el.text or "").strip():
                source = _strip(source_el.text)
            elif " - " in title:
                source = title.rsplit(" - ", 1)[1].strip()
            else:
                source = ""

            out.append({
                "title": title,
                "snippet": snippet,
                "url": link,
                "source": source,
                "published": published,
            })
    except Exception:
        return []
    return out


def parse_naver_html(html_text, now):
    """네이버 뉴스 검색 결과 HTML 파싱(방어적). 실패 시 빈 리스트."""
    if not html_text or lxml_html is None:
        return []
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []

    try:
        anchors = doc.xpath('//a[contains(concat(" ", normalize-space(@class), " "), " news_tit ")]')
    except Exception:
        anchors = []

    out = []
    for a in anchors:
        try:
            title = _strip(a.get("title") or a.text_content())
            url = (a.get("href") or "").strip()
            if not title or not url:
                continue

            # 기사 하나를 감싸는 상위 컨테이너를 찾아 그 안에서 스니펫/언론사/날짜를 탐색
            container = a
            for _ in range(6):
                parent = container.getparent()
                if parent is None:
                    break
                container = parent
                cls = container.get("class") or ""
                if "news_wrap" in cls or "bx" == cls.strip():
                    break

            snippet = ""
            try:
                dsc_nodes = container.xpath(
                    './/a[contains(@class,"api_txt_lines")] | .//div[contains(@class,"news_dsc")]'
                )
                if dsc_nodes:
                    snippet = _strip(dsc_nodes[0].text_content())
            except Exception:
                snippet = ""

            source = ""
            published = None
            try:
                info_nodes = container.xpath(
                    './/a[contains(@class,"info press")] | .//span[contains(@class,"info")]'
                    ' | .//span[contains(@class,"date")]'
                )
                for node in info_nodes:
                    t = (node.text_content() or "").strip()
                    if not t:
                        continue
                    if published is None:
                        cand = parse_relative_date(t, now)
                        if cand is not None:
                            published = cand
                            continue
                    if not source and not re.search(r"\d", t) and "언론사" not in t and "선택" not in t:
                        source = t
            except Exception:
                pass

            out.append({
                "title": title,
                "snippet": snippet,
                "url": url,
                "source": source or "네이버뉴스",
                "published": published,
            })
        except Exception:
            continue

    return out


class NewsClient:
    def __init__(self, fetch=None):
        self._fetch = fetch or self._http_fetch

    @staticmethod
    def _http_fetch(url):
        r = requests.get(url, headers=_UA_HEADERS, timeout=15)
        r.raise_for_status()
        return r.text

    def fetch_recent(self, company, stock_name, days=30, now=None):
        now = now or datetime.now(KST)
        # 뉴스 헤드라인은 DART 법인 정식명칭이 아니라 KRX 상장명으로 회사를 지칭하므로,
        # 검색 질의어는 stock_name(있으면)을 우선한다.
        query = stock_name or company
        if not query:
            return []

        raw = []

        try:
            rss_url = GOOGLE_NEWS_RSS_URL.format(q=quote(query))
            rss_text = self._fetch(rss_url)
            raw.extend(parse_google_rss(rss_text))
        except Exception:
            pass

        if len(raw) < 5:
            try:
                naver_url = NAVER_NEWS_SEARCH_URL.format(q=quote(query))
                naver_text = self._fetch(naver_url)
                raw.extend(parse_naver_html(naver_text, now))
            except Exception:
                pass

        # 관련성 판정은 두 이름(법인 정식명칭에서 "(주)"/"주식회사"를 뗀 키, KRX 상장명 키)
        # 중 하나만 맞아도 통과시킨다 — 둘 중 하나가 늘 실제 매칭 대상과 어긋나기 쉽다.
        company_key = _norm(_clean_company_name(company))
        stock_key = _norm(stock_name)
        relevant = [
            it for it in raw
            if (company_key and (company_key in _norm(it.get("title")) or company_key in _norm(it.get("snippet"))))
            or (stock_key and (stock_key in _norm(it.get("title")) or stock_key in _norm(it.get("snippet"))))
        ]

        recent = filter_recent(relevant, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        return recent[: config.NEWS_MAX_ITEMS]
