from datetime import datetime, timezone, timedelta

from app import news_client as nc

KST = timezone(timedelta(hours=9))


# ---- parse_rss_date -------------------------------------------------

def test_parse_rss_date_rfc1123_gmt():
    d = nc.parse_rss_date("Mon, 11 Aug 2026 09:30:00 GMT")
    assert d.year == 2026 and d.month == 8 and d.day == 11


def test_parse_rss_date_invalid_returns_none():
    assert nc.parse_rss_date("이상한 날짜") is None


# ---- parse_relative_date --------------------------------------------

def test_parse_relative_date_days_ago():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=KST)
    d = nc.parse_relative_date("3일 전", now)
    assert d.year == 2026 and d.month == 8 and d.day == 11


def test_parse_relative_date_hours_ago():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=KST)
    d = nc.parse_relative_date("2시간 전", now)
    assert d == now - timedelta(hours=2)


def test_parse_relative_date_minutes_ago():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=KST)
    d = nc.parse_relative_date("30분 전", now)
    assert d == now - timedelta(minutes=30)


def test_parse_relative_date_absolute_dotted():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=KST)
    d = nc.parse_relative_date("2026.08.11.", now)
    assert d.year == 2026 and d.month == 8 and d.day == 11


def test_parse_relative_date_unparseable_returns_none():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=KST)
    assert nc.parse_relative_date("알 수 없는 문자열", now) is None


# ---- filter_recent -----------------------------------------------------

def test_filter_recent_excludes_old_and_undated():
    now = datetime(2026, 8, 14, tzinfo=KST)
    items = [
        {"title": "a", "url": "u1", "published": datetime(2026, 8, 10, tzinfo=KST)},
        {"title": "b", "url": "u2", "published": datetime(2026, 6, 1, tzinfo=KST)},
        {"title": "c", "url": "u3", "published": None},
    ]
    out = nc.filter_recent(items, days=30, now=now)
    assert [i["title"] for i in out] == ["a"]


# ---- dedup ---------------------------------------------------------

def test_dedup_by_title_url():
    items = [
        {"title": "속보 A", "url": "u1"},
        {"title": "속보  A", "url": "u1"},
        {"title": "B", "url": "u2"},
    ]
    assert len(nc.dedup(items)) == 2


# ---- parse_google_rss -----------------------------------------------

GOOGLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Google News</title>
<item>
<title>브이티 실적 호조 - 한국경제</title>
<link>http://n1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>&lt;a href="http://n1"&gt;영업익 증가&lt;/a&gt;</description>
<source url="http://hankyung.com">한국경제</source>
</item>
<item>
<title>브이티, 신제품 출시 - 매일경제</title>
<link>http://n2</link>
<pubDate>Tue, 12 Aug 2026 10:00:00 GMT</pubDate>
<description>&lt;a href="http://n2"&gt;신제품 소개&lt;/a&gt;</description>
</item>
</channel>
</rss>
"""


def test_parse_google_rss_extracts_items():
    items = nc.parse_google_rss(GOOGLE_RSS_XML)
    assert len(items) == 2

    first = items[0]
    assert first["title"] == "브이티 실적 호조 - 한국경제"
    assert first["url"] == "http://n1"
    assert first["source"] == "한국경제"
    assert first["snippet"] == "영업익 증가"
    assert first["published"].year == 2026
    assert first["published"].month == 8
    assert first["published"].day == 11

    # second item has no <source> element -> derived from " - " suffix in title
    second = items[1]
    assert second["source"] == "매일경제"
    assert second["url"] == "http://n2"


def test_parse_google_rss_malformed_returns_empty():
    assert nc.parse_google_rss("not xml at all <<<") == []


# ---- parse_naver_html (defensive parsing) ----------------------------

def test_parse_naver_html_garbage_returns_empty_list():
    now = datetime(2026, 8, 14, tzinfo=KST)
    assert nc.parse_naver_html("<html><body>no news here</body></html>", now) == []


def test_parse_naver_html_none_input_does_not_raise():
    now = datetime(2026, 8, 14, tzinfo=KST)
    assert nc.parse_naver_html(None, now) == []


# ---- NewsClient.fetch_recent -------------------------------------------

def _fake_fetch_google_only(rss_xml):
    def fetch(url):
        if "news.google.com" in url:
            return rss_xml
        return ""  # naver not reached because google already has >= 5, but just in case
    return fetch


RSS_WITH_RELEVANT_AND_OLD_AND_IRRELEVANT = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 실적 호조 - 한국경제</title>
<link>http://n1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>영업익 증가</description>
<source>한국경제</source>
</item>
<item>
<title>옛날 기사 - 오래된신문</title>
<link>http://old</link>
<pubDate>Mon, 01 Jun 2026 09:30:00 GMT</pubDate>
<description>브이티 관련</description>
<source>오래된신문</source>
</item>
<item>
<title>전혀 다른 회사 뉴스 - 딴신문</title>
<link>http://irrelevant</link>
<pubDate>Tue, 12 Aug 2026 09:30:00 GMT</pubDate>
<description>관련 없음</description>
<source>딴신문</source>
</item>
</channel></rss>
"""


def test_fetch_recent_filters_old_and_irrelevant_items():
    now = datetime(2026, 8, 14, tzinfo=KST)
    fetch = _fake_fetch_google_only(RSS_WITH_RELEVANT_AND_OLD_AND_IRRELEVANT)
    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_recent("브이티", "브이티", days=30, now=now)

    titles = [i["title"] for i in items]
    assert "브이티 실적 호조 - 한국경제" in titles
    assert "옛날 기사 - 오래된신문" not in titles       # too old
    assert "전혀 다른 회사 뉴스 - 딴신문" not in titles  # not relevant (no company name)

    for it in items:
        assert set(it.keys()) >= {"title", "snippet", "url", "source", "published"}


def test_fetch_recent_swallows_fetch_exception():
    def fetch(url):
        raise RuntimeError("network down")

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_recent("브이티", "브이티", days=30, now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []


def test_fetch_recent_uses_naver_fallback_when_google_has_few_items():
    now = datetime(2026, 8, 14, tzinfo=KST)
    google_rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 단독 기사 - 신문사</title>
<link>http://g1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 관련 내용</description>
<source>신문사</source>
</item>
</channel></rss>
"""
    calls = {"naver": False}

    def fetch(url):
        if "news.google.com" in url:
            return google_rss
        if "search.naver.com" in url:
            calls["naver"] = True
            return "<html><body>no parseable news</body></html>"
        raise AssertionError("unexpected url: %s" % url)

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_recent("브이티", "브이티", days=30, now=now)
    assert calls["naver"] is True
    assert any(i["title"] == "브이티 단독 기사 - 신문사" for i in items)


# ---- I12: DART 법인명과 KRX 상장명이 다를 때 KRX 이름으로도 매칭되어야 함 -----

def test_fetch_recent_matches_on_krx_name_when_dart_legal_name_differs():
    now = datetime(2026, 8, 14, tzinfo=KST)
    # 헤드라인은 KRX 상장명("브이티")을 쓰지만, DART 법인 정식명칭은 전혀 다르다
    # ("주식회사" 접두/상이한 표기) — 종전 로직(company만으로 매칭)이면 걸러졌을 기사.
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 실적 호조 - 한국경제</title>
<link>http://n1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>영업익 증가</description>
<source>한국경제</source>
</item>
</channel></rss>
"""
    fetch = _fake_fetch_google_only(rss)
    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_recent("주식회사브이티코스메틱스", "브이티", days=30, now=now)

    assert len(items) == 1
    assert items[0]["title"] == "브이티 실적 호조 - 한국경제"


# ---- NewsClient.fetch_opinions -----------------------------------------

def test_fetch_opinions_dedups_across_queries():
    now = datetime(2026, 8, 14, tzinfo=KST)

    # 같은 기사가 "목표주가"/"투자의견" 두 질의 모두에서 검색된다고 가정(중복 링크).
    item_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 목표주가 상향 - 증권신문</title>
<link>http://o1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 목표주가 관련</description>
<source>증권신문</source>
</item>
</channel></rss>
"""
    other_item_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 실적 전망 밝아 - 경제지</title>
<link>http://o2</link>
<pubDate>Tue, 12 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 실적 전망</description>
<source>경제지</source>
</item>
</channel></rss>
"""
    calls = []

    def fetch(url):
        calls.append(url)
        if "%EB%AA%A9%ED%91%9C%EC%A3%BC%EA%B0%80" in url or "목표주가" in url:
            return item_xml
        if "투자의견" in url or "%ED%88%AC%EC%9E%90%EC%9D%98%EA%B2%AC" in url:
            return item_xml  # 동일 기사가 다른 질의에서도 잡힘 -> 중복
        if "실적" in url or "%EC%8B%A4%EC%A0%81" in url:
            return other_item_xml
        return ""

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_opinions("브이티", "브이티", days=30, now=now)

    urls = [i["url"] for i in items]
    assert urls.count("http://o1") == 1  # 두 질의에서 중복 등장해도 한 번만
    assert "http://o2" in urls
    assert all(i["kind"] == "opinion" for i in items)
    assert len(calls) == len(nc.OPINION_QUERY_TEMPLATES)  # 질의 템플릿 개수만큼 호출


def test_fetch_opinions_swallows_fetch_exception():
    def fetch(url):
        raise RuntimeError("network down")

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_opinions("브이티", "브이티", days=30, now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []


# ---- NewsClient.fetch_blog -----------------------------------------------

NAVER_BLOG_HTML = """
<html><body>
<div class="total_wrap">
  <a class="title_link" href="http://b1">브이티 화장품 써봤어요 후기</a>
  <div class="total_dsc">브이티 신제품 사용 후기입니다</div>
  <span class="sub_time">2일 전</span>
</div>
</body></html>
"""


def test_fetch_blog_items_get_kind_blog():
    now = datetime(2026, 8, 14, tzinfo=KST)

    def fetch(url):
        assert "search.naver.com" in url and "where=blog" in url
        return NAVER_BLOG_HTML

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_blog("브이티", "브이티", days=30, now=now)
    assert len(items) == 1
    assert items[0]["kind"] == "blog"
    assert items[0]["url"] == "http://b1"


def test_fetch_blog_parse_failure_returns_empty_list():
    def fetch(url):
        raise RuntimeError("network down")

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_blog("브이티", "브이티", days=30, now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []


def test_parse_naver_blog_html_garbage_returns_empty_list():
    now = datetime(2026, 8, 14, tzinfo=KST)
    assert nc.parse_naver_blog_html("<html><body>nothing here</body></html>", now) == []


def test_parse_naver_blog_html_none_input_does_not_raise():
    now = datetime(2026, 8, 14, tzinfo=KST)
    assert nc.parse_naver_blog_html(None, now) == []


# ---- NewsClient.fetch_all -------------------------------------------------

def test_fetch_all_merges_dedups_caps_and_drops_out_of_window_items():
    now = datetime(2026, 8, 14, tzinfo=KST)

    news_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 실적 호조 - 한국경제</title>
<link>http://n1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 영업익 증가</description>
<source>한국경제</source>
</item>
<item>
<title>브이티 옛날 기사 - 오래된신문</title>
<link>http://old</link>
<pubDate>Mon, 01 Jun 2026 09:30:00 GMT</pubDate>
<description>브이티 관련(30일보다 오래됨)</description>
<source>오래된신문</source>
</item>
</channel></rss>
"""
    opinion_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>브이티 실적 호조 - 한국경제</title>
<link>http://n1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 영업익 증가(투자의견 질의에서도 동일 기사 재검색됨)</description>
<source>한국경제</source>
</item>
<item>
<title>브이티 목표주가 상향 - 증권신문</title>
<link>http://o1</link>
<pubDate>Tue, 12 Aug 2026 09:30:00 GMT</pubDate>
<description>브이티 목표주가</description>
<source>증권신문</source>
</item>
</channel></rss>
"""

    def fetch(url):
        if "news.google.com" in url:
            return opinion_xml if any(k in url for k in
                                       ["%EB%AA%A9%ED%91%9C", "%ED%88%AC%EC%9E%90",
                                        "%EC%8B%A4%EC%A0%81", "%EC%A6%9D%EA%B6%8C"]) else news_xml
        if "where=blog" in url:
            return NAVER_BLOG_HTML
        return ""

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_all("브이티", "브이티", days=30, now=now)

    urls = [i["url"] for i in items]
    assert "http://old" not in urls  # 30일 밖 -> 제외
    assert urls.count("http://n1") == 1  # news+opinion 양쪽에서 잡혀도 dedup으로 한 번만
    assert "http://o1" in urls
    assert "http://b1" in urls
    kinds = {i["url"]: i["kind"] for i in items}
    assert kinds["http://n1"] == "news"  # 먼저 합쳐진 news가 우선
    assert kinds["http://o1"] == "opinion"
    assert kinds["http://b1"] == "blog"
    assert len(items) <= 40  # config.NEWS_MAX_ITEMS_ALL
    # 최신순 정렬 확인
    assert items == sorted(items, key=lambda i: i["published"], reverse=True)


def test_fetch_all_raising_fetcher_yields_empty_list_not_exception():
    def fetch(url):
        raise RuntimeError("network down")

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_all("브이티", "브이티", days=30, now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []


# ---- _industry_query_terms ---------------------------------------------

def test_industry_query_terms_shortens_verbose_sector_string():
    terms = nc._industry_query_terms("기타 화학제품 제조업")
    assert terms  # usable terms
    assert all(len(t) <= 10 for t in terms)
    assert "제조업" not in terms[0]
    assert "기타" not in terms[0]


def test_industry_query_terms_unusable_input_returns_empty_list():
    assert nc._industry_query_terms("") == []
    assert nc._industry_query_terms(None) == []
    assert nc._industry_query_terms("제조업") == []
    assert nc._industry_query_terms("기타") == []


def test_industry_query_terms_splits_on_comma_and_caps_at_two():
    terms = nc._industry_query_terms("화장품, 기타 화학제품 제조업, 세번째 항목")
    assert len(terms) <= 2
    assert "화장품" in terms


# ---- NewsClient.fetch_industry -------------------------------------------

INDUSTRY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>화학제품 업황 개선 조짐 - 산업신문</title>
<link>http://i1</link>
<pubDate>Mon, 11 Aug 2026 09:30:00 GMT</pubDate>
<description>업황 관련 내용</description>
<source>산업신문</source>
</item>
</channel></rss>
"""


def test_fetch_industry_tags_kind_industry_and_respects_window():
    now = datetime(2026, 8, 14, tzinfo=KST)

    def fetch(url):
        return INDUSTRY_RSS

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_industry("기타 화학제품 제조업", days=30, now=now)
    assert len(items) >= 1
    assert all(i["kind"] == "industry" for i in items)


def test_fetch_industry_excludes_items_outside_window():
    now = datetime(2026, 8, 14, tzinfo=KST)
    old_rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<title>오래된 업황 기사 - 산업신문</title>
<link>http://old-industry</link>
<pubDate>Mon, 01 Jun 2026 09:30:00 GMT</pubDate>
<description>오래된 업황</description>
<source>산업신문</source>
</item>
</channel></rss>
"""

    def fetch(url):
        return old_rss

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_industry("기타 화학제품 제조업", days=30, now=now)
    assert items == []


def test_fetch_industry_returns_empty_when_no_usable_terms():
    calls = []

    def fetch(url):
        calls.append(url)
        return ""

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_industry("제조업", days=30, now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []
    assert calls == []  # 검색어가 없으면 아예 요청을 보내지 않음


def test_fetch_industry_swallows_fetch_exception():
    def fetch(url):
        raise RuntimeError("network down")

    client = nc.NewsClient(fetch=fetch)
    items = client.fetch_industry("기타 화학제품 제조업", days=30,
                                  now=datetime(2026, 8, 14, tzinfo=KST))
    assert items == []


def test_fetch_recent_queries_with_krx_name_when_available():
    # 검색 질의어 자체도 (DART 법인명이 아니라) KRX 상장명을 우선 사용해야 한다 —
    # 뉴스 헤드라인이 실제로 쓰는 표기라 검색 적중률이 높다.
    from urllib.parse import quote

    seen = {}

    def fetch(url):
        seen["url"] = url
        return ""

    client = nc.NewsClient(fetch=fetch)
    client.fetch_recent("주식회사브이티코스메틱스", "브이티", days=30,
                        now=datetime(2026, 8, 14, tzinfo=KST))
    assert quote("브이티") in seen["url"]
    assert quote("주식회사브이티코스메틱스") not in seen["url"]
