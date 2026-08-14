# FDR StockListing('KRX') 컬럼: Task4 Step1 라이브 스파이크는 컨트롤러 지시로 생략.
# (네트워크 호출 없이 진행 — pykrx/FDR 실제 응답을 확인하지 않음)
# 대신 컬럼명을 런타임에 동적으로 해석한다: Sector 또는 Industry, Code 또는 Symbol.
# 필요한 컬럼이 없으면 sector_of()는 None, peers_in_sector()는 [] 로 완만히 저하(degrade)한다.
from typing import List, Optional

from app import config


def _col(df, *candidates):
    """df.columns 중 candidates 를 순서대로 탐색해 실제로 존재하는 첫 컬럼명을 반환.
    없으면 None (호출부는 None 이면 완만히 저하해야 한다)."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _nearest_business_day():
    from pykrx import stock
    return stock.get_nearest_business_day_in_a_week()


class KrxClient:
    def __init__(self):
        self._marcap = None
        self._listing = None
        self._fund = None

    def _ensure(self):
        # 이미 주입된(테스트 등) 속성은 덮어쓰지 않는다 — None 인 항목만 채운다.
        if self._marcap is None or self._fund is None:
            from pykrx import stock
            d = _nearest_business_day()
            if self._marcap is None:
                self._marcap = stock.get_market_cap_by_ticker(d)
            if self._fund is None:
                self._fund = stock.get_market_fundamental_by_ticker(d)
        if self._listing is None:
            import FinanceDataReader as fdr
            self._listing = fdr.StockListing("KRX")

    def market_cap(self, stock_code):
        self._ensure()
        if self._marcap is None or stock_code not in self._marcap.index:
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
        if self._listing is None:
            return None
        code_col = _col(self._listing, "Code", "Symbol")
        sector_col = _col(self._listing, "Sector", "Industry")
        if code_col is None or sector_col is None:
            return None
        row = self._listing[self._listing[code_col] == stock_code]
        if len(row) == 0:
            return None
        val = row.iloc[0][sector_col]
        if val is None or (isinstance(val, float) and val != val):  # NaN
            return None
        return str(val)

    def peers_in_sector(self, sector, exclude_code, top=config.PEER_COUNT):
        self._ensure()
        if self._listing is None:
            return []
        code_col = _col(self._listing, "Code", "Symbol")
        name_col = _col(self._listing, "Name")
        sector_col = _col(self._listing, "Sector", "Industry")
        if code_col is None or sector_col is None:
            return []
        same = self._listing[self._listing[sector_col] == sector]
        rows = []
        for _, r in same.iterrows():
            code = str(r[code_col])
            if code == exclude_code:
                continue
            mc = self.market_cap(code)
            if mc is None:
                continue
            name = str(r[name_col]) if name_col is not None else code
            rows.append({"stock_code": code, "name": name, "market_cap": mc})
        rows.sort(key=lambda x: x["market_cap"], reverse=True)
        return rows[:top]
