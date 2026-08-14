from app import company


class FakeDart:
    def company_info(self, corp_code):
        return {
            "corp_name": "브이티", "est_dt": "19861107", "ceo_nm": "강승곤,정철",
            "adres": "경기도 파주시", "hm_url": "www.vtgmpcorp.co.kr",
            "induty_code": "204", "corp_cls": "K",
        }


class FakeKrx:
    def profile(self, stock_code):
        return {
            "market": "KOSDAQ", "sector": "우량기업부", "industry": "기타 화학제품 제조업",
            "products": "화장품, 라미네이팅필름", "representative": "강승곤,정철",
            "homepage": "http://www.vtgmpcorp.co.kr", "region": "경기도",
            "listing_date": "1994-09-07",
        }


class BoomDart:
    def company_info(self, corp_code):
        raise RuntimeError("DART API down")


class BoomKrx:
    def profile(self, stock_code):
        raise RuntimeError("KRX listing unavailable")


def test_overview_merges_dart_and_krx_sources():
    out = company.overview(FakeDart(), FakeKrx(), "00150165", "018290")
    assert out["corp_name"] == "브이티"
    assert out["stock_code"] == "018290"
    assert out["market"] == "KOSDAQ"
    assert out["sector"] == "우량기업부"
    assert out["industry"] == "기타 화학제품 제조업"
    assert out["products"] == "화장품, 라미네이팅필름"
    assert out["ceo"] == "강승곤,정철"
    assert out["established"] == "1986-11-07"
    assert out["homepage"] == "www.vtgmpcorp.co.kr"
    assert out["region"] == "경기도"
    assert out["listing_date"] == "1994-09-07"


def test_overview_both_sources_raise_returns_all_none_dict_no_exception():
    out = company.overview(BoomDart(), BoomKrx(), "00150165", "018290")
    assert out["stock_code"] == "018290"  # 입력값은 유지
    for key in ("corp_name", "market", "sector", "industry", "products", "ceo",
                "established", "homepage", "region", "listing_date"):
        assert out[key] is None


class NoHomepageDart:
    def company_info(self, corp_code):
        return {"corp_name": "테스트", "est_dt": None, "ceo_nm": None,
                "adres": "서울", "hm_url": None, "induty_code": "", "corp_cls": "K"}


class FallbackKrx:
    def profile(self, stock_code):
        return {"market": "KOSPI", "sector": "제조업", "industry": None, "products": None,
                "representative": "홍길동", "homepage": "http://fallback.example",
                "region": None, "listing_date": "2000-01-01"}


def test_overview_falls_back_to_krx_when_dart_fields_missing():
    out = company.overview(NoHomepageDart(), FallbackKrx(), "X", "000001")
    # DART ceo_nm/hm_url이 없으면 KRX 쪽(representative/homepage)으로 완만히 저하
    assert out["ceo"] == "홍길동"
    assert out["homepage"] == "http://fallback.example"
    # KRX region이 없으면 DART adres로 저하
    assert out["region"] == "서울"


def test_overview_missing_est_dt_format_returns_none():
    class OddDart:
        def company_info(self, corp_code):
            return {"corp_name": "X", "est_dt": "", "ceo_nm": None, "adres": None,
                    "hm_url": None, "induty_code": "", "corp_cls": ""}

    out = company.overview(OddDart(), FakeKrx(), "X", "018290")
    assert out["established"] is None
