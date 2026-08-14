import time
from fastapi.testclient import TestClient
from app import main


class FakeDart:
    def resolve_corp(self, name):
        if name == "없음":
            raise LookupError("not found")
        return {"corp_code": "X", "corp_name": "브이티", "stock_code": "018290", "induty_code": ""}

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        return {"year": 2026, "reprt_key": "HALF", "op_3m": 50.0}

    def recent_disclosures(self, corp_code, days=90):
        return []


class FakeKrx:
    def sector_of(self, code):
        return "화장품"

    def market_cap(self, code):
        return 3000.0

    def krx_per(self, code):
        return 14.0

    def peers_in_sector(self, sector, exclude_code, top=5):
        return []


class FakeNews:
    def fetch_recent(self, company, stock_name, days=30, now=None):
        return []


def fake_insights_fn(items, company, as_of=None):
    return {"status": "no_data", "investment_points": [], "risks": [],
            "overall": "", "sources": [], "as_of": as_of, "window_days": 30}


def setup_module(_):
    main.CLIENT_FACTORY = lambda: (FakeDart(), FakeKrx(), FakeNews(), fake_insights_fn)


def _wait_done(client, jid, timeout=5):
    for _ in range(int(timeout * 20)):
        st = client.get("/api/status/%s" % jid).json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_analyze_flow():
    client = TestClient(main.app)
    r = client.post("/api/analyze", json={"name": "브이티"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    st = _wait_done(client, jid)
    assert st["state"] == "done"
    res = client.get("/api/result/%s" % jid).json()
    assert res["target"]["per_op"] == 15.0


def test_analyze_not_found():
    client = TestClient(main.app)
    r = client.post("/api/analyze", json={"name": "없음"})
    assert r.status_code == 400
