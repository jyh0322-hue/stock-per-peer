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
        return classify_disclosures(out)

    def company_info(self, corp_code):
        """DART company() 원자료 중 회사개요(app.company.overview)에 필요한 필드만 골라
        반환한다. corp_name/est_dt/ceo_nm/adres/hm_url/induty_code/corp_cls.
        resolve_corp()도 내부적으로 company()를 호출하므로 중복 API 호출을 줄이기 위해
        finstate와 동일한 캐시(30일 TTL — 회사 개요는 자주 바뀌지 않음)를 쓴다.
        어떤 실패든 raise하지 않고 빈 값(전부 None)을 반환한다."""
        key = "company_info:%s" % corp_code
        hit = cache.peek(key)
        if hit is not None:
            return hit
        try:
            info = retry(self.r.company, corp_code) or {}
        except Exception:
            info = {}
        result = {
            "corp_name": info.get("corp_name"),
            "est_dt": info.get("est_dt"),
            "ceo_nm": info.get("ceo_nm"),
            "adres": info.get("adres"),
            "hm_url": info.get("hm_url"),
            "induty_code": info.get("induty_code"),
            "corp_cls": info.get("corp_cls"),
        }
        cache.put(key, result, config.FINSTATE_TTL)
        return result


def _tag(title):
    if any(k in title for k in ["사업보고서", "반기보고서", "분기보고서"]):
        return "정기공시"
    if "주요사항" in title:
        return "주요사항"
    if any(k in title for k in ["증자", "감자", "사채", "발행"]):
        return "발행공시"
    return "기타"


# ── 공시 분류(중요도/카테고리) ────────────────────────────────────────────
# 제목 문자열 매칭만 사용(추가 API 호출 없음).
_HIGH_IMPORTANCE_KEYWORDS = [
    "유상증자", "무상증자", "전환사채", "신주인수권", "자기주식", "합병", "분할",
    "영업양수", "영업양도", "공급계약", "단일판매", "실적", "현금배당",
]


def _importance(title):
    return "high" if any(k in title for k in _HIGH_IMPORTANCE_KEYWORDS) else "normal"


def _category(title):
    if "실적" in title:
        return "실적"
    if any(k in title for k in ["유상증자", "무상증자", "전환사채", "신주인수권", "사채"]):
        return "증자·사채"
    if "자기주식" in title:
        return "자기주식"
    if any(k in title for k in ["공급계약", "단일판매", "수주"]):
        return "계약·수주"
    if "배당" in title:
        return "배당"
    if any(k in title for k in ["지분", "대량보유", "최대주주"]):
        return "지분변동"
    if any(k in title for k in ["사업보고서", "반기보고서", "분기보고서"]):
        return "정기공시"
    return "기타"


def classify_disclosures(items):
    """공시 목록(dict list, 최소 title/rcept_no 필요)에 importance/category/url을
    덧붙인다. 기존 필드(date/title/rcept_no/type)는 그대로 유지(하위호환)."""
    out = []
    for it in items:
        title = str(it.get("title", ""))
        rcept_no = str(it.get("rcept_no", ""))
        merged = dict(it)
        merged.setdefault("type", _tag(title))
        merged["importance"] = _importance(title)
        merged["category"] = _category(title)
        merged["url"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s" % rcept_no
        out.append(merged)
    return out
