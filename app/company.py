"""회사 개요(overview) — DART company() API + KRX(FDR KRX-DESC) 상장정보를 병합.
모든 항목은 조회 실패 시 None으로 저하하며, 이 모듈은 절대 예외를 던지지 않는다."""


def _fmt_yyyymmdd(s):
    """DART est_dt('19861107') -> '1986-11-07'. 형식이 아니면 원본 그대로(또는 None)."""
    if not s or not isinstance(s, str):
        return None
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
    return s


def overview(dart, krx, corp_code, stock_code):
    out = {
        "corp_name": None, "stock_code": stock_code, "market": None, "sector": None,
        "industry": None, "products": None, "ceo": None, "established": None,
        "homepage": None, "region": None, "listing_date": None,
    }

    dart_info = {}
    try:
        dart_info = dart.company_info(corp_code) or {}
    except Exception:
        dart_info = {}

    krx_info = {}
    try:
        krx_info = krx.profile(stock_code) or {}
    except Exception:
        krx_info = {}

    out["corp_name"] = dart_info.get("corp_name")
    out["market"] = krx_info.get("market")
    out["sector"] = krx_info.get("sector")
    out["industry"] = krx_info.get("industry")
    out["products"] = krx_info.get("products")
    out["ceo"] = dart_info.get("ceo_nm") or krx_info.get("representative")
    out["established"] = _fmt_yyyymmdd(dart_info.get("est_dt"))
    out["homepage"] = dart_info.get("hm_url") or krx_info.get("homepage")
    out["region"] = krx_info.get("region") or dart_info.get("adres")
    out["listing_date"] = krx_info.get("listing_date")
    return out
