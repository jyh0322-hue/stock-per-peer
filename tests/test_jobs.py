from app.jobs import JobStore


def test_lifecycle():
    js = JobStore()
    jid = js.create()
    assert js.get(jid)["state"] == "running"
    js.update(jid, "PER 계산", 4, 5)
    assert js.get(jid)["progress"]["pct"] == 80
    js.finish(jid, {"ok": True})
    assert js.get(jid)["state"] == "done"
    assert js.get(jid)["result"] == {"ok": True}


def test_fail():
    js = JobStore()
    jid = js.create()
    js.fail(jid, "boom")
    assert js.get(jid)["state"] == "error"
    assert js.get(jid)["error"] == "boom"


def test_unknown_job():
    assert JobStore().get("nope") is None
