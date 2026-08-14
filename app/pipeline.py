from app import config, metrics, report


def _emit(cb, step, cur, total):
    if cb:
        cb(step, cur, total)


def _metrics_for(stock_code, name, market_cap, op_3m, krx_per,
                  year=None, reprt_key=None, fs_div=None, is_target=False):
    opa = metrics.annualize(op_3m) if op_3m is not None else None
    per = metrics.per_op(market_cap, opa) if opa is not None else None
    if per is not None:
        per_status = "ok"
    elif op_3m is not None and op_3m <= 0:
        per_status = "loss"
    else:
        # op_3m 자체가 없거나(미제출/조회실패), market_cap 미상 등 — "적자"가 아니라 "결측"이다.
        per_status = "nodata"
    return {"name": name, "stock_code": stock_code, "market_cap": market_cap,
            "op_3m": op_3m, "op_annualized": opa, "per_op": per, "per_status": per_status,
            "krx_per": krx_per, "year": year, "reprt_key": reprt_key, "fs_div": fs_div,
            "is_target": is_target}


def _resolve_verified_peer(dart, stock_code):
    """OpenDartReader.find_corp_code는 corp_name 완전일치 시 .iloc[0]을 취해 상장 여부와
    무관하게 동명이인 중 아무 하나를 반환할 수 있다. 그래서 peer는 이름이 아니라
    stock_code(6자리)로 재조회하고, 되돌아온 stock_code가 요청한 것과 같은지 반드시
    검증한다. 불일치/조회 실패 시 None을 반환해, 호출부가 이 peer의 실적을
    "데이터 없음"으로 처리하게 한다 — 다른 회사의 영업이익을 시총과 섞어 쓰지 않는다."""
    try:
        pcorp = dart.resolve_corp(stock_code)
    except Exception:
        return None
    if pcorp.get("stock_code") != stock_code:
        return None
    return pcorp


def run_analysis(name, dart, krx, news=None, insights_fn=None, progress_cb=None):
    TOTAL = 6
    # 1) 종목 해석
    _emit(progress_cb, "종목 해석", 1, TOTAL)
    info = dart.resolve_corp(name)
    target_code = info["stock_code"]
    # 이름 검색도 동일한 동명이인 위험을 안고 있으므로, 되돌아온 stock_code로
    # 재조회해 같은 기업(corp_code)이 나오는지 왕복 검증한다.
    verify = dart.resolve_corp(target_code)
    if verify.get("corp_code") != info.get("corp_code") or verify.get("stock_code") != target_code:
        raise LookupError(
            "'%s' 종목을 정확히 식별하지 못했습니다(동일한 이름의 기업이 여러 건 존재합니다)." % name
        )

    # 2) 시총·업종·PEER 확정
    _emit(progress_cb, "업종·시총 조회", 2, TOTAL)
    sector = krx.sector_of(target_code)
    target_mc = krx.market_cap(target_code)
    peers_raw = krx.peers_in_sector(sector, exclude_code=target_code) if sector else []

    # 3) PEER 최근 분기 영업이익
    _emit(progress_cb, "PEER 실적 수집", 3, TOTAL)
    peer_rows = []
    for pr in peers_raw:
        op = year = reprt_key = fs_div = None
        pcorp = _resolve_verified_peer(dart, pr["stock_code"])
        if pcorp is not None:
            try:
                q = dart.latest_quarter_op(pcorp["corp_code"])
                op = q.get("op_3m")
                year = q.get("year")
                reprt_key = q.get("reprt_key")
                fs_div = q.get("fs_div")
            except Exception:
                op = year = reprt_key = fs_div = None
        peer_rows.append(_metrics_for(pr["stock_code"], pr["name"], pr["market_cap"],
                                      op, krx.krx_per(pr["stock_code"]),
                                      year=year, reprt_key=reprt_key, fs_div=fs_div))

    # 4) 타깃 PER·순위
    _emit(progress_cb, "PER 계산", 4, TOTAL)
    tq = dart.latest_quarter_op(info["corp_code"])
    target_row = _metrics_for(target_code, info["corp_name"], target_mc,
                              tq.get("op_3m"), krx.krx_per(target_code),
                              year=tq.get("year"), reprt_key=tq.get("reprt_key"),
                              fs_div=tq.get("fs_div"), is_target=True)
    all_rows = [target_row] + peer_rows
    pers = [r["per_op"] for r in all_rows]
    stats = metrics.peer_stats(pers)
    rank = metrics.rank_within(target_row["per_op"], pers)
    stats.update(rank)
    # 타깃 자신을 제외한 peer 중 PER이 계산된 개수가 2 미만이면 "업종 중앙값"이
    # 사실상 타깃 자기 자신과의 비교로 퇴화한다 — 순위/중앙값/저평가·고평가 판정을
    # 프런트에서 숨기도록 플래그만 남긴다(계산값 자체는 그대로 반환).
    peer_per_count = sum(1 for r in peer_rows if r["per_op"] is not None)
    stats["insufficient_peers"] = peer_per_count < 2

    # 5) 타깃 공시(+심층은 후속 확장 지점)
    _emit(progress_cb, "공시 수집", 5, TOTAL)
    disc = dart.recent_disclosures(info["corp_code"])

    # 6) 뉴스·블로그 투자포인트/리스크 요약(주입 없거나 실패해도 분석 자체는 성공)
    _emit(progress_cb, "뉴스·요약", 6, TOTAL)
    ins = {"status": "disabled", "investment_points": [], "risks": [],
           "overall": "", "sources": [], "as_of": None, "window_days": config.NEWS_WINDOW_DAYS}
    if news is not None and insights_fn is not None:
        try:
            krx_name = None
            try:
                krx_name = krx.name_of(target_code)
            except Exception:
                krx_name = None
            items = news.fetch_recent(info["corp_name"], krx_name or info["corp_name"],
                                      days=config.NEWS_WINDOW_DAYS)
            ins = insights_fn(items, info["corp_name"])
        except Exception:
            pass

    all_rows.sort(key=lambda r: (r["per_op"] is None, r["per_op"] if r["per_op"] is not None else 0))
    return report.build_result(target_row, all_rows, stats, disc, deepdive=None, insights=ins)
