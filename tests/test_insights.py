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


def test_build_prompt_lists_numbered_items():
    prompt = insights.build_prompt("브이티", _items())
    assert "1. " in prompt
    assert "2. " in prompt
    assert "브이티" in prompt
