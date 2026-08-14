# DART 기반 종목 PER·PEER 비교 웹앱 — 설계서

- 작성일: 2026-08-14
- 상태: 승인 대기 (사용자 리뷰 게이트)
- 기반 자산: 기존 `stock_report.py`(단일 종목 반기 실적·판관비·5개년 추이 HTML 생성기)

---

## 1. 목적 · 범위

종목명을 웹 검색창에 입력하면, DART 공시·재무 데이터와 KRX 시세를 결합해 다음을 산출·표시하는 인터랙티브 웹앱을 만든다.

1. 타깃 종목의 **연환산 영업이익 기준 PER** 계산 (`시가총액 ÷ (최근 분기 영업이익 × 4)`)
2. 동일 업종 **시총 상위 5개 PEER 그룹**과의 PER 비교
3. 타깃 종목 **심층 재무분석**(반기 실적 YoY·판관비 분해·5개년 추이) — 기존 스크립트 재사용
4. 타깃 + PEER **최근 공시내역** 요약

### 범위 밖 (YAGNI)

- 사용자 계정·로그인·즐겨찾기
- 실시간 호가/체결, 장중 틱 데이터
- 사전 배치 프리컴퓨트 인프라(하이브리드 캐시 방식은 채택하지 않음)
- 투자 자문/매매 신호 (면책 고지만 표기)

---

## 2. 확정된 결정사항

| 항목 | 결정 |
|---|---|
| 결과물 형태 | 검색창 웹앱 (종목명 검색 → 결과 HTML 렌더링) |
| 요청 처리 모델 | **비동기 잡 + 진행률 폴링** |
| 백엔드 | Python + FastAPI |
| 재무·공시 데이터 | OpenDartReader (DART API) |
| 주가·시가총액·상장주식수 | pykrx |
| 업종 분류(PEER 후보 발굴) | FinanceDataReader `StockListing('KRX')`의 `Sector`/`Industry` |
| PEER 선정 | 동일 업종 **시총 상위 5개** |
| PER 산식 | `시가총액 ÷ (최근 분기 영업이익 × 4)` (연환산, 영업이익 기준) |
| 배포 | **Hugging Face Spaces (Docker, 무료 CPU)** |

---

## 3. 아키텍처

```
[브라우저 / web]                     [FastAPI 백엔드 / app]
 index.html (검색창)                  POST /api/analyze  {name}       → {job_id}
   │  fetch                           GET  /api/status/{job_id}       → {state, progress, steps}
   ▼                                  GET  /api/result/{job_id}       → 결과 JSON(+HTML)
 app.js ── 폴링(1~2초) ──►  진행바     GET  /                          → 검색 페이지(정적)
   ▲                                     │
   └──── 완료 시 결과 렌더 ◄─────────────┘
                                       백그라운드 워커 (스레드풀)
                                         ├ dart_client   (OpenDartReader → DART API)
                                         ├ krx_client    (pykrx 시총/주가 · FDR 업종목록)
                                         ├ quarterly     (최근 분기 영업이익 추출)
                                         ├ metrics       (PER 연환산 · PEER 순위/중앙값)
                                         └ report        (JSON + HTML + 차트)
```

- pykrx·OpenDartReader·FDR은 **블로킹 I/O**이므로 백그라운드 잡은 `run_in_executor`(스레드풀)에서 실행한다. FastAPI 이벤트 루프는 막지 않는다.
- 잡 상태는 **인메모리 저장소**(`dict[job_id] → JobState`, TTL 30분)에 보관한다. 무료 티어 콜드스타트로 프로세스가 잠들면 진행 중 잡은 소실될 수 있으나, 잡은 짧고 재실행 가능하므로 허용한다.
- 잡은 단계마다 `progress`(현재 단계명, current/total, pct)를 갱신하고, 프론트가 이를 폴링해 진행바로 표시한다.

---

## 4. 컴포넌트별 책임 (인터페이스 중심)

각 모듈은 하나의 목적을 갖고 잘 정의된 입출력으로 통신한다. 네트워크 의존 모듈(`dart_client`, `krx_client`)과 순수 계산 모듈(`metrics`, `quarterly`)을 분리해 후자를 네트워크 없이 단위테스트한다.

### `config.py`
- 상수: 보고서 코드(`11013`=1Q, `11012`=반기, `11014`=3Q, `11011`=사업), 단위 환산(원→억), PEER 개수(=5), 캐시 TTL.
- 환경변수: `OPENDART_API_KEY`.

### `cache.py`
- 디스크+메모리 2단 캐시. 키별 TTL.
- 확정 공시 재무(`(corp_code, year, reprt)`)는 불변 → 장기 캐시. 시총 스냅샷·FDR 목록은 **당일** 캐시.
- 인터페이스: `get(key)`, `set(key, value, ttl)`, `cached(fn, key, ttl)`.

### `dart_client.py` (OpenDartReader 래퍼)
- `resolve_corp(name_or_code) -> {corp_code, corp_name, stock_code, induty_code}`
- `finstate(corp_code, year, reprt, fs_div) -> DataFrame` (재시도·캐시 포함)
- `recent_disclosures(corp_code, days=90) -> list[{date, title, type, rcept_no, url}]`
- 기존 `stock_report.py`의 `retry`/`resolve_corp`/`finstate_all` 사용 패턴을 이관.

### `krx_client.py` (pykrx + FinanceDataReader 래퍼)
- `market_cap_snapshot(date) -> DataFrame[ticker → 시총, 상장주식수, 종가]` (pykrx `get_market_cap_by_ticker`, 당일 캐시)
- `krx_fundamentals(date) -> DataFrame[ticker → PER, PBR, EPS, ...]` (pykrx `get_market_fundamental_by_ticker`, 교차검증용)
- `listing() -> DataFrame[Code, Name, Market, Sector, Industry]` (FDR `StockListing('KRX')`, 당일 캐시)
- `sector_of(stock_code) -> str`
- `peers_in_sector(sector, exclude=stock_code) -> list[stock_code]` (시총 정렬 상위 반환)

### `quarterly.py` (최근 분기 영업이익 추출 — 순수 로직 + df 입력)
- `latest_quarter_op(dart_client, corp_code, fs_div) -> {year, reprt, quarter, op_3m, source}`
- 경계 처리:
  - 최신 보고서가 1Q(11013): `op_3m = 누적(=3개월)`
  - 반기(11012): `op_3m = thstrm_amount(Q2 3개월)`
  - 3Q(11014): `op_3m = thstrm_amount(Q3 3개월)`
  - 사업보고서(11011): 3개월 필드 없음 → `op_3m = 연간누적 − 3분기누적` (직전 3Q 보고서 조회)
- 영업이익 계정은 `dart_OperatingIncomeLoss` / `ifrs-full_ProfitLossFromOperatingActivities` account_id로 매칭(회사·연도별 계정명 차이 흡수).

### `metrics.py` (순수 계산)
- `annualize(op_3m) -> op_ttm_proxy` (= `op_3m * 4`)
- `per_op(market_cap, op_annualized) -> float | None` (영업이익 ≤ 0 이면 None)
- `peer_stats(list_of_per) -> {median, min, max, count}`
- `rank_within(target_per, peer_pers) -> {rank, percentile}`
- 기존 스크립트의 마진율(영업이익률·판관비율)·YoY 계산 함수 이관.

### `report.py`
- `build_result(target, peers, disclosures, deepdive) -> dict` (프론트 렌더용 JSON)
- `render_html(result) -> str` (자기완결형 HTML — 기존 차트·표 스타일 재사용; 결과 페이지 임베드/다운로드 겸용)
- 차트(PER 막대, 실적 추이, 판관비 드라이버)는 기존 matplotlib 로직을 이관해 base64 PNG로 임베드.

### `pipeline.py` (오케스트레이션)
- `run_analysis(name, progress_cb) -> result` — §6 데이터 플로우를 순서대로 실행하며 `progress_cb(step, i, n)` 호출.

### `jobs.py`
- `JobStore`: `create() -> job_id`, `update(job_id, progress)`, `finish(job_id, result)`, `fail(job_id, error)`, `get(job_id)`, TTL 청소.

### `main.py` (FastAPI)
- 라우트 4종(§5), 정적 파일(`web/`) 서빙, 백그라운드 잡 기동(`run_in_executor`).

---

## 5. API 스펙

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/` | — | 검색 페이지(HTML) |
| POST | `/api/analyze` | `{ "name": "브이티" }` | `{ "job_id": "..." }` (202) / 종목 미발견 시 400 + 유사후보 |
| GET | `/api/status/{job_id}` | — | `{ "state": "running\|done\|error", "progress": {"step": "...", "current": 3, "total": 5, "pct": 60}, "error": null }` |
| GET | `/api/result/{job_id}` | — | 결과 JSON(`report.build_result`) — done 상태에서만 |

- 프론트 흐름: POST → job_id 수신 → status 폴링(1~2초) → done이면 result 조회·렌더.

---

## 6. 데이터 플로우 (잡 1회 = 진행률 5단계)

| 단계 | 작업 | 소스 |
|---|---|---|
| 1/5 종목 해석 | 종목명 → corp_code, stock_code, 업종 | DART + FDR |
| 2/5 시총·업종 | 시총 스냅샷 조회, 동일 업종 시총 상위 5개 PEER 확정 | pykrx + FDR |
| 3/5 PEER 실적 | PEER 5개 각각 최근 분기 영업이익 조회(캐시) | DART |
| 4/5 PER 계산 | 시총 ÷ 연환산 영업이익 → PER, 중앙값·순위 산출 | metrics |
| 5/5 타깃 심층 | 반기 실적 YoY·판관비 분해·5개년 추이·최근 공시 | DART (기존 로직) |

- 타깃이 업종 시총 5위 밖이면 PEER 5개 + 타깃 = 6행으로 표시(타깃 하이라이트).
- 각 단계 종료 시 `progress_cb` 호출 → 프론트 진행바 갱신.

---

## 7. PER 산식 (정의 고정)

```
연환산 영업이익 = 최근 분기 영업이익(3개월, discrete) × 4
PER(영업이익 기준) = 시가총액 ÷ 연환산 영업이익
```

- **주의**: 이는 순이익(EPS) 기준 정통 PER이 아니라 **영업이익 기준 배수(P/OP)** 이며, 화면·설계 전반에서 "PER(영업이익 기준, 연환산)"으로 명확히 라벨링한다.
- 영업이익 ≤ 0 → `N/A(적자)`.
- 계절성 취약(최근 분기×4)은 감수한다(사용자 선택). pykrx가 제공하는 **KRX 공식 PER(순이익 TTM 기준)** 을 교차검증 컬럼으로 병기한다.

---

## 8. 화면 구성 (결과 페이지)

1. **헤더 KPI**: 시총 / 연환산 영업이익 / PER(영업이익) / 업종 PER 중앙값 대비 위치(저평가·고평가)
2. **PEER 비교표**: 종목 · 시총 · 최근분기 영업이익 · 연환산 · PER(영업이익) · KRX PER — 타깃 행 하이라이트, PER 오름차순
3. **PER 막대차트**: PEER(+타깃) PER 비교, 업종 중앙값 점선 오버레이
4. **타깃 심층분석**: 반기 실적 YoY 표 · 판관비 세부 분해 · 5개년 추이 차트 (기존 `stock_report.py` 재사용)
5. **최근 공시**: 타깃+PEER 최근 90일 공시 목록, 유형 태그(정기·주요사항·발행 등) + 실적/증자 등 주요 이벤트 하이라이트
6. **면책 고지**: 자동 집계 자료이며 투자자문·매매판단을 제공하지 않음.

---

## 9. 에러 처리

| 상황 | 처리 |
|---|---|
| 종목 미발견 | 400 + 유사 후보명 제시 |
| DART/pykrx/FDR 일시 장애 | `retry` 래퍼(지수 백오프), 부분 실패 PEER는 `N/A` 표기 후 잡 계속 |
| 영업이익 적자·데이터 결측 | 셀 단위 `N/A`, 전체 크래시 금지 |
| DART 레이트리밋 | 백오프 + 캐시 우선 |
| 잡 내부 예외 | `JobStore.fail`로 상태 error + 메시지, 프론트에 노출 |

---

## 10. 캐싱 · 성능

- corp_code 맵(기존 pkl) · FDR 업종목록 · pykrx 시총 스냅샷: **당일 캐시**.
- 종목별 분기재무(확정 공시 → 불변): `(corp_code, year, reprt)` 키 **디스크+메모리 장기 캐시**.
- PEER 5개 한정 + 캐시로, 재검색·업종 중복 종목은 즉답.
- 시총·펀더멘털은 pykrx의 전 종목 일괄 조회 1회로 해결(PEER별 추가 호출 없음).

---

## 11. 프로젝트 구조

```
주식/
  app/
    main.py          # FastAPI 앱·라우트·잡 기동
    jobs.py          # 인메모리 잡 저장소 + 진행률
    pipeline.py      # 분석 오케스트레이션(진행률 콜백)
    dart_client.py   # OpenDartReader 래퍼(종목해석·재무·공시)
    krx_client.py    # pykrx + FDR 래퍼(시총·주가·업종)
    quarterly.py     # 최근 분기 영업이익 추출(경계 로직)
    metrics.py       # PER·연환산·중앙값·순위·마진
    report.py        # 결과 JSON + HTML + 차트
    cache.py         # 디스크+메모리 캐시
    config.py        # 환경·상수
  web/
    index.html       # 검색 페이지(검색창·진행바·결과영역)
    app.js           # fetch·폴링·렌더
    styles.css
  tests/
    test_metrics.py   # PER/연환산/중앙값/순위 (네트워크 없음)
    test_quarterly.py # discrete 분기 추출 (픽스처)
    test_pipeline.py  # 모킹 클라이언트 통합
  requirements.txt   # fastapi, uvicorn, OpenDartReader, pykrx, finance-datareader, pandas, matplotlib, requests, lxml
  Dockerfile         # HF Spaces(Docker) 배포용
  README.md
  docs/superpowers/specs/2026-08-14-dart-peer-per-web-design.md  # 본 문서
```

- 기존 `stock_report.py`의 차트·손익·판관비 로직을 `report.py`/`metrics.py`/`quarterly.py`로 **모듈화 이관**한다(모놀리식 스크립트 분해).

---

## 12. 배포 (Hugging Face Spaces)

- **Docker Space**로 배포. `Dockerfile`에서 `uvicorn app.main:app` 기동, 포트 7860(HF 기본).
- `OPENDART_API_KEY`는 Space **Secrets**에 저장.
- 무료 CPU 상시 실행. 콜드스타트 첫 요청 지연은 비동기 진행바로 흡수.
- 아웃바운드 네트워크(DART·KRX) 허용됨(HF Spaces는 외부 호출 가능).

---

## 13. 테스트 전략 (TDD)

- **순수함수 우선**: `metrics`(PER·연환산·중앙값·순위), `quarterly`(discrete 분기 경계) — DataFrame 픽스처로 네트워크 없이 검증.
- **클라이언트 래퍼**: 녹화된 응답 픽스처로 모킹, 파싱·매칭 로직 검증.
- **파이프라인**: 모킹된 dart/krx 클라이언트로 5단계 플로우 통합 테스트 1개.
- **실 API 스모크**(옵트인, `OPENDART_API_KEY` 있을 때만): 브이티로 엔드투엔드 1회.

---

## 14. 미해결/후속 확인 항목

- FDR `StockListing('KRX')`의 `Sector` 분류 세분도 검증 필요(너무 넓으면 `Industry`로 좁히는 옵션 추가). → 구현 초기 스파이크로 확인.
- 사업보고서(11011)만 최신인 종목의 Q4 discrete 산출 정확도 → `quarterly` 단위테스트로 고정.
- KRX PER과 자체 산식 PER 괴리 표기 방식(툴팁/각주) → 구현 시 확정.

---

## 15. 면책

본 앱은 금융감독원 OpenDART 공시와 KRX 시세를 자동 집계·정리한다. 수치의 정리·해석을 목적으로 하며, 특정 종목의 매수/매도 등 투자 판단이나 투자 자문을 제공하지 않는다. 투자의 최종 판단과 책임은 이용자 본인에게 있다.
