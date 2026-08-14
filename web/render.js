// 결과(res) 렌더링 — 라이브 앱(web/app.js)과 정적 리포트(scripts/build_site.py가 생성하는
// HTML)가 함께 사용하는 순수 렌더 로직. DOM에 값을 "그려 넣는" 것 외의 상태(폴링, 진행바 등)는
// 다루지 않는다. 라이브 SPA 전용 동작(검색 화면으로 복귀)은 #view-search 존재 여부로
// 감지해서만 덧붙인다 — 정적 리포트에는 그 화면 자체가 없기 때문이다.
//
// 차트는 전부 인라인 SVG로 이 파일 안에서 생성한다(외부 라이브러리/CDN 없음 — 정적
// Pages 빌드가 자기완결형을 유지해야 한다). 색상은 CSS 커스텀 프로퍼티(--fg, --muted,
// --brand, --target, --good, --warn, --panel, --line, --series-1/2/3)를 통해 라이트/다크
// 테마를 함께 지원한다.
const $ = (s) => document.querySelector(s);

const REPRT_LABEL = { Q1: "1분기", HALF: "반기", Q3: "3분기", ANNUAL: "4분기" };
const FS_DIV_LABEL = { CFS: "연결", OFS: "별도" };

const fmt = (v, dp = 1) => (typeof v === "number" && !Number.isNaN(v)
  ? v.toLocaleString("ko-KR", { minimumFractionDigits: dp, maximumFractionDigits: dp })
  : "-");

const fmtSigned = (v, dp = 1) => (typeof v === "number" && !Number.isNaN(v)
  ? (v > 0 ? "+" : "") + fmt(v, dp)
  : "-");

function fmtMetric(m) {
  if (!m || typeof m.value !== "number" || Number.isNaN(m.value)) return "-";
  return fmtSigned(m.value, 1) + (m.unit || "");
}

// PER(영업이익 기준) 셀: 결측 사유(per_status)에 따라 "적자"와 "데이터 없음"을 구분해서
// 표기한다. per_status가 없는 값(적자 여부를 판단할 근거가 없는 경우)은 항상 "데이터 없음".
function perCell(v, status) {
  if (typeof v === "number" && !Number.isNaN(v)) return fmt(v, 1);
  return status === "loss" ? "N/A(적자)" : "데이터 없음";
}

// KRX PER 컬럼은 영업이익 데이터를 근거로 하지 않으므로(단순 시세 교차검증용) "적자"라고
// 단정할 근거가 없다 — 값이 없으면 항상 "데이터 없음"으로만 표기한다.
function krxPerCell(v) {
  return (typeof v === "number" && !Number.isNaN(v)) ? fmt(v, 1) : "데이터 없음";
}

function basisLabel(row) {
  if (!row || !row.year || !row.reprt_key) return "-";
  const q = REPRT_LABEL[row.reprt_key] || row.reprt_key;
  const suffix = row.fs_div === "OFS" ? "(별도)" : "";
  return row.year + " " + q + suffix;
}

function periodLabel(row) {
  if (!row || !row.year || !row.reprt_key) return "-";
  const q = REPRT_LABEL[row.reprt_key] || row.reprt_key;
  const fs = FS_DIV_LABEL[row.fs_div] || "연결";
  return row.year + " " + q + " · " + fs;
}

function perBadgeClass(v, median, insufficientPeers) {
  if (insufficientPeers) return "";
  if (typeof v !== "number" || typeof median !== "number") return "";
  if (v < median) return "badge-under";
  if (v > median * 1.3) return "badge-over";
  return "";
}

function fmtDate(d) {
  if (typeof d === "string" && d.length === 8 && /^\d{8}$/.test(d)) {
    return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6, 8);
  }
  return d || "-";
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 밸류에이션 level -> 색상 토큰/텍스트 클래스. deep_discount/discount(저평가)는 긍정,
// inline(업종 평균)은 중립, premium/high_premium(고평가)은 경계. "unavailable"은 중립 처리.
const LEVEL_TONE = {
  deep_discount: "good", discount: "good",
  inline: "neutral",
  premium: "warn", high_premium: "warn",
  unavailable: "neutral",
};

function levelTone(level) {
  return LEVEL_TONE[level] || "neutral";
}

// ---------------------------------------------------------------------------
// 공용 SVG 헬퍼
// ---------------------------------------------------------------------------

function chartEmpty(label) {
  return '<div class="chart-empty">' + escapeHtml(label || "데이터 없음") + "</div>";
}

function niceMax(v) {
  if (!(v > 0)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  for (const s of steps) {
    if (v <= s * mag) return s * mag;
  }
  return 10 * mag;
}

// ---------------------------------------------------------------------------
// 1) 헤더
// ---------------------------------------------------------------------------

function renderHeader(t, overview) {
  const sector = overview && (overview.sector || overview.industry);
  const sectorChip = sector
    ? '<span class="sector-chip">업종 · ' + escapeHtml(sector) + "</span>" : "";
  return (
    '<div class="result-head">' +
      "<h1>" + escapeHtml(t.name) + '</h1><span class="code">(' + escapeHtml(t.stock_code || "") + ")</span>" +
      sectorChip +
      '<span class="basis-chip">' + escapeHtml(periodLabel(t)) + "</span>" +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// 2) 밸류에이션 카드(hero) + 위치 게이지
// ---------------------------------------------------------------------------

function svgGauge(min, max, median, target, tone) {
  const vals = [min, max, median, target];
  if (vals.some((v) => typeof v !== "number" || Number.isNaN(v)) || !(max > min)) {
    return chartEmpty("업종 비교 데이터가 부족해 위치를 표시할 수 없습니다.");
  }
  const W = 640, H = 96, padL = 46, padR = 46, trackY = 44, trackH = 10;
  const innerW = W - padL - padR;
  const clamp = (v) => Math.min(Math.max(v, min), max);
  const x = (v) => padL + ((clamp(v) - min) / (max - min)) * innerW;
  const dotColor = "var(--" + tone + ")";
  const tx = x(target), mx = x(median);
  // 라벨 겹침 방지: 타깃/중앙값 라벨이 너무 가까우면 세로로 살짝 어긋나게 배치.
  const closeLabels = Math.abs(tx - mx) < 70;
  return (
    '<svg class="gauge-svg" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" preserveAspectRatio="xMidYMid meet">' +
      "<title>업종 PEER PER 범위(최소~최대) 대비 타깃 위치</title>" +
      '<rect x="' + padL + '" y="' + trackY + '" width="' + innerW + '" height="' + trackH +
        '" rx="5" fill="var(--line)"></rect>' +
      '<line x1="' + mx + '" y1="' + (trackY - 9) + '" x2="' + mx + '" y2="' + (trackY + trackH + 9) +
        '" stroke="var(--muted)" stroke-width="2"></line>' +
      '<text x="' + mx + '" y="' + (trackY - 14) + '" text-anchor="middle" class="gauge-lab muted">중앙값 ' +
        fmt(median) + "배</text>" +
      '<circle cx="' + tx + '" cy="' + (trackY + trackH / 2) + '" r="8" fill="' + dotColor +
        '" stroke="var(--panel)" stroke-width="2"></circle>' +
      '<text x="' + tx + '" y="' + (trackY + trackH + (closeLabels ? 34 : 24)) +
        '" text-anchor="middle" class="gauge-lab gauge-target" fill="' + dotColor + '">타깃 ' + fmt(target) + "배</text>" +
      '<text x="' + padL + '" y="' + (trackY + trackH + 24) + '" text-anchor="start" class="gauge-lab muted">최소 ' +
        fmt(min) + "배</text>" +
      '<text x="' + (W - padR) + '" y="' + (trackY + trackH + 24) + '" text-anchor="end" class="gauge-lab muted">최대 ' +
        fmt(max) + "배</text>" +
    "</svg>"
  );
}

function renderValuationCard(valuation, stats, targetPer) {
  if (!valuation) return "";
  const tone = levelTone(valuation.level);
  const rankLine = (valuation.rank && valuation.total)
    ? ('<span class="val-rank-n">' + valuation.rank + "</span> / " + valuation.total + " (PER 낮을수록 상위)")
    : "비교 가능한 동종업체 부족";
  const discountLine = (typeof valuation.discount_pct === "number")
    ? ('업종 중앙값 대비 <b>' + fmtSigned(valuation.discount_pct, 1) + "%</b>")
    : "";
  const gauge = svgGauge(stats && stats.min, stats && stats.max, valuation.median, targetPer, tone);
  const caveats = (valuation.caveats || []).map((c) =>
    "<li>" + escapeHtml(c) + "</li>").join("");
  return (
    '<div class="card hero-card tone-' + tone + '">' +
      '<div class="hero-top">' +
        '<div class="hero-label tone-' + tone + '">' + escapeHtml(valuation.label || "-") + "</div>" +
        (discountLine ? '<div class="hero-discount">' + discountLine + "</div>" : "") +
      "</div>" +
      '<div class="hero-note">' + escapeHtml(valuation.note || "") + "</div>" +
      '<div class="hero-rank">업종 내 순위 ' + rankLine + "</div>" +
      '<div class="gauge-wrap">' + gauge + "</div>" +
      (caveats ? '<ul class="hero-caveats">' + caveats + "</ul>" : "") +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// 3) KPI 행
// ---------------------------------------------------------------------------

function kpiTile(label, valueHtml, note, extraClass) {
  return (
    '<div class="kpi' + (extraClass ? " " + extraClass : "") + '">' +
      '<div class="lab">' + escapeHtml(label) + "</div>" +
      '<div class="val">' + valueHtml + "</div>" +
      (note ? '<div class="note muted">' + note + "</div>" : "") +
    "</div>"
  );
}

function renderKpiRow(t) {
  const ttmNote = t.ttm_complete ? "" : "최근 4개 분기 미확보 · 연환산 추정";
  const tiles = [
    kpiTile("시가총액", fmt(t.market_cap, 0) + '<small> 억</small>'),
    kpiTile("최근 분기 영업이익", fmt(t.op_3m, 0) + '<small> 억</small>', "기준 " + escapeHtml(basisLabel(t))),
    kpiTile("PER (연환산)", perCell(t.per_op_fwd != null ? t.per_op_fwd : t.per_op, t.per_status) + '<small> 배</small>',
      "최근분기×4"),
    kpiTile("PER (TTM)", perCell(t.per_op_ttm, t.per_status) + '<small> 배</small>',
      ttmNote || "최근 4개 분기 합산", ttmNote ? "kpi-warn" : ""),
    kpiTile("PER (순이익 · TTM)", perCell(t.per_net_ttm, t.per_status) + '<small> 배</small>',
      ttmNote || "당기순이익 기준", ttmNote ? "kpi-warn" : ""),
  ].join("");
  return '<div class="kpi-row">' + tiles + "</div>";
}

// ---------------------------------------------------------------------------
// 4) 재무 인사이트
// ---------------------------------------------------------------------------

const SEVERITY_ICON = { positive: "▲", negative: "▼", neutral: "●" };
const SEVERITY_TONE = { positive: "good", negative: "warn", neutral: "neutral" };

function renderFinancialInsight(fi) {
  if (!fi || (!fi.headline && !(fi.findings || []).length)) {
    return '<div class="card fin-empty">재무 인사이트를 계산할 재무제표 데이터가 부족합니다.</div>';
  }
  const items = (fi.findings || []).map((f) => {
    const tone = SEVERITY_TONE[f.severity] || "neutral";
    const icon = SEVERITY_ICON[f.severity] || "●";
    const metric = f.metric
      ? '<span class="fin-metric tone-' + tone + '">' + escapeHtml(f.metric.label) + " " +
        escapeHtml(fmtMetric(f.metric)) + "</span>"
      : "";
    return (
      '<li class="fin-item tone-' + tone + '">' +
        '<span class="fin-ic tone-' + tone + '">' + icon + "</span>" +
        '<span class="fin-text">' + escapeHtml(f.text) + "</span>" +
        metric +
      "</li>"
    );
  }).join("");
  return (
    '<div class="card fin-card">' +
      (fi.headline ? '<div class="fin-headline">' + escapeHtml(fi.headline) + "</div>" : "") +
      '<ul class="fin-list">' + (items || "<li>해당 없음</li>") + "</ul>" +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// 5) 손익계산서 + 마진 + 비용구조 차트
// ---------------------------------------------------------------------------

const IS_ROWS = ["매출액", "매출원가", "매출총이익", "판매관리비", "영업이익", "세전이익", "당기순이익"];
// 비용 성격 항목(매출원가/판매관리비)은 증가가 불리한 신호이므로 색상 방향을 반전한다 —
// 그 외(매출액/이익 계열)는 증가가 유리한 신호이므로 그대로 둔다.
const IS_COST_ROWS = { 매출원가: true, 판매관리비: true };

function yoy(cur, prev) {
  if (typeof cur !== "number" || typeof prev !== "number" || prev === 0) return null;
  return (cur - prev) / Math.abs(prev) * 100;
}

function renderIncomeStatement(incomeStatement, margins) {
  if (!incomeStatement) {
    return '<div class="card fin-empty">손익계산서 데이터가 없습니다.</div>';
  }
  const rows = IS_ROWS.map((label) => {
    const row = incomeStatement[label] || {};
    const cur = row.cur_cum, prev = row.prev_cum;
    const chg = yoy(cur, prev);
    const favorable = IS_COST_ROWS[label] ? (typeof chg === "number" ? -chg : null) : chg;
    const chgCls = typeof favorable === "number" ? (favorable > 0 ? "pos" : favorable < 0 ? "neg" : "") : "";
    return (
      "<tr><td>" + escapeHtml(label) + '</td><td class="num">' + fmt(cur, 1) +
      '</td><td class="num">' + fmt(prev, 1) + '</td><td class="num ' + chgCls + '">' +
      (typeof chg === "number" ? fmtSigned(chg, 1) + "%" : "-") + "</td></tr>"
    );
  }).join("");

  const stmtTable =
    '<div class="card table-scroll"><table class="stmt-table"><thead><tr>' +
      "<th>항목 (억원)</th><th class=\"num\">당기 누적</th><th class=\"num\">전년동기 누적</th><th class=\"num\">YoY</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>";

  let marginTable = "";
  if (margins) {
    const mrows = [
      ["매출총이익률", margins.gross_margin, false],
      ["영업이익률", margins.op_margin, false],
      ["순이익률", margins.net_margin, false],
      ["판관비율", margins.sga_ratio, true], // 비용 비율 — 상승이 불리하므로 색상 반전
    ].map(([label, m, invert]) => {
      m = m || {};
      const favorable = typeof m.delta === "number" ? (invert ? -m.delta : m.delta) : null;
      const dCls = typeof favorable === "number" ? (favorable > 0 ? "pos" : favorable < 0 ? "neg" : "") : "";
      return (
        "<tr><td>" + label + '</td><td class="num">' + fmt(m.cur, 1) + "%</td><td class=\"num\">" +
        fmt(m.prev, 1) + '%</td><td class="num ' + dCls + '">' +
        (typeof m.delta === "number" ? fmtSigned(m.delta, 1) + "%p" : "-") + "</td></tr>"
      );
    }).join("");
    marginTable =
      '<div class="card table-scroll" style="margin-top:10px"><table class="stmt-table"><thead><tr>' +
        "<th>수익성 지표</th><th class=\"num\">당기</th><th class=\"num\">전년동기</th><th class=\"num\">%p 변화</th>" +
      "</tr></thead><tbody>" + mrows + "</tbody></table></div>";
  }

  return stmtTable + marginTable;
}

function costShare(margins) {
  if (!margins) return null;
  const gm = margins.gross_margin, om = margins.op_margin, sg = margins.sga_ratio;
  if (!gm || !om || !sg) return null;
  if ([gm.cur, gm.prev, om.cur, om.prev, sg.cur, sg.prev].some((v) => typeof v !== "number")) return null;
  return {
    cur: { cogs: 100 - gm.cur, sga: sg.cur, op: om.cur },
    prev: { cogs: 100 - gm.prev, sga: sg.prev, op: om.prev },
  };
}

function stackedSegment(xStart, width, height, y, fill, label, valueLabel, minLabelWidth) {
  const gap = width > 4 ? 1 : 0; // 2px 표면 갭(양쪽 1px씩)의 근사
  const w = Math.max(width - gap * 2, 0);
  const x = xStart + gap;
  const fits = w >= minLabelWidth;
  const textFill = "#fff";
  const text = fits
    ? '<text x="' + (x + w / 2) + '" y="' + (y + height / 2 + 4) +
      '" text-anchor="middle" class="seg-label" fill="' + textFill + '">' + escapeHtml(valueLabel) + "</text>"
    : "";
  return (
    '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + height + '" fill="' + fill +
      '"><title>' + escapeHtml(label) + " " + escapeHtml(valueLabel) + "</title></rect>" + text
  );
}

function svgCostStructure(share) {
  if (!share) return chartEmpty("비용구조를 계산할 마진 데이터가 부족합니다.");
  const W = 640, barH = 30, rowGap = 46, padL = 92, padR = 16, top = 14;
  const innerW = W - padL - padR;
  const H = top + rowGap * 2 + barH + 30;
  const rows = [
    ["당기", share.cur],
    ["전년동기", share.prev],
  ];
  const segs = [
    { key: "cogs", label: "매출원가", color: "var(--series-1)" },
    { key: "sga", label: "판관비", color: "var(--series-2)" },
    { key: "op", label: "영업이익", color: "var(--series-3)" },
  ];
  let body = "";
  rows.forEach(([rowLabel, vals], i) => {
    const y = top + i * rowGap;
    body += '<text x="0" y="' + (y + barH / 2 + 4) + '" class="stack-row-label">' + escapeHtml(rowLabel) + "</text>";
    let cursor = padL;
    segs.forEach((s) => {
      const v = Math.max(vals[s.key] || 0, 0);
      const w = (v / 100) * innerW;
      body += stackedSegment(cursor, w, barH, y, s.color, s.label, fmt(v, 1) + "%", 34);
      cursor += w;
    });
  });
  const legend = segs.map((s) =>
    '<span class="legend-item"><span class="legend-dot" style="background:' + s.color + '"></span>' +
    escapeHtml(s.label) + "</span>").join("");
  return (
    '<div class="legend-row">' + legend + "</div>" +
    '<svg class="cost-svg" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img">' +
      "<title>매출액 대비 비용구조(매출원가·판관비·영업이익 비중) — 당기 vs 전년동기</title>" +
      body +
    "</svg>"
  );
}

// ---------------------------------------------------------------------------
// 6) 5개년 추이(콤보: 매출·영업이익 막대 + 영업이익률 라인, 축 정렬된 2단 패널)
// ---------------------------------------------------------------------------

function svgTrendChart(trend) {
  const pts = (trend || []).filter((t) => t && typeof t.year === "number")
    .sort((a, b) => a.year - b.year);
  if (pts.length < 2) return chartEmpty("5개년 추이를 표시할 데이터가 부족합니다.");

  const W = 680;
  const padL = 54, padR = 16;
  const innerW = W - padL - padR;
  const n = pts.length;
  const slot = innerW / n;
  const barW = Math.min(20, slot * 0.32);

  // 상단 패널: 매출/영업이익 그룹 막대 (동일 단위 '억원' 공유 스케일)
  const topH = 130, topPadTop = 10, topBase = topPadTop + topH;
  const revMax = Math.max.apply(null, pts.map((p) => p.revenue || 0));
  const opMax = Math.max.apply(null, pts.map((p) => p.operating_income || 0));
  const sharedMax = niceMax(Math.max(revMax, opMax, 1));

  // 하단 패널: 영업이익률(%) 라인
  const botTop = topBase + 46, botH = 90, botBase = botTop + botH;
  const marginVals = pts.map((p) => (typeof p.op_margin === "number" ? p.op_margin : null));
  const marginKnown = marginVals.filter((v) => v !== null);
  const marginMax = niceMax(Math.max.apply(null, marginKnown.concat([1])) * 1.15);

  const H = botBase + 34;

  const xCenter = (i) => padL + slot * i + slot / 2;
  const yTop = (v) => topBase - (Math.max(v, 0) / sharedMax) * topH;
  const yBot = (v) => botBase - (Math.max(v, 0) / marginMax) * botH;

  // 상단 그리드(0, sharedMax) — hairline
  let gridTop = "";
  [0, 0.5, 1].forEach((f) => {
    const gy = topBase - f * topH;
    gridTop += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
      '" class="grid-line"></line>';
    gridTop += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" text-anchor="end" class="axis-lab">' +
      fmt(sharedMax * f, 0) + "</text>";
  });

  let bars = "";
  pts.forEach((p, i) => {
    const cx = xCenter(i);
    const revV = p.revenue || 0, opV = p.operating_income || 0;
    const rx = cx - barW - 1, ox = cx + 1;
    const ry = yTop(revV), oy = yTop(opV);
    bars +=
      '<rect x="' + rx + '" y="' + ry + '" width="' + barW + '" height="' + (topBase - ry) +
        '" rx="3" fill="var(--series-1)"><title>매출 ' + p.year + "년 " + fmt(revV, 1) + "억</title></rect>" +
      '<rect x="' + ox + '" y="' + oy + '" width="' + barW + '" height="' + (topBase - oy) +
        '" rx="3" fill="var(--series-2)"><title>영업이익 ' + p.year + "년 " + fmt(opV, 1) + "억</title></rect>";
    // 마지막 해만 값 직접 라벨(끝점 강조) — 나머지는 툴팁/범례로.
    if (i === n - 1) {
      bars += '<text x="' + rx + '" y="' + (ry - 6) + '" text-anchor="middle" class="bar-end-lab">' +
        fmt(revV, 0) + "</text>";
      bars += '<text x="' + ox + '" y="' + (oy - 6) + '" text-anchor="middle" class="bar-end-lab">' +
        fmt(opV, 0) + "</text>";
    }
  });

  // 하단 그리드
  let gridBot = "";
  [0, 0.5, 1].forEach((f) => {
    const gy = botBase - f * botH;
    gridBot += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
      '" class="grid-line"></line>';
    gridBot += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" text-anchor="end" class="axis-lab">' +
      fmt(marginMax * f, 0) + "%</text>";
  });

  let linePath = "", dots = "", first = true;
  pts.forEach((p, i) => {
    if (p.op_margin == null) { first = true; return; }
    const cx = xCenter(i), cy = yBot(p.op_margin);
    linePath += (first ? "M" : "L") + cx + " " + cy + " ";
    first = false;
    const isEnd = (i === n - 1) || (i === 0);
    dots += '<circle cx="' + cx + '" cy="' + cy + '" r="4.5" fill="var(--brand)" stroke="var(--panel)" ' +
      'stroke-width="2"><title>영업이익률 ' + p.year + "년 " + fmt(p.op_margin, 1) + "%</title></circle>";
    if (isEnd) {
      dots += '<text x="' + cx + '" y="' + (cy - 10) + '" text-anchor="middle" class="line-end-lab">' +
        fmt(p.op_margin, 1) + "%</text>";
    }
  });

  let xLabels = "";
  pts.forEach((p, i) => {
    xLabels += '<text x="' + xCenter(i) + '" y="' + (botBase + 22) + '" text-anchor="middle" class="axis-lab">' +
      p.year + "</text>";
  });

  const legend =
    '<div class="legend-row">' +
      '<span class="legend-item"><span class="legend-dot" style="background:var(--series-1)"></span>매출(억원)</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:var(--series-2)"></span>영업이익(억원)</span>' +
      '<span class="legend-item"><span class="legend-dot" style="background:var(--brand);border-radius:50%"></span>영업이익률(%, 하단)</span>' +
    "</div>";

  return (
    legend +
    '<svg class="trend-svg" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img">' +
      "<title>5개년 매출·영업이익 추이 및 영업이익률 변화</title>" +
      gridTop + bars +
      '<line x1="' + padL + '" y1="' + topBase + '" x2="' + (W - padR) + '" y2="' + topBase +
        '" class="axis-line"></line>' +
      gridBot +
      '<path d="' + linePath.trim() + '" fill="none" stroke="var(--brand)" stroke-width="2" ' +
        'stroke-linejoin="round" stroke-linecap="round"></path>' +
      dots +
      '<line x1="' + padL + '" y1="' + botBase + '" x2="' + (W - padR) + '" y2="' + botBase +
        '" class="axis-line"></line>' +
      xLabels +
    "</svg>"
  );
}

// ---------------------------------------------------------------------------
// 7) PEER 비교 (표 + 네이티브 SVG 막대 차트)
// ---------------------------------------------------------------------------

function svgPeerChart(peers, median) {
  const rows = (peers || []).map((p) => ({
    name: p.name, isTarget: !!p.is_target,
    v: typeof p.per_op === "number" ? p.per_op : null,
    status: p.per_status,
  }));
  const valid = rows.map((r) => r.v).filter((v) => typeof v === "number");
  if (!valid.length) return chartEmpty("PER 데이터가 있는 PEER가 없습니다.");
  const maxV = niceMax(Math.max.apply(null, valid.concat(typeof median === "number" ? [median] : [])) * 1.12);

  const rowH = 30, gap = 8, padL = 132, padR = 56, top = 10;
  const innerW = 640 - padL - padR;
  const H = top + rows.length * (rowH + gap) + (typeof median === "number" ? 26 : 6);
  const x = (v) => (Math.max(v, 0) / maxV) * innerW;

  let body = "";
  rows.forEach((r, i) => {
    const y = top + i * (rowH + gap);
    const barColor = r.isTarget ? "var(--target)" : "var(--brand)";
    const nameCls = r.isTarget ? "peer-name target" : "peer-name";
    body += '<text x="' + (padL - 10) + '" y="' + (y + rowH / 2 + 4) + '" text-anchor="end" class="' + nameCls + '">' +
      (r.isTarget ? "★ " : "") + escapeHtml(r.name) + "</text>";
    if (typeof r.v === "number") {
      const w = x(r.v);
      body += '<rect x="' + padL + '" y="' + y + '" width="' + Math.max(w, 2) + '" height="' + rowH +
        '" rx="4" fill="' + barColor + '"><title>' + escapeHtml(r.name) + " PER " + fmt(r.v) + "배</title></rect>";
      body += '<text x="' + (padL + w + 8) + '" y="' + (y + rowH / 2 + 4) + '" class="peer-val">' +
        fmt(r.v) + "</text>";
    } else {
      body += '<rect x="' + padL + '" y="' + y + '" width="2" height="' + rowH +
        '" fill="var(--line)"></rect>';
      body += '<text x="' + (padL + 10) + '" y="' + (y + rowH / 2 + 4) + '" class="peer-val muted">' +
        (r.status === "loss" ? "N/A(적자)" : "데이터 없음") + "</text>";
    }
  });

  let medianLine = "";
  if (typeof median === "number") {
    const mx = padL + x(median);
    const my = top + rows.length * (rowH + gap);
    medianLine =
      '<line x1="' + mx + '" y1="0" x2="' + mx + '" y2="' + my + '" class="median-line"></line>' +
      '<text x="' + mx + '" y="' + (my + 16) + '" text-anchor="middle" class="axis-lab">업종 중앙값 ' +
        fmt(median) + "배</text>";
  }

  return (
    '<svg class="peer-svg" viewBox="0 0 640 ' + H + '" width="100%" role="img">' +
      "<title>업종 PEER PER(연환산) 비교, 타깃 강조</title>" +
      body + medianLine +
    "</svg>"
  );
}

// ---------------------------------------------------------------------------
// 8) 최근 공시
// ---------------------------------------------------------------------------

const DISC_TYPE_CLASS = { 정기공시: "reg", 주요사항: "major", 발행공시: "issue" };

function renderDisclosures(disclosures) {
  const items = (disclosures || []).slice(0, 20).map((d) => {
    const cls = DISC_TYPE_CLASS[d.type] || "etc";
    const highCls = d.importance === "high" ? " high" : "";
    const cat = d.category ? '<span class="cat-tag">' + escapeHtml(d.category) + "</span>" : "";
    const titleHtml = d.url
      ? '<a href="' + escapeHtml(d.url) + '" target="_blank" rel="noopener">' + escapeHtml(d.title) + "</a>"
      : escapeHtml(d.title);
    return (
      '<li class="' + cls + highCls + '"><span class="d">' + fmtDate(d.date) + '</span>' +
      '<span class="t">' + titleHtml + "</span>" + cat +
      '<span class="type ' + cls + '">' + escapeHtml(d.type) + "</span></li>"
    );
  }).join("");
  return '<ul class="disc">' + (items || "<li>최근 공시 없음</li>") + "</ul>";
}

// ---------------------------------------------------------------------------
// 9) 산업 동향
// ---------------------------------------------------------------------------

function renderIndustry(industry) {
  if (!industry || (!(industry.items || []).length && !(industry.terms || []).length)) {
    return '<div class="card fin-empty">수집된 산업 동향이 없습니다.</div>';
  }
  const terms = (industry.terms || []).map((t) =>
    '<span class="tagpill static">' + escapeHtml(t) + "</span>").join("");
  const summary = industry.summary;
  let summaryBlock = "";
  if (summary && summary.status === "ok") {
    const points = (summary.points || []).map((p) => {
      const sup = (p.sources || []).map((s) => "[" + s.n + "]").join("");
      return "<li>" + escapeHtml(p.text) + (sup ? ' <sup class="src-ref">' + escapeHtml(sup) + "</sup>" : "") + "</li>";
    }).join("");
    summaryBlock =
      '<div class="card industry-summary">' +
        (summary.summary ? "<p>" + escapeHtml(summary.summary) + "</p>" : "") +
        (points ? '<ul>' + points + "</ul>" : "") +
      "</div>";
  }
  const items = (industry.items || []).slice(0, 10).map((it) =>
    '<li><span class="d">' + escapeHtml(it.date || "-") + '</span>' +
    '<span class="t"><a href="' + escapeHtml(it.url || "#") + '" target="_blank" rel="noopener">' +
      escapeHtml(it.title) + "</a></span>" +
    '<span class="src-tag">' + escapeHtml(it.source || "") + "</span></li>"
  ).join("");
  return (
    (terms ? '<div class="terms-row">검색어: ' + terms + "</div>" : "") +
    summaryBlock +
    '<ul class="disc industry-list">' + (items || "<li>관련 산업 동향 기사가 없습니다.</li>") + "</ul>"
  );
}

// ---------------------------------------------------------------------------
// 10) 뉴스·의견
// ---------------------------------------------------------------------------

function insightItem(p) {
  const sup = (p.sources || []).map((s) => "[" + s.n + "]").join("");
  const supHtml = sup ? ' <sup class="src-ref">' + escapeHtml(sup) + "</sup>" : "";
  return "<li>" + escapeHtml(p.text) + supHtml + "</li>";
}

const KIND_LABEL = { news: "📰 뉴스", opinion: "💬 투자의견", blog: "✍️ 블로그" };
const KIND_ORDER = ["news", "opinion", "blog"];

function renderSourcesGrouped(sources) {
  const groups = {};
  (sources || []).forEach((s) => {
    const k = s.kind || "news";
    (groups[k] = groups[k] || []).push(s);
  });
  const keys = KIND_ORDER.filter((k) => groups[k] && groups[k].length)
    .concat(Object.keys(groups).filter((k) => KIND_ORDER.indexOf(k) === -1));
  if (!keys.length) return '<div class="fin-empty">최근 수집된 뉴스·의견 자료가 없습니다.</div>';
  return keys.map((k) => {
    const rows = groups[k].map((s) =>
      '<li><span class="d">' + escapeHtml(s.date || "-") + '</span>' +
      '<span class="t"><a href="' + escapeHtml(s.url || "#") + '" target="_blank" rel="noopener">' +
        escapeHtml(s.title) + "</a></span>" +
      '<span class="src-tag">' + escapeHtml(s.source || "") + "</span></li>"
    ).join("");
    return (
      '<div class="src-group">' +
        '<div class="src-group-title">' + (KIND_LABEL[k] || escapeHtml(k)) +
          ' <span class="hint">' + groups[k].length + "건</span></div>" +
        '<ul class="disc">' + rows + "</ul>" +
      "</div>"
    );
  }).join("");
}

function renderInsights(insights) {
  if (!insights) return "";
  if (insights.status === "ok") {
    const points = (insights.investment_points || []).map(insightItem).join("");
    const risks = (insights.risks || []).map(insightItem).join("");
    const views = (insights.analyst_views || []).map(insightItem).join("");
    const sources = (insights.sources || []).map((s) =>
      "[" + s.n + "] " + escapeHtml(s.title) + " · <i>" + escapeHtml(s.source) + "</i> · " +
      escapeHtml(s.date) + ' · <a href="' + escapeHtml(s.url || "#") + '" target="_blank" rel="noopener">' +
      escapeHtml(s.url) + "</a>"
    ).join("<br>");
    return (
      '<div class="ins-grid">' +
        '<div class="card ins-card ins-good"><div class="ins-title tone-good">📈 투자포인트</div>' +
          "<ul>" + (points || "<li>해당 없음</li>") + "</ul></div>" +
        '<div class="card ins-card ins-warn"><div class="ins-title tone-warn">⚠️ 리스크</div>' +
          "<ul>" + (risks || "<li>해당 없음</li>") + "</ul></div>" +
        (views ? '<div class="card ins-card ins-neutral"><div class="ins-title tone-neutral">💬 시각</div>' +
          "<ul>" + views + "</ul></div>" : "") +
      "</div>" +
      '<div class="card src-list"><b>출처</b> (최근 ' + (insights.window_days || 30) + "일)<br>" + (sources || "-") + "</div>"
    );
  }
  // status !== "ok" (disabled/no_data 등): 요약은 비활성이지만 원문 소스는 항상 보여준다.
  // "에러처럼 보이지 않게" — 안내 배너 + kind별로 묶은 원문 리스트를 정식 섹션처럼 구성한다.
  const notice = insights.status === "no_data"
    ? "최근 " + (insights.window_days || 30) + "일 내 관련 자료가 없습니다."
    : "요약(Claude) 기능이 비활성화되어 있어, 수집된 원문을 매체별로 정리해 보여줍니다.";
  return (
    '<div class="ins-banner">' + escapeHtml(notice) + "</div>" +
    renderSourcesGrouped(insights.sources)
  );
}

// ---------------------------------------------------------------------------
// 섹션 셸
// ---------------------------------------------------------------------------

function section(title, hint, bodyHtml) {
  return (
    "<h2>" + escapeHtml(title) + (hint ? ' <span class="hint">' + escapeHtml(hint) + "</span>" : "") + "</h2>" +
    bodyHtml
  );
}

// ---------------------------------------------------------------------------
// 메인 렌더
// ---------------------------------------------------------------------------

function render(res) {
  const t = res.target || {};
  const s = res.stats || {};
  const deepdive = res.deepdive || {};
  const valuation = res.valuation;
  const targetPer = typeof t.per_op_ttm === "number" ? t.per_op_ttm
    : (typeof t.per_op_fwd === "number" ? t.per_op_fwd : t.per_op);

  const rows = (res.peers || []).map((p) => {
    const cls = p.is_target ? ' class="target"' : "";
    const badge = p.is_target ? "" : perBadgeClass(p.per_op, s.median, s.insufficient_peers);
    return (
      "<tr" + cls + "><td>" + escapeHtml(p.name) + ' <span class="code">' + escapeHtml(p.stock_code || "") + "</span></td>" +
      '<td class="num">' + fmt(p.market_cap, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_3m, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_annualized, 0) + "</td>" +
      '<td class="num ' + badge + '">' + perCell(p.per_op, p.per_status) + "</td>" +
      '<td class="num">' + krxPerCell(p.krx_per) + "</td>" +
      '<td class="num basis">' + basisLabel(p) + "</td></tr>"
    );
  }).join("");

  const costChart = svgCostStructure(costShare(deepdive.margins));

  const html =
    renderHeader(t, deepdive.overview) +
    renderValuationCard(valuation, s, targetPer) +
    renderKpiRow(t) +

    section("재무 인사이트", "손익계산서 기반 · 자동 서술", renderFinancialInsight(res.financial_insight)) +

    section("손익계산서", "당기 누적 vs 전년동기 누적", renderIncomeStatement(deepdive.income_statement, deepdive.margins)) +
    section("비용구조", "매출액 대비 비중 · 당기 vs 전년동기", '<div class="card chart-card">' + costChart + "</div>") +

    section("5개년 추이", "매출·영업이익 · 영업이익률", '<div class="card chart-card">' + svgTrendChart(deepdive.trend) + "</div>") +

    section("업종 PEER 비교", "시총 상위 5 + 타깃",
      '<div class="card table-scroll"><table><thead><tr>' +
        "<th>종목</th><th class=\"num\">시총(억)</th><th class=\"num\">최근분기 영업익(억)</th>" +
        "<th class=\"num\">연환산(억)</th><th class=\"num\">PER(연환산)</th><th class=\"num\">KRX PER</th>" +
        "<th class=\"num\">기준분기</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
      '<div class="card chart-card" style="margin-top:10px">' + svgPeerChart(res.peers, s.median) + "</div>") +

    section("최근 공시", "타깃 · 최근 90일", renderDisclosures(res.disclosures)) +

    section("산업 동향", "업종 키워드 기반 수집", renderIndustry(res.industry)) +

    section("뉴스·의견", "최근 " + ((res.insights && res.insights.window_days) || 30) + "일 · 뉴스·블로그", renderInsights(res.insights)) +

    '<div class="disclaimer">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.<br>' +
    'PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4). PER(TTM)은 최근 4개 분기 합산 영업이익/순이익 기준입니다. ' +
    "영업이익 적자 종목은 N/A(적자), 실적을 확인하지 못한 경우는 데이터 없음으로 표기됩니다.<br>" +
    "뉴스·의견 섹션은 공개된 매체·작성자 개인 견해의 자동 정리이며, 본 서비스의 투자 권유가 아닙니다.</div>";

  $("#result").innerHTML = html;

  // 라이브 SPA(web/index.html + app.js)에만 있는 "새로 검색" 버튼 — 정적 리포트에는
  // #view-search 화면 자체가 없으므로(요소가 없으면 showView/clearError 호출이 깨진다),
  // 그 화면이 실제로 존재할 때만 버튼을 덧붙인다.
  if (document.getElementById("view-search")) {
    $("#result").insertAdjacentHTML(
      "beforeend",
      '<div style="margin-top:24px"><button class="btn" id="again" type="button">새로 검색</button></div>'
    );
    document.getElementById("again").addEventListener("click", () => {
      $("#q").value = "";
      clearError();
      showView("search");
      $("#q").focus();
    });
  }
}
