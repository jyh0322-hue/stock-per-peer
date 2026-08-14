from datetime import date

import pandas as pd
from app.dart_client import DartClient


class FakeReader:
    def find_corp_code(self, name):
        return "00126380" if name == "삼성전자" else None

    def company(self, code):
        return {"corp_name": "삼성전자", "stock_code": "005930", "induty_code": "264"}

    def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
        # 사업보고서(11011, ANNUAL)는 아직 제출되지 않은 상태를 흉내낸다(현재 진행 중인
        # 회계연도) — REPRT_ORDER가 ANNUAL을 먼저 시도하더라도 빈 결과를 받고 Q3로
        # 자연스럽게 폴백해야 한다.
        if reprt_code == "11011":
            return None
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


def test_empty_finstate_uses_short_ttl(tmp_path, monkeypatch):
    from app import cache, config
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache._mem.clear()

    class EmptyReader(FakeReader):
        def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
            return None

    c = DartClient(reader=EmptyReader())
    assert c.finstate("X", 2026, "HALF") is None
    key = "finstate:X:2026:HALF:CFS"
    expires_at = cache._mem[key][0]
    import time as _t
    # 30일이 아니라 1시간 근처로 만료되어야 함
    assert expires_at - _t.time() <= config.FINSTATE_EMPTY_TTL + 5


# ---- I4: REPRT_ORDER — ANNUAL(4Q)이 같은 해의 Q3보다 우선해야 함 -----------

class OrderFakeReader:
    """같은 해(year)에 ANNUAL(11011)과 Q3(11014) 데이터가 모두 존재하는 상황.
    구 순서("Q3","HALF","Q1","ANNUAL")였다면 Q3(9억)를 먼저 찾아 반환했을 것이다.
    올바른 순서(config.REPRT_ORDER = ANNUAL 우선)라면 ANNUAL 기준 4분기 실적
    (연간누적 40억 - 3Q누적 30억 = 10억)을 반환해야 한다."""

    def find_corp_code(self, name):
        return "ORDER1"

    def company(self, code):
        return {"corp_name": "테스트기업", "stock_code": "000001", "induty_code": ""}

    def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
        if reprt_code == "11011":  # 사업보고서(ANNUAL) — 연간누적 40억
            return pd.DataFrame([
                {"account_id": "dart_OperatingIncomeLoss",
                 "thstrm_amount": "4,000,000,000", "thstrm_add_amount": "4,000,000,000"},
            ])
        if reprt_code == "11014":  # 3분기보고서(Q3) — 단독 9억 / 누적 30억
            return pd.DataFrame([
                {"account_id": "dart_OperatingIncomeLoss",
                 "thstrm_amount": "900,000,000", "thstrm_add_amount": "3,000,000,000"},
            ])
        return None


def test_latest_quarter_op_prefers_annual_over_q3_same_year(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache._mem.clear()

    c = DartClient(reader=OrderFakeReader())
    r = c.latest_quarter_op("ORDER1")
    assert r["reprt_key"] == "ANNUAL"
    assert r["year"] == date.today().year
    # ANNUAL 기준 Q4 = 연간누적 40억 - 3Q누적 30억 = 10억 (Q3 단독값 9억이 아님)
    assert r["op_3m"] == 10.0


# ---- I6: CFS 조회 실패 시 OFS(별도재무제표) 폴백 --------------------------

class OfsOnlyReader:
    """연결재무제표(CFS)를 제출하지 않는 단일 법인(코스닥에 흔함) — CFS 조회는 항상
    빈 결과, 별도재무제표(OFS)에만 실적이 있다."""

    def find_corp_code(self, name):
        return "OFS1"

    def company(self, code):
        return {"corp_name": "단일법인", "stock_code": "000002", "induty_code": ""}

    def finstate_all(self, corp, year, reprt_code, fs_div="CFS"):
        if fs_div == "CFS":
            return None
        if reprt_code == "11014":  # OFS에는 Q3만 존재 (단독 80억)
            return pd.DataFrame([
                {"account_id": "dart_OperatingIncomeLoss",
                 "thstrm_amount": "8,000,000,000", "thstrm_add_amount": "8,000,000,000"},
            ])
        return None


def test_latest_quarter_op_falls_back_to_ofs_when_cfs_empty(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache._mem.clear()

    c = DartClient(reader=OfsOnlyReader())
    r = c.latest_quarter_op("OFS1")
    assert r["op_3m"] == 80.0
    assert r["fs_div"] == "OFS"


# ---- I11: 비상장 DART 법인은 LookupError -----------------------------------

class UnlistedReader:
    def find_corp_code(self, name):
        return "U1"

    def company(self, code):
        return {"corp_name": "비상장회사", "stock_code": "", "induty_code": ""}


def test_resolve_corp_unlisted_raises_lookup_error():
    c = DartClient(reader=UnlistedReader())
    try:
        c.resolve_corp("비상장회사")
        assert False, "예외가 발생해야 함"
    except LookupError as e:
        assert "상장" in str(e)
