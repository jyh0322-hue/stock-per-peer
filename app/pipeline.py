from app import company, config, financials, metrics, report


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
            "is_target": is_target,
            # PER 산정방식 3종 — per_op(위)는 "최근분기×4 연환산"과 동일값이며 하위호환을 위해
            # 그대로 남긴다. per_op_fwd는 그 별칭. TTM(최근 4개 이산분기 합산) 기반은 아래 두 개.
            # peer는 지연시간 문제로 기본은 forward만 채운다(pipeline._apply_ttm_per 참고).
            "per_op_fwd": per, "per_op_ttm": None, "per_net_ttm": None, "ttm_complete": False}


def _build_deepdive(dart, krx, corp_code, target_code, basis):
    """회사개요/손익계산서/마진/5개년추이. 각 조각은 독립적으로 try/except로 감싸
    하나가 실패해도(개요 API 다운, 특정 연도 재무제표 결측 등) 나머지와 핵심 PER/PEER
    결과에는 영향이 없도록 한다."""
    deepdive = {"overview": None, "income_statement": None, "margins": None,
                "trend": [], "basis": basis}
    try:
        deepdive["overview"] = company.overview(dart, krx, corp_code, target_code)
    except Exception:
        deepdive["overview"] = None

    year, reprt_key, fs_div = basis.get("year"), basis.get("reprt_key"), basis.get("fs_div") or "CFS"
    if year and reprt_key:
        try:
            is_df = dart.finstate(corp_code, year, reprt_key, fs_div=fs_div)
            is_data = financials.income_statement(is_df, reprt_key) if is_df is not None else None
            deepdive["income_statement"] = is_data
            deepdive["margins"] = financials.margins(is_data) if is_data else None
        except Exception:
            deepdive["income_statement"] = None
            deepdive["margins"] = None
        try:
            deepdive["trend"] = financials.five_year_trend(dart, corp_code, year, reprt_key, fs_div=fs_div)
        except Exception:
            deepdive["trend"] = []
    return deepdive


def _apply_ttm_per(row, dart, corp_code, market_cap, fs_div):
    """TTM(최근 4개 이산분기 합산) 기준 영업이익 PER·순이익 PER을 row에 채운다.
    실패해도 row는 이미 forward PER을 갖고 있으므로 조용히 넘어간다."""
    try:
        ttm_op = financials.ttm_operating_income(dart, corp_code, fs_div=fs_div or "CFS")
        row["ttm_complete"] = ttm_op.get("complete", False)
        row["per_op_ttm"] = metrics.per_op(market_cap, ttm_op.get("op_ttm"))
    except Exception:
        pass
    try:
        net_ttm = financials.ttm_net_income(dart, corp_code, fs_div=fs_div or "CFS")
        row["per_net_ttm"] = metrics.per_op(market_cap, net_ttm)
    except Exception:
        pass


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
    TOTAL = 7
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

    # 5) 재무제표 심층분석(회사개요/손익계산서/마진/5개년추이) + TTM PER(영업이익·순이익)
    # peer까지 TTM(분기당 최대 4~8회 finstate 호출)을 계산하면 peer 5개 기준 지연이
    # 크게 늘어나(실측: 아래 참고) 응답시간이 나빠지므로, TTM은 타깃에게만 적용하고
    # peer는 기존 forward PER(최근분기×4 연환산)만 유지한다. 프런트가 방식 차이를
    # 알 수 있도록 all_rows 전체에 per_op_fwd/per_op_ttm/per_net_ttm/ttm_complete
    # 키는 채우되(타깃 외에는 ttm 쪽이 None), 최상위 결과에도 명시적 플래그를 남긴다.
    _emit(progress_cb, "재무제표 분석", 5, TOTAL)
    basis = {"year": tq.get("year"), "reprt_key": tq.get("reprt_key"), "fs_div": tq.get("fs_div")}
    deepdive = _build_deepdive(dart, krx, info["corp_code"], target_code, basis)
    _apply_ttm_per(target_row, dart, info["corp_code"], target_mc, basis.get("fs_div"))

    # 6) 타깃 공시(중요도/카테고리 분류 포함)
    _emit(progress_cb, "공시 수집", 6, TOTAL)
    disc = dart.recent_disclosures(info["corp_code"])

    # 7) 뉴스·블로그 투자포인트/리스크 요약(주입 없거나 실패해도 분석 자체는 성공)
    _emit(progress_cb, "뉴스·요약", 7, TOTAL)
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
    res = report.build_result(target_row, all_rows, stats, disc, deepdive=deepdive, insights=ins)
    # peer는 지연시간 문제로 TTM PER을 계산하지 않는다(위 5단계 주석 참고) — 프런트가
    # per_op_ttm 결측을 "데이터 없음"이 아니라 "미계산(방식 차이)"으로 표시할 수 있게 플래그.
    res["peer_ttm_computed"] = False
    return res
