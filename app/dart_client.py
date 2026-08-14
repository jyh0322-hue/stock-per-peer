import time
from datetime import date, timedelta

import pandas as pd

from app import cache, config, quarterly


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
        corp_name = info.get("corp_name", name_or_code)
        stock_code = info.get("stock_code", "")
        if not stock_code:
            # 비상장 법인(자회사 등) — 시총/PER 비교가 성립하지 않으므로 여기서 걸러낸다.
            raise LookupError("'%s'은(는) 상장 종목이 아닙니다." % corp_name)
        return {
            "corp_code": code,
            "corp_name": corp_name,
            "stock_code": stock_code,
            "induty_code": info.get("induty_code", ""),
        }

    def finstate(self, corp_code, year, reprt_key, fs_div="CFS"):
        key = "finstate:%s:%s:%s:%s" % (corp_code, year, reprt_key, fs_div)
        hit = cache.peek(key)
        if hit is not None:
            records = hit
        else:
            df_raw = retry(self.r.finstate_all, corp_code, year,
                           config.REPRT[reprt_key], fs_div=fs_div)
            records = [] if df_raw is None else df_raw.to_dict("records")
            ttl = config.FINSTATE_TTL if records else config.FINSTATE_EMPTY_TTL
            cache.put(key, records, ttl)
        if not records:
            return None
        return pd.DataFrame(records)

    def latest_quarter_op(self, corp_code, fs_div="CFS"):
        result = self._scan_quarters(corp_code, fs_div)
        if result["op_3m"] is None and fs_div == "CFS":
            # 코스닥 등 단일 법인(연결재무제표 미제출) 필터는 CFS만으로는 조회되지 않으므로
            # 별도(개별)재무제표로 한 번 더 시도한다.
            alt = self._scan_quarters(corp_code, "OFS")
            if alt["op_3m"] is not None:
                alt["fs_div"] = "OFS"
                return alt
        result["fs_div"] = fs_div
        return result

    def _scan_quarters(self, corp_code, fs_div):
        # 최신 보고서 탐색: 올해부터 과거로, config.REPRT_ORDER(최신성 큰 순: 사업보고서
        # ANNUAL이 3Q/반기/1Q보다 늦게 제출되지만 더 최신 분기를 담고 있음) 순서.
        latest_year = date.today().year
        for year in range(latest_year, latest_year - 2, -1):
            for reprt_key in config.REPRT_ORDER:
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
