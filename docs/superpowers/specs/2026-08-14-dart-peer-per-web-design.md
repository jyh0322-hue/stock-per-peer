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
| 뉴스·블로그 수집 | **네이버 검색 API(공식) + 웹 크롤링 폴백** |
| 투자포인트·리스크 요약 | **Claude API (Anthropic)** |
| 텔레그램·증권 리서치 | 초기 제외(후속 확장 지점) |

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

## 6. 데이터 플로우 (잡 1회 = 진행률 6단계)

| 단계 | 작업 | 소스 |
|---|---|---|
| 1/6 종목 해석 | 종목명 → corp_code, stock_code, 업종 | DART + FDR |
| 2/6 시총·업종 | 시총 스냅샷 조회, 동일 업종 시총 상위 5개 PEER 확정 | pykrx + FDR |
| 3/6 PEER 실적 | PEER 5개 각각 최근 분기 영업이익 조회(캐시) | DART |
| 4/6 PER 계산 | 시총 ÷ 연환산 영업이익 → PER, 중앙값·순위 산출 | metrics |
| 5/6 공시·조립 | 타깃+PEER 최근 공시(DART `list`) | DART |
| 6/6 뉴스·요약 | 뉴스·블로그 최근 1개월 수집 → Claude 투자포인트·리스크 요약 | 네이버 API + Claude |

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
6. **투자포인트·리스크 요약**: 최근 1개월 뉴스·블로그 기반 Claude 요약. 투자포인트/리스크 각 3~5개, 문장마다 출처 각주 `[n]` + 하단 출처 목록(제목·매체·날짜·URL). §16 참조.
7. **면책 고지**: 자동 집계 자료이며 투자자문·매매판단을 제공하지 않음.

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
- **키 관리**: 로컬은 프로젝트 루트 `.env` 파일(python-dotenv 자동 로드, `.gitignore` 제외), 배포는 Space **Secrets**. 같은 코드가 양쪽을 읽음(이미 설정된 환경변수는 `.env`가 덮어쓰지 않음).
- 키 목록: `OPENDART_API_KEY`(필수), `ANTHROPIC_API_KEY`(뉴스 요약), `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`(뉴스·블로그 API). 뉴스 관련 키 미설정 시 §16 섹션만 비활성(graceful degrade), 나머지 기능은 정상.
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

---

## 16. 최근 뉴스·블로그 투자포인트·리스크 요약 (신규 서브시스템)

타깃 종목에 대한 **최근 1개월** 뉴스·블로그를 수집해, Claude로 **투자포인트**와 **리스크**를 구조화 요약하고 **모든 문장에 출처를 명기**한다.

### 16.1 목표 · 원칙

- 최근 **1개월(30일)** 발행분만 대상. 그보다 오래된 자료는 로직에서 제외.
- 요약의 모든 항목은 **수집된 자료에만 근거**(환각 금지). 문장마다 출처 인덱스 `[n]`를 부여.
- 출처는 **제목·매체(또는 블로그명)·발행일·URL**을 명기.
- 개인화된 투자자문이 아니라 **공개 자료의 정리·요약**임을 명확히 표기(면책 강화).
- 뉴스 관련 키/자료가 없으면 이 섹션만 비활성화하고 나머지 결과는 정상 제공(graceful degrade).

### 16.2 데이터 소스 (초기: 뉴스 + 블로그)

| 소스 | 접근 | 날짜 필드 | 비고 |
|---|---|---|---|
| 네이버 뉴스 | 검색 API `/v1/search/news.json` | `pubDate`(RFC1123) | 1차 |
| 네이버 블로그 | 검색 API `/v1/search/blog.json` | `postdate`(yyyymmdd) | 1차 |
| 웹 크롤링 | 네이버 뉴스 검색 결과 페이지 | 목록의 날짜 파싱 | **폴백**(키 없음/실패 시) |

- 키: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`.
- **후속 확장 지점**(초기 제외): 텔레그램 공개 채널(`t.me/s/<채널>`), 증권사 리서치(한경컨센서스 등). `news_client`에 소스 어댑터를 추가하는 형태로 확장.

### 16.3 수집 · 1개월 필터 로직

```
검색어 = 회사명(정식) OR 종목명
raw = 뉴스검색(검색어, display=30) + 블로그검색(검색어, display=20)
cutoff = today - 30일
items = []
for it in raw:
    dt = parse_date(it)                 # pubDate/postdate/크롤링 날짜
    if dt is None: continue             # 날짜 불명은 보수적으로 제외
    if dt < cutoff: continue            # 1개월 초과 제외
    items.append({title, snippet, url, source, published: dt})
items = dedup(items, key=정규화(title)+url)   # 중복 기사 제거
items = sort_desc(items, by=published)[:MAX_ITEMS]   # 최신순 상한(예: 25)
```

- `parse_date`: 뉴스 `pubDate`(RFC1123) → datetime, 블로그 `postdate`(yyyymmdd) → datetime, 크롤링은 "N일 전/YYYY.MM.DD" 파싱.
- 광고성/무관 필터: 제목·스니펫에 회사명이 포함되지 않으면 제외(간단 관련성 필터).

### 16.4 요약 (Claude) — 구조화 출력

- 수집된 `items`(제목+스니펫+날짜+매체, 본문 전체 아님)를 **번호 매겨** 하나의 프롬프트로 전달, **1회 호출**.
- Claude에 JSON 스키마 강제:
```json
{
  "investment_points": [{"text": "...", "sources": [1, 4]}],
  "risks": [{"text": "...", "sources": [2]}],
  "overall": "2~3문장 중립 요약"
}
```
- 프롬프트 지침(핵심): "① 제공된 항목에만 근거할 것 ② 각 포인트/리스크에 근거 항목 번호를 `sources`로 명기 ③ 자료에 없으면 항목을 만들지 말 것 ④ 매수/매도 권유·목표주가 단정 금지, 사실·전망을 중립 서술 ⑤ 한국어."
- 반환된 `sources` 번호를 수집 `items` 인덱스와 매핑해 **출처 객체**로 치환.
- 모델: 비용·품질 균형을 위해 경량 Claude 모델 사용(설정 가능). 입력은 스니펫만이라 토큰 통제됨.

### 16.5 출력 스키마 (`insights`)

```json
{
  "as_of": "2026-08-14",
  "window_days": 30,
  "investment_points": [{"text": "...", "sources": [{"n":1,"title","source","date","url"}]}],
  "risks": [{"text": "...", "sources": [ ... ]}],
  "overall": "...",
  "sources": [{"n":1,"title","source","date","url"}, ...],
  "status": "ok | no_data | disabled"
}
```

- `status`: 키 미설정 → `disabled`, 1개월 내 자료 없음 → `no_data`, 정상 → `ok`.

### 16.6 아키텍처 편입

- 신규 모듈:
  - `app/news_client.py` — 네이버 API + 크롤링 폴백 + 날짜 파싱/1개월 필터/중복 제거. `fetch_recent(company, stock_name, days=30) -> List[Item]`.
  - `app/insights.py` — `summarize(items, claude=None, as_of=None) -> dict`. Claude 호출자를 주입 가능(테스트 시 Fake 주입, 네트워크 없이 검증).
- `pipeline.run_analysis`에 **6단계**로 편입(진행률 total 5→6). 실패·비활성은 예외 없이 `status`로 흡수.
- `report.build_result`/`render_html`·프론트에 §8-6 섹션 렌더 추가.
- env: `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`. requirements에 `anthropic` 추가.

### 16.7 에러 처리 · 비용 · 캐시

| 상황 | 처리 |
|---|---|
| 네이버 키 없음/실패 | 크롤링 폴백 시도 → 그래도 실패 시 `status=disabled` |
| 1개월 내 자료 0건 | `status=no_data`, 섹션에 "최근 1개월 자료 없음" 표기 |
| Claude 키 없음/오류 | `status=disabled`, 수집 원문 목록만 출처로 노출(요약 생략) |
| 비용 | Claude 1회 호출/종목, 결과는 `(종목, 당일)` 캐시 |

### 16.8 면책 (강화)

이 섹션의 요약은 최근 1개월 공개 뉴스·블로그를 자동 정리한 것으로, **작성자·매체의 견해**이며 본 서비스의 투자 권유가 아니다. 사실관계·수치는 원문 출처로 반드시 교차 확인해야 하며, 투자 판단과 책임은 이용자 본인에게 있다.
