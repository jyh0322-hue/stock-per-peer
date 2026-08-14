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
