from app import metrics, report


def _emit(cb, step, cur, total):
    if cb:
        cb(step, cur, total)


def _metrics_for(stock_code, name, market_cap, op_3m, krx_per, is_target=False):
    opa = metrics.annualize(op_3m) if op_3m is not None else None
    per = metrics.per_op(market_cap, opa) if opa is not None else None
    return {"name": name, "stock_code": stock_code, "market_cap": market_cap,
            "op_3m": op_3m, "op_annualized": opa, "per_op": per,
            "krx_per": krx_per, "is_target": is_target}


def run_analysis(name, dart, krx, progress_cb=None):
    TOTAL = 5
    # 1) 종목 해석
    _emit(progress_cb, "종목 해석", 1, TOTAL)
    info = dart.resolve_corp(name)
    target_code = info["stock_code"]

    # 2) 시총·업종·PEER 확정
    _emit(progress_cb, "업종·시총 조회", 2, TOTAL)
    sector = krx.sector_of(target_code)
    target_mc = krx.market_cap(target_code)
    peers_raw = krx.peers_in_sector(sector, exclude_code=target_code) if sector else []

    # 3) PEER 최근 분기 영업이익
    _emit(progress_cb, "PEER 실적 수집", 3, TOTAL)
    peer_rows = []
    for pr in peers_raw:
        try:
            pcorp = dart.resolve_corp(pr["name"])
            q = dart.latest_quarter_op(pcorp["corp_code"])
            op = q["op_3m"]
        except Exception:
            op = None
        peer_rows.append(_metrics_for(pr["stock_code"], pr["name"], pr["market_cap"],
                                      op, krx.krx_per(pr["stock_code"])))

    # 4) 타깃 PER·순위
    _emit(progress_cb, "PER 계산", 4, TOTAL)
    tq = dart.latest_quarter_op(info["corp_code"])
    target_row = _metrics_for(target_code, info["corp_name"], target_mc,
                              tq["op_3m"], krx.krx_per(target_code), is_target=True)
    all_rows = [target_row] + peer_rows
    pers = [r["per_op"] for r in all_rows]
    stats = metrics.peer_stats(pers)
    rank = metrics.rank_within(target_row["per_op"], pers)
    stats.update(rank)

    # 5) 타깃 공시(+심층은 후속 확장 지점)
    _emit(progress_cb, "공시·결과 조립", 5, TOTAL)
    disc = dart.recent_disclosures(info["corp_code"])

    all_rows.sort(key=lambda r: (r["per_op"] is None, r["per_op"] if r["per_op"] is not None else 0))
    return report.build_result(target_row, all_rows, stats, disc, deepdive=None)
