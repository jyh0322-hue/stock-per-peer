from app import insights


def _items():
    return [
        {"title": "브이티 2분기 영업익 급증", "snippet": "북미 매출 확대", "url": "http://n1",
         "source": "뉴스", "published": None},
        {"title": "브이티 밸류 부담 지적", "snippet": "PER 고평가 우려", "url": "http://n2",
         "source": "뉴스", "published": None},
    ]


def test_no_data_status():
    res = insights.summarize([], company="브이티")
    assert res["status"] == "no_data"


def test_disabled_when_no_claude():
    # claude 미주입 + 키 없음 -> disabled, 원문은 sources로 노출
    res = insights.summarize(_items(), company="브이티", claude=lambda p: (_ for _ in ()).throw(RuntimeError()))
    assert res["status"] == "disabled"
    assert len(res["sources"]) == 2


def test_summarize_maps_sources():
    fake_json = ('{"investment_points":[{"text":"북미 매출 확대","sources":[1]}],'
                 '"risks":[{"text":"밸류 부담","sources":[2]}],"overall":"중립"}')
    res = insights.summarize(_items(), company="브이티", claude=lambda p: fake_json, as_of="2026-08-14")
    assert res["status"] == "ok"
    assert res["investment_points"][0]["sources"][0]["url"] == "http://n1"
    assert res["risks"][0]["sources"][0]["url"] == "http://n2"
    assert res["as_of"] == "2026-08-14"


def test_out_of_range_source_index_skipped():
    fake_json = ('{"investment_points":[{"text":"이상값","sources":[1,99,-1]}],'
                 '"risks":[],"overall":"중립"}')
    res = insights.summarize(_items(), company="브이티", claude=lambda p: fake_json)
    assert res["status"] == "ok"
    assert len(res["investment_points"][0]["sources"]) == 1
    assert res["investment_points"][0]["sources"][0]["url"] == "http://n1"


def test_unparseable_json_is_disabled():
    res = insights.summarize(_items(), company="브이티", claude=lambda p: "not json at all")
    assert res["status"] == "disabled"
    assert len(res["sources"]) == 2


def test_analyst_views_mapped_to_full_source_objects():
    fake_json = ('{"investment_points":[],"risks":[],'
                 '"analyst_views":[{"text":"A증권, 목표주가 5만원 제시","sources":[1]}],'
                 '"overall":"중립"}')
    res = insights.summarize(_items(), company="브이티", claude=lambda p: fake_json)
    assert res["status"] == "ok"
    assert len(res["analyst_views"]) == 1
    assert res["analyst_views"][0]["text"] == "A증권, 목표주가 5만원 제시"
    assert res["analyst_views"][0]["sources"][0]["url"] == "http://n1"


def test_analyst_views_absent_key_maps_to_empty_list():
    # analyst_views 키 자체가 없는(구버전 형식) 응답이어도 예외 없이 빈 배열로 처리되어야 함
    fake_json = ('{"investment_points":[{"text":"북미 매출 확대","sources":[1]}],'
                 '"risks":[],"overall":"중립"}')
    res = insights.summarize(_items(), company="브이티", claude=lambda p: fake_json)
    assert res["status"] == "ok"
    assert res["analyst_views"] == []


def test_disabled_status_still_includes_empty_analyst_views():
    res = insights.summarize(_items(), company="브이티", claude=lambda p: (_ for _ in ()).throw(RuntimeError()))
    assert res["status"] == "disabled"
    assert res["analyst_views"] == []
    assert len(res["sources"]) == 2


def test_sources_carry_kind_for_frontend_grouping():
    # 프런트가 news/opinion/blog로 묶어 보여줄 수 있도록, disabled 상태에서도
    # 항목의 kind가 sources에 그대로 실려야 한다.
    items = [
        {"title": "브이티 실적", "snippet": "", "url": "http://n1", "source": "뉴스",
         "published": None, "kind": "news"},
        {"title": "A증권 목표주가", "snippet": "", "url": "http://o1", "source": "증권신문",
         "published": None, "kind": "opinion"},
        {"title": "브이티 후기", "snippet": "", "url": "http://b1", "source": "블로그",
         "published": None, "kind": "blog"},
    ]
    res = insights.summarize(items, company="브이티", claude=lambda p: (_ for _ in ()).throw(RuntimeError()))
    assert res["status"] == "disabled"
    kinds = [s["kind"] for s in res["sources"]]
    assert kinds == ["news", "opinion", "blog"]


def test_build_prompt_lists_numbered_items():
    prompt = insights.build_prompt("브이티", _items())
    assert "1. " in prompt
    assert "2. " in prompt
    assert "브이티" in prompt


# ---- summarize_industry ---------------------------------------------------

def _industry_items():
    return [
        {"title": "화학제품 업황 개선 조짐", "snippet": "수출 회복세", "url": "http://i1",
         "source": "산업신문", "published": None, "kind": "industry"},
        {"title": "화학업종 수출 전망 밝아", "snippet": "가격 반등", "url": "http://i2",
         "source": "경제지", "published": None, "kind": "industry"},
    ]


def test_summarize_industry_no_data_status():
    res = insights.summarize_industry([], sector="화학제품")
    assert res["status"] == "no_data"


def test_summarize_industry_disabled_when_no_claude_keeps_sources():
    res = insights.summarize_industry(
        _industry_items(), sector="화학제품",
        claude=lambda p: (_ for _ in ()).throw(RuntimeError()))
    assert res["status"] == "disabled"
    assert len(res["sources"]) == 2
    assert res["summary"] is None


def test_summarize_industry_maps_sources():
    fake_json = ('{"points":[{"text":"업황 개선 조짐","sources":[1]}],'
                 '"summary":"업황이 개선되는 모습입니다."}')
    res = insights.summarize_industry(_industry_items(), sector="화학제품", claude=lambda p: fake_json)
    assert res["status"] == "ok"
    assert res["points"][0]["sources"][0]["url"] == "http://i1"
    assert res["summary"] == "업황이 개선되는 모습입니다."


def test_summarize_industry_unparseable_json_is_disabled():
    res = insights.summarize_industry(_industry_items(), sector="화학제품", claude=lambda p: "not json")
    assert res["status"] == "disabled"
    assert len(res["sources"]) == 2


def test_build_industry_prompt_lists_numbered_items_and_sector():
    prompt = insights.build_industry_prompt("화학제품", _industry_items())
    assert "1. " in prompt
    assert "2. " in prompt
    assert "화학제품" in prompt
