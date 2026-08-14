import os
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import pipeline
from app.jobs import JobStore

logger = logging.getLogger(__name__)

# requests 예외 등은 요청 URL(OpenDART crtfc_key=<40자 키> 포함)을 그대로 문자열에 담기 때문에,
# 원인 불명 예외는 절대 그대로 클라이언트에 보내지 않는다 — 서버 로그로만 남기고
# 사용자에게는 일반 메시지를 준다. LookupError만 예외(사용자 질의로 만들어진, 키가 섞일 수 없는
# "종목을 찾지 못했습니다" 류의 안전한 메시지)로 취급해 그대로 노출한다.
GENERIC_ERROR_MSG = "분석 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

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
    except LookupError as e:
        # 사용자 질의 문자열로만 구성되는 메시지라 민감정보가 섞일 수 없다 — 그대로 노출.
        STORE.fail(job_id, e)
    except Exception as e:  # noqa
        # requests.exceptions 등은 요청 URL 전체(OpenDART crtfc_key 포함)를 담을 수 있으므로
        # 절대 str(e)를 클라이언트로 보내지 않는다. 상세는 서버 로그에만 남긴다.
        logger.exception("분석 작업 실패 (job_id=%s, name=%s)", job_id, name)
        STORE.fail(job_id, GENERIC_ERROR_MSG)


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
