import pandas as pd
from app.krx_client import KrxClient


def make_client():
    # marcap: FinanceDataReader StockListing("KRX") 형태 — Code + Marcap(원)
    marcap = pd.DataFrame({
        "Code": ["005930", "000660", "005380", "051910", "035420", "068270"],
        "Marcap": [1e12, 5e11, 2e11, 8e11, 3e11, 1e11],
    })
    # listing: FinanceDataReader StockListing("KRX-DESC") 형태 — Code/Name/Industry
    listing = pd.DataFrame({
        "Code": ["005930", "000660", "005380", "051910", "035420", "068270"],
        "Name": ["삼성전자", "SK하이닉스", "현대차", "LG화학", "NAVER", "셀트리온"],
        "Industry": ["반도체", "반도체", "자동차", "화학", "반도체", "바이오"],
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


def test_sector_of_prefers_industry_over_sector():
    # Sector(KRX 상장부/벤처기업부 등 실제 업종이 아닌 값)보다 Industry(실제 업종)를 우선해야 한다.
    listing = pd.DataFrame({
        "Code": ["005930"],
        "Name": ["삼성전자"],
        "Sector": ["벤처기업부"],
        "Industry": ["반도체 및 관련 부품 제조업"],
    })
    c = KrxClient()
    c._marcap = pd.DataFrame({"Code": ["005930"], "Marcap": [1e12]})
    c._listing = listing
    c._fund = pd.DataFrame({"PER": []})
    assert c.sector_of("005930") == "반도체 및 관련 부품 제조업"


def test_peers_in_sector_top_by_marcap_excludes_self():
    c = make_client()
    peers = c.peers_in_sector("반도체", exclude_code="005930", top=5)
    codes = [p["stock_code"] for p in peers]
    assert "005930" not in codes
    # 반도체 나머지: 000660(5e11), 035420(3e11) -> 시총 내림차순
    assert codes == ["000660", "035420"]


def test_krx_per():
    assert make_client().krx_per("000660") == 9.0


def test_krx_per_returns_none_when_fundamentals_source_raises(monkeypatch):
    # pykrx가 이 환경에서 깨져 있는 상황(LOGOUT/IndexError 등)을 시뮬레이션.
    # _fund를 주입하지 않은 상태에서 pykrx 호출이 예외를 던지면 krx_per는 raise하지 않고 None을 반환해야 한다.
    from pykrx import stock as pykrx_stock

    def boom(*args, **kwargs):
        raise IndexError("index -1 is out of bounds")

    monkeypatch.setattr(pykrx_stock, "get_nearest_business_day_in_a_week", boom)

    c = KrxClient()
    c._marcap = pd.DataFrame({"Code": ["005930"], "Marcap": [1e12]})
    c._listing = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Industry": ["반도체"]})
    # c._fund 는 주입하지 않는다 (None) -> _ensure 경로에서 pykrx 호출을 시도하다 실패해야 함
    assert c.krx_per("005930") is None
