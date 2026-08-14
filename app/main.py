import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import pipeline
from app.jobs import JobStore

app = FastAPI(title="DART PER·PEER 분석")
STORE = JobStore()
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")


def _default_factory():
    from app.dart_client import DartClient
    from app.krx_client import KrxClient
    from app.news_client import NewsClient
    from app import insights
    return DartClient(), KrxClient(), NewsClient(), insights.summarize


CLIENT_FACTORY = _default_factory


class AnalyzeReq(BaseModel):
    name: str


def _run_job(job_id, name):
    try:
        dart, krx, news, insights_fn = CLIENT_FACTORY()
        result = pipeline.run_analysis(
            name, dart, krx, news=news, insights_fn=insights_fn,
            progress_cb=lambda s, c, t: STORE.update(job_id, s, c, t))
        STORE.finish(job_id, result)
    except Exception as e:  # noqa
        STORE.fail(job_id, e)


@app.post("/api/analyze")
async def analyze(req: AnalyzeReq):
    # 종목 존재만 먼저 동기 검증 -> 즉시 400 반환 가능
    try:
        dart = CLIENT_FACTORY()[0]
        dart.resolve_corp(req.name)
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = STORE.create()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_job, job_id, req.name)
    return JSONResponse({"job_id": job_id}, status_code=202)


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    st = STORE.get(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"state": st["state"], "progress": st["progress"], "error": st["error"]}


@app.get("/api/result/{job_id}")
async def result(job_id: str):
    st = STORE.get(job_id)
    if st is None or st["state"] != "done":
        raise HTTPException(status_code=404, detail="not ready")
    return st["result"]


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


# 정적 자산(app.js, styles.css)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
