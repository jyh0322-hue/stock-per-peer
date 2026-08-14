---
title: DART 종목 PER PEER 분석
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# DART 종목 PER·PEER 분석 웹앱

종목명을 검색하면 DART 재무·공시와 KRX 시세를 이용해 **연환산 영업이익 기준
PER**을 계산하고, 동일 업종 시가총액 상위 5개 PEER와 비교한 리포트를
비동기로 생성한다. 최근 1개월 뉴스·블로그를 수집해 Claude로 투자포인트·
리스크를 요약하는 기능도 포함한다(선택).

## 개요

- 백엔드: FastAPI (`app/main.py`) — 분석 요청을 백그라운드 잡으로 실행하고
  진행률(`/api/status/{job_id}`)과 결과(`/api/result/{job_id}`)를 폴링으로 제공.
- 프론트: 정적 HTML/CSS/JS (`web/`) — 검색창 → 진행바 → 결과(표·차트·투자
  포인트) 렌더.
- 데이터 소스: OpenDART(재무·공시), pykrx/FinanceDataReader(KRX 시세·업종),
  구글 뉴스 RSS + 네이버 뉴스 HTML 크롤링(뉴스), Anthropic Claude(요약, 선택).

### 6단계 분석 파이프라인 (`app/pipeline.py: run_analysis`)

| 단계 | 이름 | 내용 | 사용 모듈 |
|---|---|---|---|
| 1 | 종목 해석 | 종목명 → DART corp_code/종목코드 | `app/dart_client.py` |
| 2 | 업종·시총 조회 | KRX 업종 분류, 시가총액, 업종 내 시총 상위 PEER 후보 확정 | `app/krx_client.py` |
| 3 | PEER 실적 수집 | 각 PEER의 최근 분기 영업이익(DART) 수집 | `app/dart_client.py`, `app/quarterly.py` |
| 4 | PER 계산 | 연환산 영업이익, PER(영업이익 기준), 업종 통계·순위 산출 | `app/metrics.py` |
| 5 | 공시 수집 | 타깃 종목 최근 공시 목록 | `app/dart_client.py` |
| 6 | 뉴스·요약 | 최근 1개월 뉴스 수집 + Claude 투자포인트/리스크 요약(주입 실패해도 분석 자체는 성공) | `app/news_client.py`, `app/insights.py` |

결과는 `app/report.py`가 JSON(API 응답)과 PER 비교 차트(base64 PNG, 한글
폰트 포함), HTML 리포트로 정리한다.

### 모듈 요약

| 모듈 | 역할 |
|---|---|
| `app/main.py` | FastAPI 라우트, 비동기 잡 실행, 정적 파일 서빙 |
| `app/jobs.py` | 인메모리 잡 저장소(진행률/상태) |
| `app/pipeline.py` | 6단계 오케스트레이션 |
| `app/dart_client.py` | OpenDART 종목 해석, 재무, 최근 분기 영업이익, 공시 |
| `app/quarterly.py` | 분기 재무 추출/보정 로직 |
| `app/krx_client.py` | pykrx/FinanceDataReader 기반 시총·업종·PEER 조회 |
| `app/metrics.py` | 연환산, PER 계산, 업종 통계·순위 |
| `app/news_client.py` | 구글 뉴스 RSS + 네이버 뉴스 HTML 크롤링, 최근 N일 필터 |
| `app/insights.py` | Claude 호출 1회로 투자포인트/리스크 요약 + 출처 매핑 |
| `app/report.py` | 결과 JSON/HTML 조립, PER 비교 차트 렌더 |
| `app/cache.py` | 파일 기반 캐시(재무 결과 등, TTL 관리) |
| `app/config.py` | `.env` 로딩, 상수(PEER 개수, 계정과목 ID 등) |
| `web/` | 정적 프론트엔드(검색/진행바/결과 렌더) |

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # 값을 채운 뒤
uvicorn app.main:app --reload --port 8000
# http://localhost:8000
```

## 환경변수 / `.env`

프로젝트 루트의 `.env`를 `app/config.py`가 자동으로 로드한다(이미 설정된
환경변수는 덮어쓰지 않음). 로컬은 `.env`, 배포(HF Spaces)는 Space
**Settings → Secrets**로 동일한 코드가 동작한다. `.env`는 `.gitignore`에
포함되어 커밋되지 않는다. 시작하려면:

```bash
cp .env.example .env   # 값 채우기
```

| 변수 | 필수 여부 | 설명 |
|---|---|---|
| `OPENDART_API_KEY` | **필수** | 금융감독원 OpenDART API 키(40자리). 없으면 분석 자체가 실패한다. [발급: opendart.fss.or.kr](https://opendart.fss.or.kr) |
| `ANTHROPIC_API_KEY` | 선택 | 설정 시 6단계(뉴스 기반 투자포인트/리스크) 요약이 활성화된다. 미설정 시 해당 섹션은 `status: "disabled"`로 표시되고 나머지 분석(PER·PEER·공시)은 정상 동작한다. |

> **참고**: 이전에는 네이버 검색 API를 뉴스 수집에 사용했으나, 현재는 사용하지
> 않는다. 뉴스는 **구글 뉴스 RSS**(키 불필요)를 기본 소스로, **네이버 뉴스
> HTML 크롤링**을 보조 소스로 사용한다. 따라서 `NAVER_CLIENT_ID` /
> `NAVER_CLIENT_SECRET` 같은 네이버 API 키는 **발급받을 필요가 없다** —
> `.env.example`에서도 제거했다.

## 테스트

```bash
python3 -m pytest -v            # 네트워크 없는 단위/통합 테스트
RUN_LIVE=1 python3 -m pytest tests/test_smoke_live.py -v   # 실 API 스모크(OPENDART_API_KEY 필요, 선택)
```

`test_smoke_live.py`는 `RUN_LIVE=1`이 아니면 항상 SKIP된다. 실행 시
DART/KRX/뉴스/Claude(설정된 경우)까지 실제 네트워크를 태우는 전체 6단계
파이프라인을 돌리며, PER 핵심 필드만 엄격히 검증하고 뉴스/요약 파트는
`status`가 정의된 값 중 하나이기만 하면 통과하도록 관대하게 검증한다(외부
사이트 마크업 변경에 테스트가 흔들리지 않도록).

## Hugging Face Spaces 배포

1. Hugging Face에서 **New Space** 생성 → SDK: **Docker** 선택.
2. 이 저장소를 Space에 push한다(루트의 `Dockerfile` 포함).
3. Space **Settings → Repository secrets** 에 다음을 등록한다.
   - `OPENDART_API_KEY` (필수)
   - `ANTHROPIC_API_KEY` (선택 — 등록하지 않으면 투자포인트/리스크 섹션만 비활성)
4. 빌드가 끝나면 Space URL(포트 7860)로 접속한다.

Dockerfile은 `python:3.11-slim` 기반이며, 차트의 한글 라벨을 위해
`fonts-nanum`을 설치하고 `app/`, `web/`만 복사해 이미지를 가볍게 유지한다.

## GitHub Pages 자동 리포트

FastAPI 서버 없이도, GitHub Actions가 `scripts/build_site.py`로 정적 HTML
리포트를 생성해 GitHub Pages(`gh-pages` 브랜치)에 게시할 수 있다. 리포트는
실행할 때마다 `gh-pages` 브랜치의 기존 내용을 먼저 내려받은 뒤 새 리포트를
추가하므로 **누적**된다(이전 실행 결과가 지워지지 않는다).

> **주의**: 무료 GitHub Pages는 **공개(public) 저장소**에서만 동작한다.
> 비공개 저장소에서 쓰려면 GitHub Pro/Team/Enterprise 등 유료 플랜이 필요하다.

### 1) Pages 활성화

**Settings → Pages → Source**에서 **Deploy from a branch**를 선택하고,
Branch를 **`gh-pages` / `(root)`** 로 지정한다. (`gh-pages` 브랜치는 워크플로가
첫 실행될 때 자동으로 생성되므로, 최소 한 번 워크플로를 실행한 뒤 이 브랜치가
목록에 나타나면 선택하면 된다.)

### 2) 시크릿 등록

**Settings → Secrets and variables → Actions → Repository secrets**에 다음을
등록한다.

| 시크릿 | 필수 여부 | 설명 |
|---|---|---|
| `OPENDART_API_KEY` | **필수** | 없으면 리포트 생성 자체가 실패한다. |
| `ANTHROPIC_API_KEY` | 선택 | 없어도 나머지 분석(PER·PEER·공시)은 정상 생성되고, 투자포인트/리스크 섹션만 비활성으로 표시된다. |

`GITHUB_TOKEN`은 별도 등록이 필요 없다 — GitHub Actions가 실행마다 자동으로
발급하며, 워크플로의 `permissions: contents: write` 설정으로 `gh-pages`
브랜치에 push할 수 있는 권한만 부여한다.

### 3) 수동 실행

**Actions 탭 → "정적 리포트 생성 및 GitHub Pages 배포" → Run workflow**를
누르고, `stocks` 입력란에 쉼표로 구분한 종목명을 입력한다(예:
`브이티,코스맥스`). 비워두면 저장소 루트의 `watchlist.txt`에 있는 종목
목록을 사용한다.

### 4) 매일 자동 실행

`schedule` 트리거가 매일 08:00 KST(cron `0 23 * * *`, UTC 23:00)에 워크플로를
실행하며, 이때는 항상 `watchlist.txt`의 종목 목록을 사용한다. 감시 종목을
바꾸려면 `watchlist.txt`를 수정해서 커밋하면 된다.

### 5) 결과 확인

배포가 끝나면 다음 URL 형태로 리포트 목록에 접근할 수 있다.

```
https://<github-사용자명>.github.io/<저장소명>/
```

인덱스 페이지(`index.html`)는 지금까지 생성된 모든 리포트를 최신순으로
나열하며, 각 리포트(`<종목명>.html`)는 인라인 CSS/JS만으로 구성된
자기완결형(self-contained) 페이지라 외부 리소스 요청 없이 그대로 열람할 수
있다.

## PER 정의

```
PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4)
```

- 순이익 기준 **KRX 공식 PER**은 별도 컬럼(`krx_per`)으로 병기해 교차검증할
  수 있게 한다.
- 최근 분기 **영업이익이 적자**인 경우 PER(영업이익 기준)은 `N/A`로 표시한다
  (0 또는 음수로 나눈 값을 의미 있는 배수로 취급하지 않음).

## 면책

OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문이나 매매판단을 제공하지
않는다. 투자포인트·리스크 요약은 최근 1개월 공개 뉴스·블로그(작성자·매체의
견해 포함)를 자동 정리한 것으로, 본 서비스의 투자 권유가 아니다.
