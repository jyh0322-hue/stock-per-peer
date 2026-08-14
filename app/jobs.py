import time
import uuid
from typing import Optional


class JobStore:
    def __init__(self, ttl=1800):
        self.ttl = ttl
        self._jobs = {}

    def _gc(self):
        now = time.time()
        dead = [k for k, v in self._jobs.items() if now - v["_ts"] > self.ttl]
        for k in dead:
            self._jobs.pop(k, None)

    def create(self):
        self._gc()
        jid = uuid.uuid4().hex[:12]
        self._jobs[jid] = {"state": "running", "progress": {"step": "대기", "current": 0,
                           "total": 6, "pct": 0}, "result": None, "error": None, "_ts": time.time()}
        return jid

    def update(self, job_id, step, current, total):
        j = self._jobs.get(job_id)
        if not j:
            return
        pct = int(current / total * 100) if total else 0
        j["progress"] = {"step": step, "current": current, "total": total, "pct": pct}
        j["_ts"] = time.time()

    def finish(self, job_id, result):
        j = self._jobs.get(job_id)
        if j:
            j["state"] = "done"
            j["result"] = result
            j["_ts"] = time.time()

    def fail(self, job_id, error):
        j = self._jobs.get(job_id)
        if j:
            j["state"] = "error"
            j["error"] = str(error)
            j["_ts"] = time.time()

    def get(self, job_id):
        j = self._jobs.get(job_id)
        if not j:
            return None
        return {"state": j["state"], "progress": j["progress"],
                "result": j["result"], "error": j["error"]}
