from app import pipeline


# DART는 stock_code(6자리)로 조회하면 유일하게 매칭되지만, 이름으로 조회하면
# corp_name 완전일치 중 아무 하나(.iloc[0])를 반환할 수 있다(동명이인 위험). 그래서
# FakeDart는 실제 OpenDartReader.find_corp_code처럼 "6자리 숫자면 stock_code, 아니면
# corp_name"으로 나누어 조회하도록 만든다 — peer/target 조회가 실제로 stock_code
# 기준으로 이뤄지는지 테스트가 검증할 수 있게 하기 위함이다.
_CORPS = {
    "018290": {"corp_code": "X", "corp_name": "브이티", "stock_code": "018290", "induty_code": "204"},
    "192820": {"corp_code": "C1", "corp_name": "코스맥스", "stock_code": "192820", "induty_code": ""},
    "003350": {"corp_code": "C2", "corp_name": "한국콜마", "stock_code": "003350", "induty_code": ""},
}
_BY_NAME = {v["corp_name"]: v for v in _CORPS.values()}


class FakeDart:
    def resolve_corp(self, name_or_code):
        if isinstance(name_or_code, str) and name_or_code.isdigit() and len(name_or_code) == 6:
            info = _CORPS.get(name_or_code)
        else:
            info = _BY_NAME.get(name_or_code)
        if info is None:
            raise LookupError("'%s' 기업을 DART에서 찾지 못했습니다." % name_or_code)
        return dict(info)

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        return {"year": 2026, "reprt_key": "HALF", "op_3m": 50.0, "fs_div": fs_div}

    def recent_disclosures(self, corp_code, days=90):
        return [{"date": "20260810", "title": "반기보고서", "type": "정기공시", "rcept_no": "1"}]


class FakeKrx:
    _mc = {"018290": 3000.0, "192820": 5000.0, "003350": 1200.0}
    _per = {"018290": 14.0, "192820": 12.0, "003350": 20.0}
    _name = {"018290": "브이티", "192820": "코스맥스", "003350": "한국콜마"}

    def sector_of(self, code):
        return "화장품"

    def market_cap(self, code):
        return self._mc.get(code)

    def krx_per(self, code):
        return self._per.get(code)

    def name_of(self, code):
        return self._name.get(code)

    def peers_in_sector(self, sector, exclude_code, top=5):
        return [{"stock_code": "192820", "name": "코스맥스", "market_cap": 5000.0},
                {"stock_code": "003350", "name": "한국콜마", "market_cap": 1200.0}]


def _make_fakes():
    return FakeDart(), FakeKrx()


def test_run_analysis_progress_and_per():
    steps = []
    dart, krx = _make_fakes()

    res = pipeline.run_analysis("브이티", dart, krx,
                                progress_cb=lambda s, c, t: steps.append((s, c, t)))
    # 타깃 PER = 시총3000 / (50*4=200) = 15.0
    assert res["target"]["per_op"] == 15.0
    assert res["stats"]["count"] >= 2
    assert len(steps) == 7 and steps[-1][1] == 7
    # news/insights_fn 미주입 시에도 분석은 성공하고, insights는 비활성 상태로 채워짐
    assert res["insights"]["status"] == "disabled"
    # 심층분석은 FakeDart/FakeKrx가 finstate/company_info/profile을 제공하지 않아도
    # (각 조각이 try/except로 감싸여) 예외 없이 deepdive 키 자체는 채워져야 한다.
    assert "deepdive" in res and res["deepdive"] is not None
    assert set(res["deepdive"].keys()) == {"overview", "income_statement", "margins", "trend", "basis"}


def test_run_analysis_includes_insights_and_7_steps():
    steps = []

    class FakeNews:
        def fetch_recent(self, company, stock_name, days=30, now=None):
            return [{"title": "호재", "snippet": "매출↑", "url": "http://n1",
                     "source": "뉴스", "published": None}]

    def fake_insights(items, company, as_of=None):
        return {"status": "ok", "investment_points": [{"text": "매출 성장", "sources": []}],
                "risks": [], "overall": "중립", "sources": [], "as_of": as_of, "window_days": 30}

    dart, krx = _make_fakes()
    res = pipeline.run_analysis("브이티", dart, krx, news=FakeNews(), insights_fn=fake_insights,
                                progress_cb=lambda s, c, t: steps.append((s, c, t)))
    assert res["insights"]["status"] == "ok"
    assert steps[-1][2] == 7 and steps[-1][1] == 7


def test_run_analysis_survives_news_failure():
    class BoomNews:
        def fetch_recent(self, company, stock_name, days=30, now=None):
            raise RuntimeError("network down")

    def fake_insights(items, company, as_of=None):
        raise AssertionError("should not be called if news fetch failed")

    dart, krx = _make_fakes()
    res = pipeline.run_analysis("브이티", dart, krx, news=BoomNews(), insights_fn=fake_insights)
    assert res["insights"]["status"] == "disabled"
    assert res["target"]["per_op"] == 15.0


# ---- C1: peer identity mismatch -----------------------------------------

class MismatchDart(FakeDart):
    """코스맥스(192820)로 조회했는데 DART가 완전히 다른 회사(동명이인/오매칭)를
    돌려주는 상황을 시뮬레이션한다. 그 회사의 영업이익(999억, 명백히 다른 값)이
    코스맥스의 시총과 섞이면 안 된다."""

    def resolve_corp(self, name_or_code):
        if name_or_code == "192820":
            return {"corp_code": "WRONG", "corp_name": "전혀다른회사",
                     "stock_code": "999999", "induty_code": ""}
        return super().resolve_corp(name_or_code)

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        if corp_code == "WRONG":
            return {"year": 2026, "reprt_key": "HALF", "op_3m": 999.0, "fs_div": fs_div}
        return super().latest_quarter_op(corp_code, fs_div)


def test_peer_identity_mismatch_drops_peer_instead_of_merging():
    dart = MismatchDart()
    krx = FakeKrx()
    res = pipeline.run_analysis("브이티", dart, krx)

    peer = next(p for p in res["peers"] if p["stock_code"] == "192820")
    # 오매칭된 999억 영업이익이 코스맥스 시총(5000억)과 섞여 가짜 PER을 만들면 안 된다.
    assert peer["op_3m"] is None
    assert peer["per_op"] is None
    assert peer["per_status"] == "nodata"
    # 시총/이름은 KRX 데이터 그대로 유지되어야 한다(peer 자체가 사라지지는 않음).
    assert peer["market_cap"] == 5000.0
    assert peer["name"] == "코스맥스"


class TargetMismatchDart(FakeDart):
    """이름으로 검색한 타깃이 왕복 검증(같은 stock_code로 재조회)에서 다른 corp_code를
    반환하면(동명이인 오매칭) 분석을 계속 진행하지 않고 LookupError로 실패해야 한다."""

    def resolve_corp(self, name_or_code):
        if name_or_code == "브이티":
            return {"corp_code": "X", "corp_name": "브이티", "stock_code": "018290", "induty_code": "204"}
        if name_or_code == "018290":
            return {"corp_code": "DIFFERENT", "corp_name": "브이티(동명이인)",
                     "stock_code": "018290", "induty_code": ""}
        return super().resolve_corp(name_or_code)


def test_target_identity_verification_failure_raises_lookup_error():
    dart = TargetMismatchDart()
    krx = FakeKrx()
    try:
        pipeline.run_analysis("브이티", dart, krx)
        assert False, "LookupError가 발생해야 함"
    except LookupError:
        pass


# ---- C2: per_status (loss vs nodata) -------------------------------------

def test_metrics_for_per_status_ok_loss_nodata():
    ok = pipeline._metrics_for("A", "A사", 1000.0, 50.0, None)
    assert ok["per_status"] == "ok"

    loss = pipeline._metrics_for("B", "B사", 1000.0, -10.0, None)
    assert loss["per_status"] == "loss"
    assert loss["per_op"] is None

    nodata = pipeline._metrics_for("C", "C사", 1000.0, None, None)
    assert nodata["per_status"] == "nodata"
    assert nodata["per_op"] is None


# ---- I3: insufficient_peers flag ------------------------------------------

class SparseOpDart(FakeDart):
    """타깃만 실적이 있고 peer 둘 다 실적을 못 찾는 상황(<2 peer PER)."""

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        if corp_code == "X":
            return {"year": 2026, "reprt_key": "HALF", "op_3m": 50.0, "fs_div": fs_div}
        return {"year": None, "reprt_key": None, "op_3m": None, "fs_div": fs_div}


def test_insufficient_peers_flag_true_when_fewer_than_two_peer_pers():
    res = pipeline.run_analysis("브이티", SparseOpDart(), FakeKrx())
    assert res["stats"]["insufficient_peers"] is True


def test_insufficient_peers_flag_false_when_enough_peer_pers():
    res = pipeline.run_analysis("브이티", FakeDart(), FakeKrx())
    assert res["stats"]["insufficient_peers"] is False
