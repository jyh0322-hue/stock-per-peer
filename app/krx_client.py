# pykrx가 이 환경의 KRX 세션 정책 변경으로 사용 불가(LOGOUT 응답, get_market_cap_by_ticker
# KeyError 등 — 라이브 스모크로 확인됨) 하므로 시가총액/업종 조회는 FinanceDataReader로 전환한다.
# pykrx는 fundamental(PER) 조회에서만 optional/best-effort로 남겨두되, 실패해도 절대
# raise하지 않고 None을 반환해 UI의 교차검증 컬럼이 N/A로만 표시되도록 한다.
from typing import List, Optional

from app import config


def _col(df, *candidates):
    """df.columns 중 candidates 를 순서대로 탐색해 실제로 존재하는 첫 컬럼명을 반환.
    없으면 None (호출부는 None 이면 완만히 저하해야 한다)."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _is_missing(val):
    return val is None or (isinstance(val, float) and val != val)  # None 또는 NaN


class KrxClient:
    def __init__(self):
        self._marcap = None   # FinanceDataReader StockListing("KRX") — 시총/종가 등
        self._listing = None  # FinanceDataReader StockListing("KRX-DESC") — 업종 등
        self._fund = None     # pykrx fundamental — optional, 실패 시 None 유지

    def _ensure(self):
        # 이미 주입된(테스트 등) 속성은 덮어쓰지 않는다 — None 인 항목만 채운다.
        if self._marcap is None:
            import FinanceDataReader as fdr
            self._marcap = fdr.StockListing("KRX")
        if self._listing is None:
            import FinanceDataReader as fdr
            self._listing = fdr.StockListing("KRX-DESC")

    def market_cap(self, stock_code):
        self._ensure()
        if self._marcap is None:
            return None
        code_col = _col(self._marcap, "Code", "Symbol")
        marcap_col = _col(self._marcap, "Marcap", "시가총액")
        if code_col is None or marcap_col is None:
            return None
        row = self._marcap[self._marcap[code_col] == stock_code]
        if len(row) == 0:
            return None
        val = row.iloc[0][marcap_col]
        if _is_missing(val):
            return None
        return float(val) / config.EOK

    def krx_per(self, stock_code):
        # pykrx fundamental은 이 환경에서 불안정/불가(LOGOUT 등) — best-effort만 하고
        # 어떤 실패든 절대 raise 하지 않는다. 이미 주입된 self._fund가 있으면 그것을 사용.
        try:
            if self._fund is None:
                from pykrx import stock
                d = stock.get_nearest_business_day_in_a_week()
                self._fund = stock.get_market_fundamental_by_ticker(d)
            if self._fund is None or stock_code not in self._fund.index:
                return None
            v = float(self._fund.loc[stock_code, "PER"])
            return v if v > 0 else None
        except Exception:
            return None

    def sector_of(self, stock_code):
        self._ensure()
        if self._listing is None:
            return None
        code_col = _col(self._listing, "Code", "Symbol")
        if code_col is None:
            return None
        row = self._listing[self._listing[code_col] == stock_code]
        if len(row) == 0:
            return None
        return self._resolve_sector(row.iloc[0])

    def _resolve_sector(self, row):
        # Industry(실제 업종 분류)를 우선하고, 없으면 Sector(KRX 상장부/벤처기업부 등
        # 실제 업종이 아닌 값일 때가 많음)로 완만히 저하한다.
        industry_col = _col(self._listing, "Industry")
        if industry_col is not None:
            val = row[industry_col]
            if not _is_missing(val):
                return str(val)
        sector_col = _col(self._listing, "Sector")
        if sector_col is not None:
            val = row[sector_col]
            if not _is_missing(val):
                return str(val)
        return None

    def peers_in_sector(self, sector, exclude_code, top=config.PEER_COUNT):
        self._ensure()
        if self._listing is None:
            return []
        code_col = _col(self._listing, "Code", "Symbol")
        name_col = _col(self._listing, "Name")
        if code_col is None:
            return []
        rows = []
        for _, r in self._listing.iterrows():
            code = str(r[code_col])
            if code == exclude_code:
                continue
            if self._resolve_sector(r) != sector:
                continue
            mc = self.market_cap(code)
            if mc is None:
                continue
            name = str(r[name_col]) if name_col is not None else code
            rows.append({"stock_code": code, "name": name, "market_cap": mc})
        rows.sort(key=lambda x: x["market_cap"], reverse=True)
        return rows[:top]
