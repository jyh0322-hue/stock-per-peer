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
NEWS_MAX_ITEMS = 25
NEWS_MAX_ITEMS_ALL = 40  # fetch_all(뉴스+투자의견+블로그 병합) 상한
REPRT = {"Q1": "11013", "HALF": "11012", "Q3": "11014", "ANNUAL": "11011"}
REPRT_ORDER = ["ANNUAL", "Q3", "HALF", "Q1"]  # 최신성 판단용(누적 범위 큰 순)
OP_ACCOUNT_IDS = ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"]
REVENUE_ACCOUNT_IDS = ["ifrs-full_Revenue"]
NET_INCOME_ACCOUNT_IDS = ["ifrs-full_ProfitLoss"]
# 손익계산서 주요 7개 항목 — account_id 기반 매칭(계정명은 회사/연도별로 다를 수 있음).
# stock_report.py(레거시 단독 스크립트)의 IS_ITEMS를 그대로 이식(실데이터 검증됨).
IS_ITEMS = [
    ("매출액", ["ifrs-full_Revenue"]),
    ("매출원가", ["ifrs-full_CostOfSales"]),
    ("매출총이익", ["ifrs-full_GrossProfit"]),
    ("판매관리비", ["dart_TotalSellingGeneralAdministrativeExpenses",
                  "ifrs-full_SellingGeneralAndAdministrativeExpense"]),
    ("영업이익", OP_ACCOUNT_IDS),
    ("세전이익", ["ifrs-full_ProfitLossBeforeTax"]),
    ("당기순이익", NET_INCOME_ACCOUNT_IDS),
]
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
FINSTATE_TTL = 60 * 60 * 24 * 30  # 30일
FINSTATE_EMPTY_TTL = 60 * 60  # 미제출/빈 결과는 1시간만 캐시(공시 게시 후 곧 반영)
LISTING_TTL = 60 * 60 * 12  # 12시간 (장중 시총 변동 반영하되 재조회 억제)


def env(name):
    return os.environ.get(name)


def api_key():
    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        raise RuntimeError("OPENDART_API_KEY 가 필요합니다. .env 파일 또는 환경변수로 설정하세요.")
    return key
