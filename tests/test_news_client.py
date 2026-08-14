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
