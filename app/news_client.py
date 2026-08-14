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
NAVER_BLOG_SEARCH_URL = "https://search.naver.com/search.naver?where=blog&query={q}"

# 투자의견/증권가 코멘트/목표주가 등 "의견성" 기사를 추가로 훑기 위한 질의어 템플릿.
# {name}은 stock_name(있으면)·company 순으로 채워진다(fetch_recent와 동일한 우선순위).
OPINION_QUERY_TEMPLATES = [
    "{name} 목표주가",
    "{name} 투자의견",
    "{name} 실적 전망",
    "{name} 증권",
]

# 업종 동향 검색 시 뒤에 붙이는 접미어. "{term} 업황"/"{term} 전망"/"{term} 수출" 형태로 질의한다.
INDUSTRY_QUERY_SUFFIXES = ["업황", "전망", "수출"]

# FDR/KRX가 주는 업종명은 통계청 표준산업분류 표기라 장황하다(예: "기타 화학제품 제조업").
# 검색 질의어로는 부적합하므로 걸러내는 토큰들.
_INDUSTRY_STRIP_TOKENS = ["제조업", "및", "기타"]

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


def parse_naver_blog_html(html_text, now):
    """네이버 블로그 검색 결과 HTML 파싱(방어적). 마크업이 자주 바뀌므로 parse_naver_html과
    동일한 원칙으로 짠다: 실패 시 예외를 삼키고 빈 리스트를 반환한다."""
    if not html_text or lxml_html is None:
        return []
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []

    try:
        anchors = doc.xpath(
            '//a[contains(concat(" ", normalize-space(@class), " "), " title_link ")]'
            ' | //a[contains(concat(" ", normalize-space(@class), " "), " api_txt_lines ")'
            ' and contains(concat(" ", normalize-space(@class), " "), " total_tit ")]'
        )
    except Exception:
        anchors = []

    out = []
    for a in anchors:
        try:
            title = _strip(a.get("title") or a.text_content())
            url = (a.get("href") or "").strip()
            if not title or not url:
                continue

            # 포스트 하나를 감싸는 상위 컨테이너를 찾아 그 안에서 스니펫/블로그명/날짜를 탐색
            container = a
            for _ in range(6):
                parent = container.getparent()
                if parent is None:
                    break
                container = parent
                cls = container.get("class") or ""
                if "total_wrap" in cls or "bx" == cls.strip() or "blog_" in cls:
                    break

            snippet = ""
            try:
                dsc_nodes = container.xpath(
                    './/a[contains(@class,"dsc_link")] | .//div[contains(@class,"total_dsc")]'
                    ' | .//a[contains(@class,"api_txt_lines") and not(contains(@class,"total_tit"))]'
                )
                if dsc_nodes:
                    snippet = _strip(dsc_nodes[0].text_content())
            except Exception:
                snippet = ""

            source = ""
            published = None
            try:
                info_nodes = container.xpath(
                    './/a[contains(@class,"name")] | .//span[contains(@class,"sub_time")]'
                    ' | .//span[contains(@class,"date")] | .//span[contains(@class,"sub_txt")]'
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
                    if not source and not re.search(r"\d", t):
                        source = t
            except Exception:
                pass

            out.append({
                "title": title,
                "snippet": snippet,
                "url": url,
                "source": source or "네이버블로그",
                "published": published,
            })
        except Exception:
            continue

    return out


def _industry_query_terms(sector):
    """업종 문자열을 검색 질의어로 쓸 수 있게 짧은 토큰 1~2개로 축약한다.
    "기타 화학제품 제조업" -> ["화학제품"]. 콤마/세미콜론으로 나뉜 항목이 있으면
    각각 정제해 최대 2개까지 취한다. 정제 후 아무것도 남지 않으면 빈 리스트."""
    if not sector:
        return []
    segments = re.split(r"[,;]", sector)
    out = []
    for seg in segments:
        t = seg.strip()
        for tok in _INDUSTRY_STRIP_TOKENS:
            t = t.replace(tok, "")
        t = re.sub(r"\s+", " ", t).strip()
        if not t or t in out:
            continue
        out.append(t)
        if len(out) >= 2:
            break
    return out


def _relevant_only(items, company, stock_name):
    """제목/스니펫에 회사명(법인명에서 "(주)"/"주식회사" 제거한 키, 또는 KRX 상장명 키)
    중 하나라도 포함된 항목만 남긴다. fetch_recent/fetch_opinions/fetch_blog가 공유."""
    company_key = _norm(_clean_company_name(company))
    stock_key = _norm(stock_name)
    return [
        it for it in items
        if (company_key and (company_key in _norm(it.get("title")) or company_key in _norm(it.get("snippet"))))
        or (stock_key and (stock_key in _norm(it.get("title")) or stock_key in _norm(it.get("snippet"))))
    ]


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
        relevant = _relevant_only(raw, company, stock_name)

        recent = filter_recent(relevant, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        recent = recent[: config.NEWS_MAX_ITEMS]
        for it in recent:
            it["kind"] = "news"
        return recent

    def fetch_opinions(self, company, stock_name, days=30, now=None):
        """투자의견/증권가 코멘트/목표주가/실적전망 등 "의견성" 기사를 추가 질의어로
        수집한다. 구글 뉴스 RSS만 사용(네이버 뉴스 검색은 fetch_recent의 보조 소스로
        이미 커버). 여러 질의 결과를 합쳐 중복 제거 후 최신순으로 상한을 둔다."""
        now = now or datetime.now(KST)
        query_name = stock_name or company
        if not query_name:
            return []

        raw = []
        for template in OPINION_QUERY_TEMPLATES:
            q = template.format(name=query_name)
            try:
                rss_url = GOOGLE_NEWS_RSS_URL.format(q=quote(q))
                rss_text = self._fetch(rss_url)
                raw.extend(parse_google_rss(rss_text))
            except Exception:
                continue

        relevant = _relevant_only(raw, company, stock_name)
        recent = filter_recent(relevant, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        recent = recent[: config.NEWS_MAX_ITEMS]
        for it in recent:
            it["kind"] = "opinion"
        return recent

    def fetch_blog(self, company, stock_name, days=30, now=None):
        """네이버 블로그 검색에서 분석글/후기성 포스트를 수집한다. 구글 뉴스 RSS는
        블로그를 색인하지 않으므로 별도 소스가 필요하다. 파싱 실패는 조용히 빈 리스트로
        처리한다(parse_naver_blog_html이 이미 방어적)."""
        now = now or datetime.now(KST)
        query = stock_name or company
        if not query:
            return []

        try:
            blog_url = NAVER_BLOG_SEARCH_URL.format(q=quote(query))
            blog_text = self._fetch(blog_url)
            raw = parse_naver_blog_html(blog_text, now)
        except Exception:
            raw = []

        relevant = _relevant_only(raw, company, stock_name)
        recent = filter_recent(relevant, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        for it in recent:
            it["kind"] = "blog"
        return recent

    def fetch_all(self, company, stock_name, days=30, now=None):
        """뉴스 + 투자의견 + 블로그를 합쳐 중복 제거·최근 days일 필터·최신순 정렬 후
        config.NEWS_MAX_ITEMS_ALL개로 상한을 둔 리스트를 반환한다. 각 소스는 이미
        자체적으로 실패를 삼키므로(네트워크 예외 포함) 이 메서드는 예외를 던지지 않는다."""
        now = now or datetime.now(KST)
        news = self.fetch_recent(company, stock_name, days=days, now=now)
        opinions = self.fetch_opinions(company, stock_name, days=days, now=now)
        blogs = self.fetch_blog(company, stock_name, days=days, now=now)

        merged = news + opinions + blogs
        merged = dedup(merged)
        merged = filter_recent(merged, days, now)
        merged.sort(key=lambda i: i["published"], reverse=True)
        return merged[: config.NEWS_MAX_ITEMS_ALL]

    def fetch_industry(self, sector, days=30, now=None):
        """업종 동향(업황/전망/수출) 뉴스를 구글 뉴스 RSS로 수집한다. sector가 비어있거나
        정제 후 쓸만한 검색어가 없으면(_industry_query_terms가 빈 리스트) 빈 리스트를
        반환한다. 개별 회사 관련성 필터(_relevant_only)는 적용하지 않는다 — 질의어 자체가
        업종을 특정하므로 회사명 매칭은 의미가 없다."""
        now = now or datetime.now(KST)
        terms = _industry_query_terms(sector)
        if not terms:
            return []

        raw = []
        for term in terms:
            for suffix in INDUSTRY_QUERY_SUFFIXES:
                q = "%s %s" % (term, suffix)
                try:
                    rss_url = GOOGLE_NEWS_RSS_URL.format(q=quote(q))
                    rss_text = self._fetch(rss_url)
                    raw.extend(parse_google_rss(rss_text))
                except Exception:
                    continue

        recent = filter_recent(raw, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        recent = recent[: config.NEWS_MAX_ITEMS]
        for it in recent:
            it["kind"] = "industry"
        return recent
