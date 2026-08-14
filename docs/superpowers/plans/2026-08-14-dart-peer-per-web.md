# DART 종목 PER·PEER 비교 웹앱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 종목명을 검색하면 DART 재무·공시와 KRX 시세를 결합해 연환산 영업이익 기준 PER을 계산하고 동일 업종 시총 상위 5개 PEER와 비교해 보여주는 비동기 웹앱을 만든다.

**Architecture:** FastAPI 백엔드가 검색 요청을 비동기 잡으로 받아 스레드풀에서 실행하고, 프론트는 진행률을 폴링한다. 네트워크 클라이언트(dart/krx)와 순수 계산(metrics/quarterly)을 분리해 후자를 네트워크 없이 TDD한다. 기존 `stock_report.py`의 차트·손익 로직을 모듈로 이관해 재사용한다.

**Tech Stack:** Python 3.9, FastAPI, uvicorn, OpenDartReader, pykrx, FinanceDataReader, pandas, matplotlib, pytest. 배포는 Hugging Face Spaces(Docker).

**Spec:** `docs/superpowers/specs/2026-08-14-dart-peer-per-web-design.md`

## Global Constraints

- Python 3.9 호환 문법만 사용(현 환경 3.9.6). `match`문·`X | Y` 타입힌트 금지, `Optional[X]`/`Union` 사용.
- 금액 단위: 원 → 억원 환산(`/1e8`), 표시 소수 자리 스펙 준수.
- 영업이익 계정 매칭은 계정명이 아닌 `account_id`(`dart_OperatingIncomeLoss`, `ifrs-full_ProfitLossFromOperatingActivities`)로 한다.
- PEER 개수 = 5 (`config.PEER_COUNT`), 상수로 관리.
- PER 라벨은 항상 "PER(영업이익 기준, 연환산)"으로 표기. 영업이익 ≤ 0 이면 `None`/`N/A(적자)`.
- 보고서 코드: 1Q=`11013`, 반기=`11012`, 3Q=`11014`, 사업=`11011`.
- 네트워크 호출은 모두 `retry` 래퍼 경유. 순수 계산 모듈은 네트워크·pandas I/O 의존 금지.
- 비밀키 `OPENDART_API_KEY`는 환경변수/HF Secret에서만 읽고 코드·로그에 남기지 않는다.
- 커밋은 각 Task 완료 시 1회 이상. TDD 순서(실패 테스트 → 구현 → 통과) 준수.
- 뉴스·요약 서브시스템(Task 11~13, 스펙 §16): 최근 **30일** 발행분만 대상. 요약 문장마다 출처 인덱스 명기, 수집 자료 밖 내용 금지(환각 금지). 관련 키 미설정/실패는 예외 없이 `status`(`ok|no_data|disabled`)로 흡수.
- 뉴스 관련 env: `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`. requirements에 `anthropic` 추가. httpx는 FastAPI TestClient 의존이므로 requirements에 포함.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `app/config.py` | 상수·환경변수 |
| `app/cache.py` | 디스크+메모리 2단 캐시 |
| `app/metrics.py` | PER·연환산·중앙값·순위·마진 (순수) |
| `app/quarterly.py` | 최근 분기 영업이익 discrete 추출 (df 입력, 순수 로직) |
| `app/dart_client.py` | OpenDartReader 래퍼(종목해석·재무·공시) |
| `app/krx_client.py` | pykrx + FDR 래퍼(시총·주가·업종) |
| `app/report.py` | 결과 JSON + HTML + 차트 |
| `app/pipeline.py` | 분석 오케스트레이션(진행률 콜백) |
| `app/jobs.py` | 인메모리 잡 저장소 + TTL |
| `app/main.py` | FastAPI 라우트·백그라운드 실행 |
| `web/index.html` `web/app.js` `web/styles.css` | 검색 프론트 |
| `tests/*` | 단위·통합 테스트 |
| `Dockerfile` `requirements.txt` `README.md` | 배포·문서 |

---

## Task 0: 프로젝트 스캐폴드 · 의존성 · config · cache

**Files:**
- Create: `requirements.txt`, `app/__init__.py`, `app/config.py`, `app/cache.py`, `tests/__init__.py`, `tests/test_cache.py`, `.gitignore`

**Interfaces:**
- Produces:
  - `config.PEER_COUNT:int`, `config.REPRT` dict, `config.EOK:float`, `config.api_key()->str`, `config.CACHE_DIR:str`
  - `cache.memoize(key:str, ttl:int, producer:Callable[[],Any])->Any`
  - `cache.day_key(prefix:str)->str` (예: `"marcap:20260814"`)

- [ ] **Step 1: requirements.txt 작성**

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
OpenDartReader
pykrx
finance-datareader
pandas
matplotlib
requests
lxml
python-dotenv
anthropic
httpx
pytest
```

- [ ] **Step 2: 의존성 설치**

Run: `python3 -m pip install -r requirements.txt`
Expected: 설치 성공(특히 `pykrx`, `finance-datareader`, `fastapi`).

- [ ] **Step 3: .gitignore 작성**

```
__pycache__/
*.pyc
.cache/
docs_cache/
.DS_Store
*.env
```

- [ ] **Step 4: config.py 작성**

```python
import os

# 프로젝트 루트의 .env 를 읽어 환경변수로 로드(있으면). 이미 설정된 환경변수는 덮어쓰지 않음
# → 로컬은 .env, 배포(HF Spaces)는 Space Secrets(실제 환경변수)로 같은 코드가 동작.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
except Exception:
    pass  # python-dotenv 미설치/‑.env 없음이어도 os.environ 로 동작

PEER_COUNT = 5
EOK = 1e8  # 원 -> 억원
NEWS_WINDOW_DAYS = 30
REPRT = {"Q1": "11013", "HALF": "11012", "Q3": "11014", "ANNUAL": "11011"}
REPRT_ORDER = ["ANNUAL", "Q3", "HALF", "Q1"]  # 최신성 판단용(누적 범위 큰 순)
OP_ACCOUNT_IDS = ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"]
REVENUE_ACCOUNT_IDS = ["ifrs-full_Revenue"]
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")


def env(name):
    return os.environ.get(name)


def api_key():
    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        raise RuntimeError("OPENDART_API_KEY 가 필요합니다. .env 파일 또는 환경변수로 설정하세요.")
    return key
```

> `.env` 파일은 프로젝트 루트(`주식/.env`)에 두고 `.gitignore`로 제외한다. 키가 없을 때는 `.env.example`을 복사(`cp .env.example .env`)해 값을 채운다.

- [ ] **Step 5: cache.py 실패 테스트 작성 (`tests/test_cache.py`)**

```python
import time
from app import cache


def test_memoize_calls_producer_once_within_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"v": 42}

    a = cache.memoize("k1", ttl=60, producer=producer)
    b = cache.memoize("k1", ttl=60, producer=producer)
    assert a == b == {"v": 42}
    assert calls["n"] == 1  # 두 번째는 캐시 히트


def test_memoize_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    cache.memoize("k2", ttl=0, producer=producer)
    time.sleep(0.01)
    cache.memoize("k2", ttl=0, producer=producer)
    assert calls["n"] == 2  # ttl=0 이면 매번 재생성
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_cache.py -v`
Expected: FAIL (`app.cache` 없음/함수 미정의).

- [ ] **Step 7: cache.py 구현**

```python
import os
import json
import time

from app import config

CACHE_DIR = config.CACHE_DIR
_mem = {}  # key -> (expires_at, value)


def _path(key):
    safe = key.replace(":", "_").replace("/", "_")
    return os.path.join(CACHE_DIR, safe + ".json")


def day_key(prefix):
    return prefix + ":" + time.strftime("%Y%m%d")


def memoize(key, ttl, producer):
    now = time.time()
    hit = _mem.get(key)
    if hit and hit[0] > now:
        return hit[1]
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _path(key)
    if ttl > 0 and os.path.exists(p) and (now - os.path.getmtime(p)) < ttl:
        try:
            with open(p, "r", encoding="utf-8") as f:
                value = json.load(f)
            _mem[key] = (now + ttl, value)
            return value
        except Exception:
            pass
    value = producer()
    _mem[key] = (now + ttl, value)
    if ttl > 0:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except (TypeError, ValueError):
            pass  # JSON 직렬화 불가한 값은 메모리 캐시만
    return value
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_cache.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: 커밋**

```bash
git init 2>/dev/null; git add requirements.txt .gitignore app/__init__.py app/config.py app/cache.py tests/__init__.py tests/test_cache.py
git commit -m "feat: 프로젝트 스캐폴드 + config + 2단 캐시"
```

---

## Task 1: metrics.py — PER·연환산·중앙값·순위 (순수 함수)

**Files:**
- Create: `app/metrics.py`, `tests/test_metrics.py`

**Interfaces:**
- Consumes: 없음(순수).
- Produces:
  - `annualize(op_3m: float) -> float`  (`op_3m * 4`)
  - `per_op(market_cap: float, op_annualized: float) -> Optional[float]`  (op ≤ 0 이면 None)
  - `peer_stats(pers: List[Optional[float]]) -> dict`  (`{"median","min","max","count"}`, None 제외)
  - `rank_within(target_per: Optional[float], peer_pers: List[Optional[float]]) -> dict`  (`{"rank","total","percentile"}`, PER 낮을수록 1위)
  - `margin(numer: float, denom: float) -> Optional[float]`  (`numer/denom*100`, denom 0/None → None)

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_metrics.py`)**

```python
import math
from app import metrics


def test_annualize():
    assert metrics.annualize(25.0) == 100.0


def test_per_op_positive():
    # 시총 1000억, 연환산 영업이익 100억 -> PER 10
    assert metrics.per_op(1000.0, 100.0) == 10.0


def test_per_op_negative_op_returns_none():
    assert metrics.per_op(1000.0, -50.0) is None
    assert metrics.per_op(1000.0, 0.0) is None


def test_peer_stats_ignores_none():
    s = metrics.peer_stats([10.0, None, 20.0, 30.0])
    assert s["count"] == 3
    assert s["median"] == 20.0
    assert s["min"] == 10.0 and s["max"] == 30.0


def test_peer_stats_empty():
    s = metrics.peer_stats([None, None])
    assert s["count"] == 0
    assert s["median"] is None


def test_rank_within_lower_is_better():
    r = metrics.rank_within(10.0, [10.0, 20.0, 30.0, 40.0])
    assert r["rank"] == 1 and r["total"] == 4
    assert math.isclose(r["percentile"], 25.0)


def test_rank_within_none_target():
    r = metrics.rank_within(None, [10.0, 20.0])
    assert r["rank"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: FAIL (`app.metrics` 없음).

- [ ] **Step 3: metrics.py 구현**

```python
from statistics import median
from typing import List, Optional


def annualize(op_3m):
    return op_3m * 4


def per_op(market_cap, op_annualized):
    if op_annualized is None or op_annualized <= 0:
        return None
    if market_cap is None:
        return None
    return market_cap / op_annualized


def _clean(pers):
    return [p for p in pers if p is not None]


def peer_stats(pers):
    vals = _clean(pers)
    if not vals:
        return {"median": None, "min": None, "max": None, "count": 0}
    return {"median": float(median(vals)), "min": min(vals), "max": max(vals), "count": len(vals)}


def rank_within(target_per, peer_pers):
    vals = _clean(peer_pers)
    if target_per is None or not vals:
        return {"rank": None, "total": len(vals), "percentile": None}
    universe = sorted(vals)  # target_per 는 vals 에 이미 포함된다고 가정
    rank = sum(1 for v in universe if v < target_per) + 1
    total = len(universe)
    percentile = (rank / total) * 100 if total else None
    return {"rank": rank, "total": total, "percentile": percentile}


def margin(numer, denom):
    if not denom or numer is None:
        return None
    return numer / denom * 100
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_metrics.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/metrics.py tests/test_metrics.py
git commit -m "feat: metrics — PER 연환산/중앙값/순위 순수함수"
```

---

## Task 2: quarterly.py — 최근 분기 영업이익 discrete 추출

**Files:**
- Create: `app/quarterly.py`, `tests/test_quarterly.py`

**Interfaces:**
- Consumes: `config.OP_ACCOUNT_IDS`, `config.EOK`.
- Produces:
  - `op_3m_from_df(df, reprt_key: str, prev_cum_df=None) -> Optional[float]`  — DataFrame에서 억원 단위 3개월 영업이익 반환. 사업보고서(ANNUAL)일 때만 `prev_cum_df`(직전 3Q 누적) 필요.
  - `to_eok(x) -> float`
  - `pick_op_amount(df, field: str) -> Optional[float]`  (account_id 매칭, 억원)

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_quarterly.py`)**

`pandas.DataFrame` 픽스처로 DART `finstate_all` 응답 형태를 흉내낸다. 금액은 원 단위 문자열.

```python
import pandas as pd
from app import quarterly


def _df(op_thstrm, op_add=None):
    # DART finstate_all 유사 스키마
    return pd.DataFrame([
        {"account_id": "dart_OperatingIncomeLoss",
         "thstrm_amount": op_thstrm, "thstrm_add_amount": op_add},
        {"account_id": "ifrs-full_Revenue",
         "thstrm_amount": "9,999", "thstrm_add_amount": "9,999"},
    ])


def test_half_report_uses_3month_field():
    # 반기: thstrm_amount = Q2 3개월치 = 20억
    df = _df("2,000,000,000", "5,000,000,000")
    assert quarterly.op_3m_from_df(df, "HALF") == 20.0


def test_q1_report_add_equals_3month():
    # 1Q: 3개월=누적, thstrm_amount 사용
    df = _df("3,000,000,000", "3,000,000,000")
    assert quarterly.op_3m_from_df(df, "Q1") == 30.0


def test_annual_report_q4_is_annual_minus_3q_cum():
    # 사업보고서 연간누적 100억, 직전 3Q 누적 70억 -> Q4 = 30억
    annual = _df("10,000,000,000", "10,000,000,000")
    prev_3q = _df("1,000,000,000", "7,000,000,000")  # 3Q 누적은 add 필드
    assert quarterly.op_3m_from_df(annual, "ANNUAL", prev_cum_df=prev_3q) == 30.0


def test_missing_op_returns_none():
    df = pd.DataFrame([{"account_id": "ifrs-full_Revenue",
                        "thstrm_amount": "1", "thstrm_add_amount": "1"}])
    assert quarterly.op_3m_from_df(df, "HALF") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_quarterly.py -v`
Expected: FAIL (`app.quarterly` 없음).

- [ ] **Step 3: quarterly.py 구현**

```python
from typing import Optional
from app import config


def to_eok(x):
    try:
        s = str(x).replace(",", "").replace("(", "-").replace(")", "").strip()
        return float(s) / config.EOK
    except Exception:
        return float("nan")


def pick_op_amount(df, field):
    m = df[df["account_id"].isin(config.OP_ACCOUNT_IDS)]
    if len(m) == 0:
        return None
    v = to_eok(m.iloc[0].get(field))
    return v if v == v else None  # NaN -> None


def op_3m_from_df(df, reprt_key, prev_cum_df=None):
    if reprt_key in ("Q1", "HALF", "Q3"):
        # thstrm_amount = 해당 분기 3개월 (1Q는 3개월=누적)
        return pick_op_amount(df, "thstrm_amount")
    if reprt_key == "ANNUAL":
        annual_cum = pick_op_amount(df, "thstrm_add_amount")
        if annual_cum is None:
            annual_cum = pick_op_amount(df, "thstrm_amount")
        prev_cum = pick_op_amount(prev_cum_df, "thstrm_add_amount") if prev_cum_df is not None else None
        if annual_cum is None or prev_cum is None:
            return None
        return annual_cum - prev_cum
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_quarterly.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/quarterly.py tests/test_quarterly.py
git commit -m "feat: quarterly — 최근 분기 영업이익 discrete 추출"
```

---

## Task 3: dart_client.py — 종목 해석·재무·공시 래퍼

**Files:**
- Create: `app/dart_client.py`, `tests/test_dart_client.py`

**Interfaces:**
- Consumes: `config`, `quarterly`.
- Produces:
  - `class DartClient` (생성자에서 `OpenDartReader(api_key)` 주입 가능하게 `reader=None` 인자)
  - `resolve_corp(name_or_code) -> dict`  `{"corp_code","corp_name","stock_code","induty_code"}`
  - `finstate(corp_code, year, reprt_key, fs_div="CFS") -> DataFrame`  (retry·캐시)
  - `latest_quarter_op(corp_code, fs_div="CFS") -> dict`  `{"year","reprt_key","op_3m"}`  (없으면 op_3m=None)
  - `recent_disclosures(corp_code, days=90) -> List[dict]`  `{"date","title","rcept_no","type"}`
  - 모듈 함수 `retry(fn, *a, tries=5, **k)`

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_dart_client.py`)**

`OpenDartReader`를 가짜(Fake)로 주입해 네트워크 없이 검증한다.

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_dart_client.py -v`
Expected: FAIL (`app.dart_client` 없음).

- [ ] **Step 3: dart_client.py 구현**

```python
import time
from datetime import date, timedelta
from typing import Optional

from app import config, quarterly

_LATEST_YEAR = date.today().year


def retry(fn, *a, tries=5, **k):
    last = None
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as e:
            last = e
            time.sleep(1.0 * (i + 1))
    raise last


class DartClient:
    def __init__(self, reader=None):
        if reader is None:
            import OpenDartReader
            reader = OpenDartReader(config.api_key())
        self.r = reader

    def resolve_corp(self, name_or_code):
        code = retry(self.r.find_corp_code, name_or_code)
        if not code:
            raise LookupError("'%s' 기업을 DART에서 찾지 못했습니다." % name_or_code)
        info = {}
        try:
            info = retry(self.r.company, code) or {}
        except Exception:
            pass
        return {
            "corp_code": code,
            "corp_name": info.get("corp_name", name_or_code),
            "stock_code": info.get("stock_code", ""),
            "induty_code": info.get("induty_code", ""),
        }

    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        return retry(self.r.finstate_all, corp_code, year,
                     config.REPRT[reprt_key], fs_div=fs_div)

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        # 최신 보고서 탐색: 올해부터 과거로, 분기 최신성 순서
        for year in range(_LATEST_YEAR, _LATEST_YEAR - 2, -1):
            for reprt_key in ("Q3", "HALF", "Q1", "ANNUAL"):
                try:
                    df = self.finstate(corp_code, year, reprt_key, fs_div)
                except Exception:
                    df = None
                if df is None or len(df) == 0:
                    continue
                prev = None
                if reprt_key == "ANNUAL":
                    try:
                        prev = self.finstate(corp_code, year, "Q3", fs_div)
                    except Exception:
                        prev = None
                op = quarterly.op_3m_from_df(df, reprt_key, prev_cum_df=prev)
                if op is not None:
                    return {"year": year, "reprt_key": reprt_key, "op_3m": op}
        return {"year": None, "reprt_key": None, "op_3m": None}

    def recent_disclosures(self, corp_code, days=90):
        end = date.today()
        start = end - timedelta(days=days)
        try:
            df = retry(self.r.list, corp_code,
                       start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        except Exception:
            return []
        if df is None or len(df) == 0:
            return []
        out = []
        for _, row in df.iterrows():
            title = str(row.get("report_nm", ""))
            out.append({
                "date": str(row.get("rcept_dt", "")),
                "title": title,
                "rcept_no": str(row.get("rcept_no", "")),
                "type": _tag(title),
            })
        return out


def _tag(title):
    if any(k in title for k in ["사업보고서", "반기보고서", "분기보고서"]):
        return "정기공시"
    if "주요사항" in title:
        return "주요사항"
    if any(k in title for k in ["증자", "감자", "사채", "발행"]):
        return "발행공시"
    return "기타"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_dart_client.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/dart_client.py tests/test_dart_client.py
git commit -m "feat: dart_client — 종목해석/재무/최근분기/공시 래퍼"
```

---

## Task 4: krx_client.py — 시총·업종(PEER 발굴)

**Files:**
- Create: `app/krx_client.py`, `tests/test_krx_client.py`

**Interfaces:**
- Consumes: `config`, `cache`.
- Produces:
  - `class KrxClient`
  - `market_cap(stock_code) -> Optional[float]`  (억원)
  - `krx_per(stock_code) -> Optional[float]`  (KRX 공식 PER, 교차검증)
  - `sector_of(stock_code) -> Optional[str]`
  - `peers_in_sector(sector, exclude_code, top=config.PEER_COUNT) -> List[dict]`  `{"stock_code","name","market_cap"}` 시총 내림차순
  - 내부 스냅샷 로더는 `cache.memoize`로 당일 캐시.

- [ ] **Step 1: FDR Sector 컬럼 검증 스파이크 (설치 후 1회)**

Run:
```bash
python3 -c "import FinanceDataReader as fdr; df=fdr.StockListing('KRX'); print(list(df.columns)); print(df.head(2).to_dict('records'))"
```
결정 규칙:
- 컬럼에 `Sector`(또는 `Industry`)가 있으면 그대로 사용.
- 없으면(신버전에서 빠진 경우) `fdr.StockListing('KRX-DESC')` 또는 `krx` 패키지의 업종분류로 대체하고, 아래 `_load_listing`의 컬럼명을 실제 값에 맞춰 조정한다.
- 확인된 컬럼명을 `krx_client.py` 상단 주석에 기록한다. (스펙 §14 미해결 항목 해소)

- [ ] **Step 2: 실패 테스트 작성 (`tests/test_krx_client.py`)**

스냅샷 로더를 가짜로 주입해 네트워크 없이 검증한다.

```python
import pandas as pd
from app.krx_client import KrxClient


def make_client():
    # marcap: index=ticker, 시가총액(원)
    marcap = pd.DataFrame(
        {"시가총액": [1e12, 5e11, 2e11, 8e11, 3e11, 1e11]},
        index=["005930", "000660", "005380", "051910", "035420", "068270"],
    )
    listing = pd.DataFrame({
        "Code": ["005930", "000660", "005380", "051910", "035420", "068270"],
        "Name": ["삼성전자", "SK하이닉스", "현대차", "LG화학", "NAVER", "셀트리온"],
        "Sector": ["반도체", "반도체", "자동차", "화학", "반도체", "바이오"],
    })
    fund = pd.DataFrame({"PER": [12.0, 9.0, 5.0, 15.0, 30.0, 40.0]},
                        index=listing["Code"])
    c = KrxClient()
    c._marcap = marcap
    c._listing = listing
    c._fund = fund
    return c


def test_market_cap_in_eok():
    c = make_client()
    assert c.market_cap("005930") == 10000.0  # 1e12원 = 10000억


def test_sector_of():
    assert make_client().sector_of("005930") == "반도체"


def test_peers_in_sector_top_by_marcap_excludes_self():
    c = make_client()
    peers = c.peers_in_sector("반도체", exclude_code="005930", top=5)
    codes = [p["stock_code"] for p in peers]
    assert "005930" not in codes
    # 반도체 나머지: 000660(5e11), 035420(3e11) -> 시총 내림차순
    assert codes == ["000660", "035420"]


def test_krx_per():
    assert make_client().krx_per("000660") == 9.0
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_krx_client.py -v`
Expected: FAIL (`app.krx_client` 없음).

- [ ] **Step 4: krx_client.py 구현**

```python
# FDR StockListing('KRX') 컬럼: Task4 Step1 스파이크로 확인 후 필요시 조정.
# 기대 컬럼: Code, Name, Sector
from typing import List, Optional

from app import config


def _nearest_business_day():
    from pykrx import stock
    return stock.get_nearest_business_day_in_a_week()


class KrxClient:
    def __init__(self):
        self._marcap = None
        self._listing = None
        self._fund = None

    def _ensure(self):
        if self._marcap is None:
            from pykrx import stock
            d = _nearest_business_day()
            self._marcap = stock.get_market_cap_by_ticker(d)
            self._fund = stock.get_market_fundamental_by_ticker(d)
        if self._listing is None:
            import FinanceDataReader as fdr
            self._listing = fdr.StockListing("KRX")

    def market_cap(self, stock_code):
        self._ensure()
        if stock_code not in self._marcap.index:
            return None
        return float(self._marcap.loc[stock_code, "시가총액"]) / config.EOK

    def krx_per(self, stock_code):
        self._ensure()
        if self._fund is None or stock_code not in self._fund.index:
            return None
        v = float(self._fund.loc[stock_code, "PER"])
        return v if v > 0 else None

    def sector_of(self, stock_code):
        self._ensure()
        row = self._listing[self._listing["Code"] == stock_code]
        if len(row) == 0:
            return None
        return str(row.iloc[0]["Sector"])

    def peers_in_sector(self, sector, exclude_code, top=config.PEER_COUNT):
        self._ensure()
        same = self._listing[self._listing["Sector"] == sector]
        rows = []
        for _, r in same.iterrows():
            code = str(r["Code"])
            if code == exclude_code:
                continue
            mc = self.market_cap(code)
            if mc is None:
                continue
            rows.append({"stock_code": code, "name": str(r["Name"]), "market_cap": mc})
        rows.sort(key=lambda x: x["market_cap"], reverse=True)
        return rows[:top]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_krx_client.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: 커밋**

```bash
git add app/krx_client.py tests/test_krx_client.py
git commit -m "feat: krx_client — 시총/업종/PEER 발굴(pykrx+FDR)"
```

---

## Task 5: report.py — 결과 JSON + HTML + 차트

**Files:**
- Create: `app/report.py`, `tests/test_report.py`
- Reference: `stock_report.py` (차트 함수 `chart_perf`, base64 헬퍼 이관 대상)

**Interfaces:**
- Consumes: `metrics`.
- Produces:
  - `build_result(target: dict, peers: List[dict], stats: dict, disclosures: List[dict], deepdive: Optional[dict]) -> dict`
  - `render_html(result: dict) -> str`  (자기완결형 HTML 문자열)
  - `per_bar_chart_b64(peers: List[dict], target_code: str, median: Optional[float]) -> str`  (base64 PNG)

`build_result` 반환 JSON 스키마(프론트 계약):
```json
{
  "target": {"name","stock_code","market_cap","op_3m","op_annualized","per_op","krx_per"},
  "peers": [{"name","stock_code","market_cap","op_3m","op_annualized","per_op","krx_per","is_target"}],
  "stats": {"median","min","max","count","rank","percentile"},
  "disclosures": [{"date","title","type","rcept_no"}],
  "deepdive": {"...기존 stock_report 요약..."} 또는 null,
  "chart_per_b64": "iVBOR..."
}
```

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_report.py`)**

렌더링 순수성만 검증(차트는 base64 문자열 존재 여부).

```python
from app import report


def _sample():
    target = {"name": "브이티", "stock_code": "018290", "market_cap": 3000.0,
              "op_3m": 50.0, "op_annualized": 200.0, "per_op": 15.0, "krx_per": 14.0}
    peers = [
        {"name": "브이티", "stock_code": "018290", "market_cap": 3000.0,
         "op_3m": 50.0, "op_annualized": 200.0, "per_op": 15.0, "krx_per": 14.0, "is_target": True},
        {"name": "코스맥스", "stock_code": "192820", "market_cap": 5000.0,
         "op_3m": 100.0, "op_annualized": 400.0, "per_op": 12.5, "krx_per": 12.0, "is_target": False},
    ]
    stats = {"median": 13.75, "min": 12.5, "max": 15.0, "count": 2, "rank": 2, "percentile": 100.0}
    return target, peers, stats


def test_build_result_shape():
    t, p, s = _sample()
    res = report.build_result(t, p, s, disclosures=[], deepdive=None)
    assert res["target"]["per_op"] == 15.0
    assert len(res["peers"]) == 2
    assert res["stats"]["median"] == 13.75
    assert isinstance(res["chart_per_b64"], str)


def test_render_html_contains_key_labels():
    t, p, s = _sample()
    res = report.build_result(t, p, s, disclosures=[], deepdive=None)
    html = report.render_html(res)
    assert "브이티" in html
    assert "PER(영업이익 기준" in html
    assert "코스맥스" in html
    assert html.strip().startswith("<!doctype html>") or html.strip().startswith("<!DOCTYPE html>")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: FAIL (`app.report` 없음).

- [ ] **Step 3: report.py 구현**

차트는 matplotlib(Agg), 폰트 설정은 `stock_report.py`의 `set_korean_font`/`fig_to_b64` 로직 이관.

```python
import base64
from io import BytesIO
from typing import List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


def _set_korean_font():
    for cand in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic"]:
        try:
            font_manager.findfont(cand, fallback_to_default=False)
            plt.rcParams["font.family"] = cand
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def _fig_b64(fig):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def per_bar_chart_b64(peers, target_code, median):
    _set_korean_font()
    labels, vals, colors = [], [], []
    for p in peers:
        if p.get("per_op") is None:
            continue
        labels.append(p["name"])
        vals.append(p["per_op"])
        colors.append("#E45756" if p["stock_code"] == target_code else "#4C78A8")
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.bar(range(len(vals)), vals, 0.6, color=colors)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("PER(영업이익 기준, 연환산, 배)")
    if median is not None:
        ax.axhline(median, ls="--", color="#888", lw=1.2)
        ax.text(len(vals) - 0.5, median, "  업종 중앙값 %.1f" % median, va="bottom", fontsize=8.5)
    for i, v in enumerate(vals):
        ax.text(i, v, "%.1f" % v, ha="center", va="bottom", fontsize=9)
    ax.set_title("업종 PEER PER 비교", fontsize=12)
    fig.tight_layout()
    return _fig_b64(fig)


def build_result(target, peers, stats, disclosures, deepdive):
    return {
        "target": target,
        "peers": peers,
        "stats": stats,
        "disclosures": disclosures or [],
        "deepdive": deepdive,
        "chart_per_b64": per_bar_chart_b64(peers, target["stock_code"], stats.get("median")),
    }


def _fmt(v, dp=1):
    return ("%,.{}f".format(dp) % v).replace("%", "") if False else (
        format(v, ",.%df" % dp) if isinstance(v, (int, float)) and v == v else "-")


def _per_cell(v):
    return format(v, ",.1f") if isinstance(v, (int, float)) else "N/A(적자)"


def render_html(result):
    t = result["target"]
    rows = ""
    for p in result["peers"]:
        hl = ' style="font-weight:700;background:#fff3f0"' if p.get("is_target") else ""
        rows += (
            "<tr%s><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>\n"
            % (hl, p["name"], _fmt(p["market_cap"], 0), _fmt(p["op_3m"], 0),
               _fmt(p["op_annualized"], 0), _per_cell(p.get("per_op")), _per_cell(p.get("krx_per")))
        )
    disc = "".join("<li>[%s] <b>%s</b> <span class='tag'>%s</span></li>"
                   % (d["date"], d["title"], d["type"]) for d in result["disclosures"][:20])
    s = result["stats"]
    return """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} PER·PEER 분석</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;max-width:960px;margin:0 auto;padding:24px;line-height:1.6}}
table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{padding:8px 10px;border-bottom:1px solid #e5e7eb;white-space:nowrap}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
th{{background:#f8fafc;color:#6b7280;font-size:.82rem}}
img.chart{{width:100%;border:1px solid #eee;border-radius:12px;margin:10px 0}}
.tag{{font-size:.72rem;color:#fff;background:#5b8cff;border-radius:5px;padding:1px 6px}}
.kpi{{display:inline-block;border:1px solid #e5e7eb;border-radius:12px;padding:12px 16px;margin:6px}}
.disc{{margin-top:32px;font-size:.8rem;color:#6b7280;border-top:1px solid #eee;padding-top:12px}}
</style></head><body>
<h1>{name} <small>({code})</small> PER·PEER 분석</h1>
<div>
  <span class="kpi">시총 <b>{mcap}</b>억</span>
  <span class="kpi">연환산 영업이익 <b>{opa}</b>억</span>
  <span class="kpi">PER(영업이익 기준, 연환산) <b>{per}</b></span>
  <span class="kpi">업종 중앙값 <b>{med}</b> · 순위 {rank}/{total}</span>
</div>
<h2>업종 PEER 비교 (시총 상위 {peern})</h2>
<img class="chart" src="data:image/png;base64,{chart}">
<table><thead><tr><th>종목</th><th class="num">시총(억)</th><th class="num">최근분기 영업익(억)</th>
<th class="num">연환산(억)</th><th class="num">PER(영업이익)</th><th class="num">KRX PER</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>최근 공시</h2><ul>{disc}</ul>
<div class="disc">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.
PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4).</div>
</body></html>""".format(
        name=t["name"], code=t.get("stock_code", ""),
        mcap=_fmt(t["market_cap"], 0), opa=_fmt(t.get("op_annualized"), 0),
        per=_per_cell(t.get("per_op")), med=_fmt(s.get("median")),
        rank=s.get("rank") or "-", total=s.get("count") or "-",
        peern=len(result["peers"]), chart=result["chart_per_b64"], rows=rows, disc=disc)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/report.py tests/test_report.py
git commit -m "feat: report — 결과 JSON/HTML/PER 차트"
```

---

## Task 6: pipeline.py — 오케스트레이션(진행률 콜백)

**Files:**
- Create: `app/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `DartClient`, `KrxClient`, `metrics`, `report`.
- Produces:
  - `run_analysis(name, dart, krx, progress_cb=None) -> dict`  (dart/krx 주입으로 테스트 가능)
  - `progress_cb(step: str, current: int, total: int)` 규약: 총 5단계.

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_pipeline.py`)**

Fake dart/krx 주입, 진행률 5회 호출 검증.

```python
from app import pipeline


class FakeDart:
    def resolve_corp(self, name):
        return {"corp_code": "X", "corp_name": "브이티", "stock_code": "018290", "induty_code": "204"}

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


def _corp_of(code):
    return {"192820": "C1", "003350": "C2"}.get(code, "X")


def test_run_analysis_progress_and_per():
    steps = []
    dart = FakeDart()
    krx = FakeKrx()
    # peer의 corp_code 해석은 dart.resolve_corp을 이름으로 다시 부르므로 간단화: monkeypatch
    dart.resolve_corp = lambda name: {"corp_code": _corp_of(name) if name in ("코스맥스", "한국콜마") else "X",
                                      "corp_name": name, "stock_code":
                                      {"코스맥스": "192820", "한국콜마": "003350"}.get(name, "018290"),
                                      "induty_code": ""}

    res = pipeline.run_analysis("브이티", dart, krx,
                                progress_cb=lambda s, c, t: steps.append((s, c, t)))
    # 타깃 PER = 시총3000 / (50*4=200) = 15.0
    assert res["target"]["per_op"] == 15.0
    assert res["stats"]["count"] >= 2
    assert len(steps) == 5 and steps[-1][1] == 5
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: FAIL (`app.pipeline` 없음).

- [ ] **Step 3: pipeline.py 구현**

```python
from app import metrics, report


def _emit(cb, step, cur, total):
    if cb:
        cb(step, cur, total)


def _metrics_for(stock_code, name, market_cap, op_3m, krx_per, is_target=False):
    opa = metrics.annualize(op_3m) if op_3m is not None else None
    per = metrics.per_op(market_cap, opa) if opa is not None else None
    return {"name": name, "stock_code": stock_code, "market_cap": market_cap,
            "op_3m": op_3m, "op_annualized": opa, "per_op": per,
            "krx_per": krx_per, "is_target": is_target}


def run_analysis(name, dart, krx, progress_cb=None):
    TOTAL = 5
    # 1) 종목 해석
    _emit(progress_cb, "종목 해석", 1, TOTAL)
    info = dart.resolve_corp(name)
    target_code = info["stock_code"]

    # 2) 시총·업종·PEER 확정
    _emit(progress_cb, "업종·시총 조회", 2, TOTAL)
    sector = krx.sector_of(target_code)
    target_mc = krx.market_cap(target_code)
    peers_raw = krx.peers_in_sector(sector, exclude_code=target_code) if sector else []

    # 3) PEER 최근 분기 영업이익
    _emit(progress_cb, "PEER 실적 수집", 3, TOTAL)
    peer_rows = []
    for pr in peers_raw:
        try:
            pcorp = dart.resolve_corp(pr["name"])
            q = dart.latest_quarter_op(pcorp["corp_code"])
            op = q["op_3m"]
        except Exception:
            op = None
        peer_rows.append(_metrics_for(pr["stock_code"], pr["name"], pr["market_cap"],
                                      op, krx.krx_per(pr["stock_code"])))

    # 4) 타깃 PER·순위
    _emit(progress_cb, "PER 계산", 4, TOTAL)
    tq = dart.latest_quarter_op(info["corp_code"])
    target_row = _metrics_for(target_code, info["corp_name"], target_mc,
                              tq["op_3m"], krx.krx_per(target_code), is_target=True)
    all_rows = [target_row] + peer_rows
    if not any(r["is_target"] for r in all_rows[:len(peer_rows)]):
        pass  # target 항상 포함
    pers = [r["per_op"] for r in all_rows]
    stats = metrics.peer_stats(pers)
    rank = metrics.rank_within(target_row["per_op"], pers)
    stats.update(rank)

    # 5) 타깃 공시(+심층은 후속 확장 지점)
    _emit(progress_cb, "공시·결과 조립", 5, TOTAL)
    disc = dart.recent_disclosures(info["corp_code"])

    all_rows.sort(key=lambda r: (r["per_op"] is None, r["per_op"] if r["per_op"] is not None else 0))
    return report.build_result(target_row, all_rows, stats, disc, deepdive=None)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline — 5단계 분석 오케스트레이션"
```

---

## Task 7: jobs.py — 인메모리 잡 저장소

**Files:**
- Create: `app/jobs.py`, `tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `class JobStore(ttl=1800)`
  - `create() -> str`
  - `update(job_id, step, current, total)`
  - `finish(job_id, result: dict)`
  - `fail(job_id, error: str)`
  - `get(job_id) -> Optional[dict]`  `{"state","progress","result","error"}`

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_jobs.py`)**

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_jobs.py -v`
Expected: FAIL.

- [ ] **Step 3: jobs.py 구현**

```python
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
                           "total": 5, "pct": 0}, "result": None, "error": None, "_ts": time.time()}
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_jobs.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/jobs.py tests/test_jobs.py
git commit -m "feat: jobs — 인메모리 잡 저장소 + 진행률"
```

---

## Task 8: main.py — FastAPI 라우트·백그라운드 실행

**Files:**
- Create: `app/main.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `JobStore`, `pipeline`, `DartClient`, `KrxClient`.
- Produces: FastAPI 앱 `app`, 라우트 `/`, `/api/analyze`, `/api/status/{job_id}`, `/api/result/{job_id}`.
- 테스트를 위해 클라이언트 팩토리를 모듈 전역 `CLIENT_FACTORY`로 두고 주입 가능하게 한다.

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_main.py`)**

`TestClient` + 가짜 팩토리로 네트워크 없이 전 흐름 검증.

```python
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


def setup_module(_):
    main.CLIENT_FACTORY = lambda: (FakeDart(), FakeKrx())


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: FAIL (`app.main` 없음).

- [ ] **Step 3: main.py 구현**

```python
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
    return DartClient(), KrxClient()


CLIENT_FACTORY = _default_factory


class AnalyzeReq(BaseModel):
    name: str


def _run_job(job_id, name):
    try:
        dart, krx = CLIENT_FACTORY()
        result = pipeline.run_analysis(
            name, dart, krx,
            progress_cb=lambda s, c, t: STORE.update(job_id, s, c, t))
        STORE.finish(job_id, result)
    except Exception as e:  # noqa
        STORE.fail(job_id, e)


@app.post("/api/analyze")
async def analyze(req: AnalyzeReq):
    # 종목 존재만 먼저 동기 검증 -> 즉시 400 반환 가능
    try:
        dart, _ = CLIENT_FACTORY()
        dart.resolve_corp(req.name)
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = STORE.create()
    loop = asyncio.get_event_loop()
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
```

- [ ] **Step 4: web/ 최소 파일 생성(테스트가 `/` 로딩 시 필요)**

`web/index.html`은 Task 9에서 완성하되, 이 테스트 통과를 위해 최소 뼈대를 먼저 만든다.

```bash
mkdir -p web && printf '<!doctype html><meta charset="utf-8"><title>DART PER</title><h1>ok</h1>' > web/index.html
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: 커밋**

```bash
git add app/main.py tests/test_main.py web/index.html
git commit -m "feat: main — FastAPI 비동기 잡 라우트"
```

---

## Task 9: 프론트엔드 — 검색·진행바·결과 렌더

**Files:**
- Modify/Create: `web/index.html`, `web/app.js`, `web/styles.css`

**Interfaces:**
- Consumes: `/api/analyze`, `/api/status/{id}`, `/api/result/{id}` JSON 계약(Task 5의 스키마).
- Produces: 없음(UI). 검증은 수동 브라우저 확인.

- [ ] **Step 1: web/index.html 작성**

```html
<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DART 종목 PER·PEER 분석</title>
<link rel="stylesheet" href="/static/styles.css">
</head><body>
<div class="wrap">
  <h1>DART 종목 PER·PEER 분석</h1>
  <form id="f"><input id="q" placeholder="종목명 입력 (예: 브이티)" autocomplete="off">
  <button type="submit">분석</button></form>
  <div id="progress" class="hidden"><div class="bar"><div id="fill"></div></div><p id="step"></p></div>
  <div id="error" class="hidden err"></div>
  <div id="result"></div>
</div>
<script src="/static/app.js"></script>
</body></html>
```

- [ ] **Step 2: web/styles.css 작성**

```css
body{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;background:#fafafa;color:#1a1d21}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:1.5rem}
#f{display:flex;gap:8px;margin:16px 0}
#q{flex:1;padding:10px 12px;border:1px solid #d1d5db;border-radius:10px;font-size:1rem}
button{padding:10px 18px;border:0;border-radius:10px;background:#1d4ed8;color:#fff;font-weight:600;cursor:pointer}
.hidden{display:none}
.bar{height:10px;background:#e5e7eb;border-radius:6px;overflow:hidden}
#fill{height:100%;width:0;background:#1d4ed8;transition:width .3s}
#step{color:#6b7280;font-size:.9rem;margin:8px 0}
.err{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:10px;padding:12px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:.9rem;margin:8px 0}
th,td{padding:8px 10px;border-bottom:1px solid #e5e7eb;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
th{background:#f1f5f9;color:#64748b;font-size:.82rem}
tr.target{font-weight:700;background:#fff3f0}
img.chart{width:100%;border:1px solid #eee;border-radius:12px;margin:10px 0}
.kpi{display:inline-block;border:1px solid #e5e7eb;border-radius:12px;padding:10px 14px;margin:6px 6px 6px 0;background:#fff}
.tag{font-size:.72rem;color:#fff;background:#5b8cff;border-radius:5px;padding:1px 6px;margin-left:6px}
```

- [ ] **Step 3: web/app.js 작성**

```javascript
const $ = (s) => document.querySelector(s);
const fmt = (v, dp = 1) => (typeof v === "number" ? v.toLocaleString("ko-KR",
  { minimumFractionDigits: dp, maximumFractionDigits: dp }) : "-");
const per = (v) => (typeof v === "number" ? fmt(v, 1) : "N/A(적자)");

$("#f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#q").value.trim();
  if (!name) return;
  $("#error").classList.add("hidden");
  $("#result").innerHTML = "";
  $("#progress").classList.remove("hidden");
  setProgress("요청 전송", 0);
  try {
    const r = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (r.status === 400) { showError((await r.json()).detail); return; }
    const { job_id } = await r.json();
    poll(job_id);
  } catch (err) { showError("네트워크 오류: " + err); }
});

function setProgress(step, pct) {
  $("#step").textContent = step + " (" + pct + "%)";
  $("#fill").style.width = pct + "%";
}
function showError(msg) {
  $("#progress").classList.add("hidden");
  $("#error").textContent = msg;
  $("#error").classList.remove("hidden");
}

async function poll(jobId) {
  const st = await (await fetch("/api/status/" + jobId)).json();
  if (st.state === "error") { showError(st.error || "분석 실패"); return; }
  const p = st.progress || {};
  setProgress(p.step || "진행 중", p.pct || 0);
  if (st.state === "done") {
    const res = await (await fetch("/api/result/" + jobId)).json();
    $("#progress").classList.add("hidden");
    render(res);
    return;
  }
  setTimeout(() => poll(jobId), 1500);
}

function render(res) {
  const t = res.target, s = res.stats;
  const rows = res.peers.map((p) =>
    `<tr class="${p.is_target ? "target" : ""}"><td>${p.name}</td>
     <td class="num">${fmt(p.market_cap, 0)}</td><td class="num">${fmt(p.op_3m, 0)}</td>
     <td class="num">${fmt(p.op_annualized, 0)}</td><td class="num">${per(p.per_op)}</td>
     <td class="num">${per(p.krx_per)}</td></tr>`).join("");
  const disc = (res.disclosures || []).slice(0, 20).map((d) =>
    `<li>[${d.date}] <b>${d.title}</b><span class="tag">${d.type}</span></li>`).join("");
  $("#result").innerHTML = `
    <h2>${t.name} <small>(${t.stock_code || ""})</small></h2>
    <div>
      <span class="kpi">시총 <b>${fmt(t.market_cap, 0)}</b>억</span>
      <span class="kpi">연환산 영업이익 <b>${fmt(t.op_annualized, 0)}</b>억</span>
      <span class="kpi">PER(영업이익, 연환산) <b>${per(t.per_op)}</b></span>
      <span class="kpi">업종 중앙값 <b>${fmt(s.median)}</b> · 순위 ${s.rank || "-"}/${s.count || "-"}</span>
    </div>
    <img class="chart" src="data:image/png;base64,${res.chart_per_b64}">
    <table><thead><tr><th>종목</th><th class="num">시총(억)</th><th class="num">최근분기 영업익(억)</th>
    <th class="num">연환산(억)</th><th class="num">PER(영업이익)</th><th class="num">KRX PER</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <h2>최근 공시</h2><ul>${disc || "<li>최근 공시 없음</li>"}</ul>
    <p style="color:#6b7280;font-size:.8rem;margin-top:24px">
    ※ OpenDART·KRX 자동 집계. 투자자문·매매판단을 제공하지 않습니다.</p>`;
}
```

- [ ] **Step 4: 수동 검증 (로컬 서버 기동)**

Run:
```bash
export OPENDART_API_KEY="발급받은키"
python3 -m uvicorn app.main:app --reload --port 8000
```
브라우저에서 `http://localhost:8000` → "브이티" 검색 → 진행바가 5단계로 차오르고 PEER 비교표·차트·공시가 렌더되는지 확인. 적자 종목은 `N/A(적자)` 표기 확인.

- [ ] **Step 5: 커밋**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat: 프론트 — 검색/진행바/결과 렌더"
```

---

## Task 10: 배포(HF Spaces Docker) · 문서 · 스모크

**Files:**
- Create: `Dockerfile`, `README.md`, `tests/test_smoke_live.py`

**Interfaces:**
- Produces: 배포 산출물. 실 API 스모크는 opt-in.

- [ ] **Step 1: Dockerfile 작성 (HF Spaces Docker, 포트 7860)**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
ENV PORT=7860
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
```

- [ ] **Step 2: README.md 작성 (로컬 실행 + HF Spaces 배포 절차)**

````markdown
# DART 종목 PER·PEER 분석 웹앱

종목명을 검색하면 DART 재무·공시와 KRX 시세로 연환산 영업이익 기준 PER을
계산하고 동일 업종 시총 상위 5개 PEER와 비교한다.

## 로컬 실행
```bash
pip install -r requirements.txt
export OPENDART_API_KEY="발급받은40자리키"
uvicorn app.main:app --reload --port 8000
# http://localhost:8000
```

## 테스트
```bash
python3 -m pytest -v            # 네트워크 없는 단위/통합
RUN_LIVE=1 python3 -m pytest tests/test_smoke_live.py -v   # 실 API(키 필요)
```

## Hugging Face Spaces 배포
1. New Space → SDK: **Docker** 선택
2. 이 저장소를 Space에 push (Dockerfile 포함)
3. Space **Settings → Secrets** 에 `OPENDART_API_KEY` 추가
4. 빌드 완료 후 Space URL로 접속

## PER 정의
`PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4)`.
순이익 기준 KRX 공식 PER을 교차검증 컬럼으로 병기한다. 영업이익 적자는 N/A.

## 면책
OpenDART·KRX 데이터를 자동 집계한 자료로 투자자문·매매판단을 제공하지 않는다.
````

- [ ] **Step 3: 실 API 스모크 테스트 작성 (opt-in)**

```python
import os
import pytest


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="실 API 스모크는 RUN_LIVE=1 일 때만")
def test_live_vt():
    from app.dart_client import DartClient
    from app.krx_client import KrxClient
    from app import pipeline
    res = pipeline.run_analysis("브이티", DartClient(), KrxClient())
    assert res["target"]["name"]
    assert "per_op" in res["target"]
    assert len(res["peers"]) >= 1
```

- [ ] **Step 4: 전체 단위 테스트 통과 확인**

Run: `python3 -m pytest -v`
Expected: 모든 네트워크 없는 테스트 PASS. `test_smoke_live`는 SKIP.

- [ ] **Step 5: (선택) 실 API 스모크**

Run: `RUN_LIVE=1 OPENDART_API_KEY=... python3 -m pytest tests/test_smoke_live.py -v`
Expected: PASS 또는 실데이터 기반 결과 확인.

- [ ] **Step 6: 커밋**

```bash
git add Dockerfile README.md tests/test_smoke_live.py
git commit -m "feat: HF Spaces 배포 산출물 + 실API 스모크 + 문서"
```

---

## Task 11: news_client.py — 뉴스·블로그 수집 + 최근 1개월 필터

**Files:**
- Create: `app/news_client.py`, `tests/test_news_client.py`
- Modify: `requirements.txt` (`anthropic` 추가)

> **변경(2026-08-14)**: 네이버 검색 API 키 발급이 불가하여 **구글 뉴스 RSS를 기본 소스**로, **네이버 뉴스 검색 HTML 크롤링을 보조 소스**로 사용한다. API 키 불필요.

**Interfaces:**
- Consumes: `config`.
- Produces:
  - `parse_rss_date(pubdate: str) -> Optional[datetime]` (RFC1123, 구글뉴스 `<pubDate>`)
  - `parse_relative_date(text: str, now: datetime) -> Optional[datetime]` ("3일 전"/"2026.08.11." 등 네이버 표기)
  - `filter_recent(items, days, now) -> List[dict]` (published None/초과 제외)
  - `dedup(items) -> List[dict]` (정규화 title+url 기준)
  - `parse_google_rss(xml_text: str) -> List[dict]` (item: `{"title","snippet","url","source","published"}`)
  - `parse_naver_html(html_text: str, now) -> List[dict]`
  - `class NewsClient(fetch=None)` — `fetch(url) -> str` 주입해 네트워크 없이 테스트. `fetch_recent(company, stock_name, days=30, now=None) -> List[dict]`
    - 구글 RSS 우선 수집 → 결과 부족(<5건) 시 네이버 HTML 보조 수집 → 병합·중복제거·1개월 필터·최신순 상한 25건.

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_news_client.py`)**

```python
from datetime import datetime, timezone, timedelta
from app import news_client as nc

KST = timezone(timedelta(hours=9))


def test_parse_news_date_rfc1123():
    d = nc.parse_news_date("Mon, 11 Aug 2026 09:30:00 +0900")
    assert d.year == 2026 and d.month == 8 and d.day == 11


def test_parse_blog_date_yyyymmdd():
    d = nc.parse_blog_date("20260811")
    assert d.year == 2026 and d.month == 8 and d.day == 11


def test_filter_recent_excludes_old_and_undated():
    now = datetime(2026, 8, 14, tzinfo=KST)
    items = [
        {"title": "a", "url": "u1", "published": datetime(2026, 8, 10, tzinfo=KST)},
        {"title": "b", "url": "u2", "published": datetime(2026, 6, 1, tzinfo=KST)},
        {"title": "c", "url": "u3", "published": None},
    ]
    out = nc.filter_recent(items, days=30, now=now)
    assert [i["title"] for i in out] == ["a"]


def test_dedup_by_title_url():
    items = [{"title": "속보 A", "url": "u1"}, {"title": "속보  A", "url": "u1"},
             {"title": "B", "url": "u2"}]
    assert len(nc.dedup(items)) == 2


def test_fetch_recent_maps_and_filters():
    now = datetime(2026, 8, 14, tzinfo=KST)

    def fake_get(url, params, headers):
        if "news" in url:
            return {"items": [
                {"title": "<b>브이티</b> 실적 호조", "description": "영업익 증가",
                 "originallink": "http://n1", "pubDate": "Mon, 11 Aug 2026 09:30:00 +0900"},
                {"title": "옛날 기사", "description": "브이티", "originallink": "http://old",
                 "pubDate": "Mon, 01 Jun 2026 09:30:00 +0900"},
            ]}
        return {"items": [
            {"title": "브이티 블로그", "description": "리뷰", "link": "http://b1", "postdate": "20260812",
             "bloggername": "뷰티로그"}]}

    client = nc.NewsClient(get=fake_get)
    items = client.fetch_recent("브이티", "브이티", days=30, now=now)
    titles = [i["title"] for i in items]
    assert "브이티 실적 호조" in titles      # HTML 태그 제거됨
    assert "옛날 기사" not in titles         # 1개월 초과 제외
    assert any(i["source"] == "뷰티로그" for i in items)  # 블로그 매핑
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_news_client.py -v`
Expected: FAIL (`app.news_client` 없음).

- [ ] **Step 3: news_client.py 구현**

```python
import re
import html
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional

import requests

NAVER_NEWS = "https://openapi.naver.com/v1/search/news.json"
NAVER_BLOG = "https://openapi.naver.com/v1/search/blog.json"
KST = timezone(timedelta(hours=9))


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def parse_news_date(pubdate):
    try:
        return parsedate_to_datetime(pubdate)
    except Exception:
        return None


def parse_blog_date(postdate):
    try:
        return datetime.strptime(str(postdate), "%Y%m%d").replace(tzinfo=KST)
    except Exception:
        return None


def filter_recent(items, days, now):
    cutoff = now - timedelta(days=days)
    out = []
    for it in items:
        p = it.get("published")
        if p is None:
            continue
        if p.tzinfo is None:
            p = p.replace(tzinfo=KST)
        if p >= cutoff:
            out.append(it)
    return out


def dedup(items):
    seen, out = set(), []
    for it in items:
        k = _norm(it.get("title")) + "|" + (it.get("url") or "")
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


class NewsClient:
    def __init__(self, get=None):
        self._get = get or self._http_get

    def _http_get(self, url, params, headers):
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def _headers(self):
        cid = os.environ.get("NAVER_CLIENT_ID")
        csec = os.environ.get("NAVER_CLIENT_SECRET")
        if not cid or not csec:
            return None
        return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}

    def fetch_recent(self, company, stock_name, days=30, now=None):
        now = now or datetime.now(KST)
        headers = self._headers()
        if headers is None:
            return []  # 키 없음 -> 상위에서 status=disabled
        query = company or stock_name
        raw = []
        try:
            news = self._get(NAVER_NEWS, {"query": query, "display": 30, "sort": "date"}, headers)
            for it in news.get("items", []):
                raw.append({"title": _strip(it.get("title")), "snippet": _strip(it.get("description")),
                            "url": it.get("originallink") or it.get("link"),
                            "source": "뉴스", "published": parse_news_date(it.get("pubDate"))})
        except Exception:
            pass
        try:
            blog = self._get(NAVER_BLOG, {"query": query, "display": 20, "sort": "date"}, headers)
            for it in blog.get("items", []):
                raw.append({"title": _strip(it.get("title")), "snippet": _strip(it.get("description")),
                            "url": it.get("link"), "source": _strip(it.get("bloggername")) or "블로그",
                            "published": parse_blog_date(it.get("postdate"))})
        except Exception:
            pass
        # 관련성: 제목/스니펫에 회사명 포함
        key = _norm(company or stock_name)
        rel = [i for i in raw if key in _norm(i["title"]) or key in _norm(i["snippet"])]
        recent = filter_recent(rel, days, now)
        recent = dedup(recent)
        recent.sort(key=lambda i: i["published"], reverse=True)
        return recent[:25]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_news_client.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: requirements.txt에 `anthropic` 추가 후 커밋**

```bash
git add app/news_client.py tests/test_news_client.py requirements.txt
git commit -m "feat: news_client — 뉴스/블로그 수집 + 최근 1개월 필터"
```

---

## Task 12: insights.py — Claude 투자포인트·리스크 요약

**Files:**
- Create: `app/insights.py`, `tests/test_insights.py`

**Interfaces:**
- Consumes: `news_client` 아이템.
- Produces:
  - `build_prompt(company: str, items: List[dict]) -> str` (번호 매긴 항목 목록)
  - `summarize(items: List[dict], company: str, claude=None, as_of=None) -> dict` — `claude(prompt)->str`(JSON 문자열) 주입 가능. 반환 스키마는 스펙 §16.5.
  - `status`: 자료 0건 → `no_data`, claude 없음/오류 → `disabled`, 정상 → `ok`.

- [ ] **Step 1: 실패 테스트 작성 (`tests/test_insights.py`)**

```python
from app import insights


def _items():
    return [
        {"title": "브이티 2분기 영업익 급증", "snippet": "북미 매출 확대", "url": "http://n1",
         "source": "뉴스", "published": None},
        {"title": "브이티 밸류 부담 지적", "snippet": "PER 고평가 우려", "url": "http://n2",
         "source": "뉴스", "published": None},
    ]


def test_no_data_status():
    res = insights.summarize([], company="브이티")
    assert res["status"] == "no_data"


def test_disabled_when_no_claude():
    # claude 미주입 + 키 없음 -> disabled, 원문은 sources로 노출
    res = insights.summarize(_items(), company="브이티", claude=lambda p: (_ for _ in ()).throw(RuntimeError()))
    assert res["status"] == "disabled"
    assert len(res["sources"]) == 2


def test_summarize_maps_sources():
    fake_json = ('{"investment_points":[{"text":"북미 매출 확대","sources":[1]}],'
                 '"risks":[{"text":"밸류 부담","sources":[2]}],"overall":"중립"}')
    res = insights.summarize(_items(), company="브이티", claude=lambda p: fake_json, as_of="2026-08-14")
    assert res["status"] == "ok"
    assert res["investment_points"][0]["sources"][0]["url"] == "http://n1"
    assert res["risks"][0]["sources"][0]["url"] == "http://n2"
    assert res["as_of"] == "2026-08-14"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_insights.py -v`
Expected: FAIL (`app.insights` 없음).

- [ ] **Step 3: insights.py 구현**

```python
import os
import json
from typing import List, Optional

SYSTEM = (
    "너는 한국 주식 뉴스 요약 애널리스트다. 아래 번호가 매겨진 뉴스/블로그 항목만 근거로 "
    "투자포인트와 리스크를 한국어로 정리한다. 규칙: (1) 제공 항목에만 근거 (2) 각 포인트/리스크에 "
    "근거 항목 번호를 sources 배열로 명기 (3) 자료에 없으면 항목을 만들지 말 것 (4) 매수/매도 권유·목표주가 "
    "단정 금지, 사실·전망을 중립 서술 (5) 반드시 JSON만 출력."
)


def _src_obj(item, n):
    return {"n": n, "title": item.get("title"), "source": item.get("source"),
            "date": item["published"].strftime("%Y-%m-%d") if item.get("published") else "",
            "url": item.get("url")}


def build_prompt(company, items):
    lines = ["[대상 종목] %s" % company, "", "[항목]"]
    for i, it in enumerate(items, 1):
        lines.append("%d. (%s) %s — %s" % (i, it.get("source"), it.get("title"), it.get("snippet")))
    lines.append("")
    lines.append('출력 JSON 스키마: {"investment_points":[{"text","sources":[번호]}],'
                 '"risks":[{"text","sources":[번호]}],"overall":"2~3문장"}')
    return "\n".join(lines)


def _default_claude(prompt):
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1500,
        system=SYSTEM, messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _all_sources(items):
    return [_src_obj(it, i) for i, it in enumerate(items, 1)]


def summarize(items, company, claude=None, as_of=None):
    sources = _all_sources(items)
    base = {"as_of": as_of, "window_days": 30, "investment_points": [], "risks": [],
            "overall": "", "sources": sources}
    if not items:
        base["status"] = "no_data"
        return base
    runner = claude or _default_claude
    try:
        raw = runner(build_prompt(company, items))
        parsed = json.loads(raw)
    except Exception:
        base["status"] = "disabled"
        return base

    def _map(points):
        out = []
        for p in points or []:
            srcs = [sources[n - 1] for n in p.get("sources", []) if 1 <= n <= len(sources)]
            out.append({"text": p.get("text", ""), "sources": srcs})
        return out

    base["investment_points"] = _map(parsed.get("investment_points"))
    base["risks"] = _map(parsed.get("risks"))
    base["overall"] = parsed.get("overall", "")
    base["status"] = "ok"
    return base
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_insights.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: 커밋**

```bash
git add app/insights.py tests/test_insights.py
git commit -m "feat: insights — Claude 투자포인트/리스크 요약 + 출처 매핑"
```

---

## Task 13: 편입 — 파이프라인 6단계 · 리포트 · 프론트 섹션

**Files:**
- Modify: `app/pipeline.py`, `app/report.py`, `app/main.py`(팩토리에 news/insights 주입), `web/app.js`, `web/styles.css`, `app/config.py`(NEWS_WINDOW_DAYS=30)
- Modify: `tests/test_pipeline.py` (6단계 + insights 주입 검증)

**Interfaces:**
- Consumes: `NewsClient`, `insights`.
- Produces: `run_analysis(..., news=None, insights_fn=None)` — 주입 가능. `build_result(..., insights=None)`에 `insights` 키 추가. 진행률 total 5→6.

- [ ] **Step 1: 실패 테스트 수정 (`tests/test_pipeline.py`)**

기존 테스트에 뉴스/요약 주입과 6단계 검증을 추가한다(기존 `_sample` 흐름 유지, `run_analysis` 시그니처에 `news`, `insights_fn` 추가).

```python
def test_run_analysis_includes_insights_and_6_steps():
    from app import pipeline
    steps = []

    class FakeNews:
        def fetch_recent(self, company, stock_name, days=30, now=None):
            return [{"title": "호재", "snippet": "매출↑", "url": "http://n1",
                     "source": "뉴스", "published": None}]

    def fake_insights(items, company, as_of=None):
        return {"status": "ok", "investment_points": [{"text": "매출 성장", "sources": []}],
                "risks": [], "overall": "중립", "sources": [], "as_of": as_of, "window_days": 30}

    # FakeDart/FakeKrx 는 기존 테스트 것을 재사용
    dart, krx = _make_fakes()  # 기존 헬퍼 (없으면 test 상단 fixture 재사용)
    res = pipeline.run_analysis("브이티", dart, krx, news=FakeNews(), insights_fn=fake_insights,
                                progress_cb=lambda s, c, t: steps.append((s, c, t)))
    assert res["insights"]["status"] == "ok"
    assert steps[-1][2] == 6 and steps[-1][1] == 6
```

> 주: `_make_fakes()`가 기존 파일에 없으면, 기존 `FakeDart`/`FakeKrx`를 모듈 상단으로 올려 재사용하도록 소폭 리팩터. 새 헬퍼는 기존 fake와 동일 동작이어야 한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: FAIL (`run_analysis`에 news/insights 인자 없음, `insights` 키 없음).

- [ ] **Step 3: pipeline.py 수정 — 6단계 편입**

`config.py`에 `NEWS_WINDOW_DAYS = 30` 추가. `run_analysis` 시그니처와 마지막 단계를 아래처럼 변경.

```python
def run_analysis(name, dart, krx, news=None, insights_fn=None, progress_cb=None):
    TOTAL = 6
    # ... 1~4 단계 동일 (TOTAL만 6으로) ...

    # 5) 공시
    _emit(progress_cb, "공시 수집", 5, TOTAL)
    disc = dart.recent_disclosures(info["corp_code"])

    # 6) 뉴스·블로그 투자포인트/리스크 요약
    _emit(progress_cb, "뉴스·요약", 6, TOTAL)
    ins = {"status": "disabled", "investment_points": [], "risks": [],
           "overall": "", "sources": [], "as_of": None, "window_days": 30}
    if news is not None and insights_fn is not None:
        try:
            items = news.fetch_recent(info["corp_name"], info["corp_name"])
            ins = insights_fn(items, info["corp_name"])
        except Exception:
            pass

    all_rows.sort(key=lambda r: (r["per_op"] is None, r["per_op"] if r["per_op"] is not None else 0))
    return report.build_result(target_row, all_rows, stats, disc, deepdive=None, insights=ins)
```

- [ ] **Step 4: report.py 수정 — build_result/render_html에 insights 반영**

`build_result` 시그니처에 `insights=None` 추가, 반환 dict에 `"insights": insights` 포함. `render_html`에 투자포인트/리스크 섹션(출처 각주) 추가.

```python
def build_result(target, peers, stats, disclosures, deepdive, insights=None):
    return {
        "target": target, "peers": peers, "stats": stats,
        "disclosures": disclosures or [], "deepdive": deepdive,
        "insights": insights or {"status": "disabled"},
        "chart_per_b64": per_bar_chart_b64(peers, target["stock_code"], stats.get("median")),
    }
```

`render_html`에 추가할 블록(요지): `insights.status == "ok"` 이면 투자포인트/리스크 `<ul>` + 각 항목 뒤 출처 `[n]`, 하단 `sources` 목록(제목·매체·날짜·링크). `no_data`/`disabled`면 안내 문구.

- [ ] **Step 5: main.py 팩토리 수정 — news/insights 주입**

```python
def _default_factory():
    from app.dart_client import DartClient
    from app.krx_client import KrxClient
    from app.news_client import NewsClient
    from app import insights
    return DartClient(), KrxClient(), NewsClient(), insights.summarize
```
`_run_job`에서 `dart, krx, news, insights_fn = CLIENT_FACTORY()` 후 `pipeline.run_analysis(name, dart, krx, news=news, insights_fn=insights_fn, progress_cb=...)`. `analyze`의 400 검증은 `CLIENT_FACTORY()[0]` 사용. (기존 test_main의 FakeFactory도 4-튜플 반환하도록 업데이트.)

- [ ] **Step 6: web/app.js·styles.css — 투자포인트/리스크 섹션 렌더**

`render(res)`에 `res.insights` 처리 추가: 투자포인트(초록 계열)·리스크(주황 계열) 목록, 각 문장 뒤 출처 `[n]`(툴팁/링크), 하단 출처 리스트. `status`가 ok가 아니면 "최근 1개월 자료 없음/요약 비활성" 안내.

- [ ] **Step 7: 전체 테스트 통과 확인**

Run: `python3 -m pytest -v`
Expected: 전체 PASS(네트워크 없는 테스트), 라이브 스모크 SKIP.

- [ ] **Step 8: 커밋**

```bash
git add app/pipeline.py app/report.py app/main.py app/config.py web/app.js web/styles.css tests/test_pipeline.py
git commit -m "feat: 뉴스·요약 파이프라인 6단계 편입 + 결과 섹션"
```

---

## Self-Review (작성자 점검 결과)

**1. Spec 커버리지**
- §1 PER 계산 → Task 1(`per_op`)+Task 2(`op_3m`)+Task 6.
- §3 PEER 시총 상위 5 → Task 4(`peers_in_sector`)+config.PEER_COUNT.
- §3 타깃 심층분석 → Task 5/6에 `deepdive=None` 자리 확보(반기 실적·판관비·5개년은 `stock_report.py` 로직을 후속 반영하는 확장 지점으로 명시; 초기 릴리스는 PER·PEER·공시 중심). **주의: 심층분석 완전 이식은 별도 후속 Task로 분리 가능 — 초기 범위에서 제외됨을 사용자에게 고지.**
- §4 PER 산식 → Task 1/2, Global Constraints.
- §5 API 4종 → Task 8.
- §6 데이터 플로우 5단계 → Task 6 진행률.
- §7 에러 처리 → Task 3(retry/LookupError), Task 6(부분실패 None), Task 8(400/404).
- §8 화면 5섹션 → Task 5(HTML)+Task 9(JS). *심층분석 섹션은 초기 축소.*
- §10 캐싱 → Task 0(cache) + krx/dart에서 memoize 적용(Task 4 `_ensure`는 인메모리; 디스크 캐시 결선은 구현 시 `cache.memoize`로 감싸는 것을 권장 — 후속 최적화).
- §12 HF Spaces → Task 10.
- §13 테스트 → 각 Task TDD + Task 10 스모크.

**2. Placeholder 스캔:** 실행 코드 스텝은 모두 실제 코드 포함. `deepdive`는 명시적 축소 결정(placeholder 아님).

**3. 타입 일관성:** `op_3m`/`op_annualized`/`per_op`/`market_cap`/`stock_code`/`corp_code` 명칭이 dart_client·krx_client·metrics·pipeline·report 전반에서 일치. 진행률 콜백 시그니처 `(step, current, total)` 일관.

**알려진 축소(사용자 확인 필요):** 타깃 "심층분석"(반기 YoY·판관비 분해·5개년 추이)의 완전 이식은 초기 범위에서 제외하고 PER·PEER·공시 중심으로 릴리스. 필요 시 Task 11로 `stock_report.py` 로직을 `deepdive`에 결선하는 후속 계획을 추가한다.
