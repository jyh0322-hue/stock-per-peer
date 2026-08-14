import pandas as pd
from app.dart_client import DartClient


class FakeReader:
    def find_corp_code(self, name):
        return "00126380" if name == "삼성전자" else None

    def company(self, code):
        return {"corp_name": "삼성전자", "stock_code": "005930", "induty_code": "264"}

    def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
        return pd.DataFrame([
            {"account_id": "dart_OperatingIncomeLoss",
             "thstrm_amount": "5,000,000,000", "thstrm_add_amount": "5,000,000,000"},
        ])

    def list(self, corp, start=None, end=None, kind=None):
        return pd.DataFrame([
            {"rcept_dt": "20260810", "report_nm": "반기보고서", "rcept_no": "1"},
            {"rcept_dt": "20260805", "report_nm": "주요사항보고서(유상증자결정)", "rcept_no": "2"},
        ])


def test_resolve_corp():
    c = DartClient(reader=FakeReader())
    info = c.resolve_corp("삼성전자")
    assert info["corp_code"] == "00126380"
    assert info["stock_code"] == "005930"
    assert info["induty_code"] == "264"


def test_resolve_corp_not_found_raises():
    c = DartClient(reader=FakeReader())
    try:
        c.resolve_corp("없는회사")
        assert False, "예외가 발생해야 함"
    except LookupError:
        pass


def test_latest_quarter_op():
    c = DartClient(reader=FakeReader())
    r = c.latest_quarter_op("00126380")
    assert r["op_3m"] == 50.0  # 50억


def test_recent_disclosures_tags_type():
    c = DartClient(reader=FakeReader())
    ds = c.recent_disclosures("00126380")
    assert ds[0]["date"] == "20260810"
    assert any("유상증자" in d["title"] for d in ds)


def test_finstate_is_cached_across_calls(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache._mem.clear()

    class CountingReader(FakeReader):
        def __init__(self):
            self.calls = 0

        def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
            self.calls += 1
            return FakeReader.finstate_all(self, corp, year, reprt_code, fs_div)

    r = CountingReader()
    c = DartClient(reader=r)
    a = c.finstate("X", 2026, "HALF")
    b = c.finstate("X", 2026, "HALF")
    assert r.calls == 1              # 두 번째는 캐시 히트
    assert len(a) == len(b)          # 동일 데이터 재구성
