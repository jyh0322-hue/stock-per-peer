from app import pipeline


class FakeDart:
    def resolve_corp(self, name):
        mapping = {
            "코스맥스": {"corp_code": "C1", "corp_name": "코스맥스", "stock_code": "192820", "induty_code": ""},
            "한국콜마": {"corp_code": "C2", "corp_name": "한국콜마", "stock_code": "003350", "induty_code": ""},
        }
        return mapping.get(name, {"corp_code": "X", "corp_name": "브이티", "stock_code": "018290", "induty_code": "204"})

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        return {"year": 2026, "reprt_key": "HALF", "op_3m": 50.0}

    def recent_disclosures(self, corp_code, days=90):
        return [{"date": "20260810", "title": "반기보고서", "type": "정기공시", "rcept_no": "1"}]


class FakeKrx:
    _mc = {"018290": 3000.0, "192820": 5000.0, "003350": 1200.0}
    _per = {"018290": 14.0, "192820": 12.0, "003350": 20.0}

    def sector_of(self, code):
        return "화장품"

    def market_cap(self, code):
        return self._mc.get(code)

    def krx_per(self, code):
        return self._per.get(code)

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
    assert len(steps) == 5 and steps[-1][1] == 5
