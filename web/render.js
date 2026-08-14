// 결과(res) 렌더링 — 라이브 앱(web/app.js)과 정적 리포트(scripts/build_site.py가 생성하는
// HTML)가 함께 사용하는 순수 렌더 로직. DOM에 값을 "그려 넣는" 것 외의 상태(폴링, 진행바 등)는
// 다루지 않는다. 라이브 SPA 전용 동작(검색 화면으로 복귀)은 #view-search 존재 여부로
// 감지해서만 덧붙인다 — 정적 리포트에는 그 화면 자체가 없기 때문이다.
const $ = (s) => document.querySelector(s);

const REPRT_LABEL = { Q1: "1분기", HALF: "반기", Q3: "3분기", ANNUAL: "4분기" };

const fmt = (v, dp = 1) => (typeof v === "number" && !Number.isNaN(v)
  ? v.toLocaleString("ko-KR", { minimumFractionDigits: dp, maximumFractionDigits: dp })
  : "-");

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

function perBadgeClass(v, median, insufficientPeers) {
  if (insufficientPeers) return "";
  if (typeof v !== "number" || typeof median !== "number") return "";
  if (v < median) return "badge-under";
  if (v > median * 1.3) return "badge-over";
  return "";
}

function perNote(v, median, insufficientPeers) {
  // 비교 가능한(PER이 계산된) peer가 2개 미만이면 "업종 중앙값"은 사실상 타깃 자신과의
  // 비교로 퇴화한다 — 저평가/고평가 판정 대신 데이터 부족을 알린다.
  if (insufficientPeers) {
    return '<div class="note muted">비교 가능한 동종업체 부족</div>';
  }
  if (typeof v !== "number" || typeof median !== "number") {
    return '<div class="note muted">업종 비교 데이터 부족</div>';
  }
  if (v < median) return '<div class="note good">업종 중앙값 ' + fmt(median) + '배 대비 저평가</div>';
  if (v > median) return '<div class="note warn">업종 중앙값 ' + fmt(median) + '배 대비 고평가</div>';
  return '<div class="note muted">업종 중앙값과 동일</div>';
}

function fmtDate(d) {
  if (typeof d === "string" && d.length === 8) {
    return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6, 8);
  }
  return d || "-";
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function insightItem(p) {
  const sup = (p.sources || []).map((s) => "[" + s.n + "]").join("");
  const supHtml = sup ? ' <sup class="src-ref">' + escapeHtml(sup) + "</sup>" : "";
  return "<li>" + escapeHtml(p.text) + supHtml + "</li>";
}

function renderInsights(insights) {
  const heading = '<h2>투자포인트 · 리스크 <span class="hint">최근 1개월 뉴스·블로그 · Claude 요약</span></h2>';
  if (!insights || insights.status !== "ok") {
    const notice = insights && insights.status === "no_data" ? "최근 1개월 자료 없음" : "요약 비활성";
    return (
      heading +
      '<div class="card ins-empty">' + notice +
      " — 관련 최근 1개월 뉴스·블로그가 없거나 요약 기능이 비활성화되어 있습니다.</div>"
    );
  }

  const points = (insights.investment_points || []).map(insightItem).join("");
  const risks = (insights.risks || []).map(insightItem).join("");
  const sources = (insights.sources || []).map((s) =>
    "[" + s.n + "] " + escapeHtml(s.title) + " · <i>" + escapeHtml(s.source) + "</i> · " +
    escapeHtml(s.date) + ' · <a href="' + escapeHtml(s.url || "#") + '" target="_blank" rel="noopener">' +
    escapeHtml(s.url) + "</a>"
  ).join("<br>");

  return (
    heading +
    '<div class="ins-grid">' +
      '<div class="card ins-card ins-good"><div class="ins-title good">📈 투자포인트</div>' +
        '<ul>' + (points || "<li>해당 없음</li>") + "</ul></div>" +
      '<div class="card ins-card ins-warn"><div class="ins-title warn">⚠️ 리스크</div>' +
        '<ul>' + (risks || "<li>해당 없음</li>") + "</ul></div>" +
    "</div>" +
    '<div class="card src-list"><b>출처</b> (최근 1개월)<br>' + (sources || "-") + "</div>"
  );
}

function render(res) {
  const t = res.target;
  const s = res.stats || {};

  const rows = (res.peers || []).map((p) => {
    const cls = p.is_target ? ' class="target"' : "";
    const badge = p.is_target ? "" : perBadgeClass(p.per_op, s.median, s.insufficient_peers);
    return (
      "<tr" + cls + "><td>" + p.name + ' <span class="code">' + (p.stock_code || "") + "</span></td>" +
      '<td class="num">' + fmt(p.market_cap, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_3m, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_annualized, 0) + "</td>" +
      '<td class="num ' + badge + '">' + perCell(p.per_op, p.per_status) + "</td>" +
      '<td class="num">' + krxPerCell(p.krx_per) + "</td>" +
      '<td class="num basis">' + basisLabel(p) + "</td></tr>"
    );
  }).join("");

  const discTypeClass = { "정기공시": "reg", "주요사항": "major", "발행공시": "issue" };
  const disc = (res.disclosures || []).slice(0, 20).map((d) => {
    const cls = discTypeClass[d.type] || "etc";
    return (
      '<li><span class="d">' + fmtDate(d.date) + '</span><span class="t">' + d.title + '</span>' +
      '<span class="type ' + cls + '">' + d.type + "</span></li>"
    );
  }).join("");

  const rankLine = (s.rank && s.total && !s.insufficient_peers)
    ? (s.rank + "<small> / " + s.total + "</small>") : "-";

  const insights = renderInsights(res.insights);

  $("#result").innerHTML =
    '<div class="result-head"><h1>' + t.name + '</h1><span class="code">(' + (t.stock_code || "") + ")</span></div>" +
    '<div class="kpis">' +
      '<div class="kpi"><div class="lab">시가총액</div><div class="val">' + fmt(t.market_cap, 0) + '<small> 억</small></div></div>' +
      '<div class="kpi"><div class="lab">최근 분기 영업이익</div><div class="val">' + fmt(t.op_3m, 0) + '<small> 억</small></div>' +
        '<div class="note muted">기준 ' + basisLabel(t) + '</div></div>' +
      '<div class="kpi hl"><div class="lab">PER (영업이익 기준·연환산)</div><div class="val">' + perCell(t.per_op, t.per_status) + '<small> 배</small></div>' +
        perNote(t.per_op, s.median, s.insufficient_peers) + '</div>' +
      '<div class="kpi"><div class="lab">업종 내 순위</div><div class="val">' + rankLine + '</div>' +
        '<div class="note muted">' + (s.insufficient_peers ? "비교 가능한 동종업체 부족" : "PER 낮을수록 상위") + '</div></div>' +
    '</div>' +
    '<h2>업종 PEER 비교 <span class="hint">시총 상위 5 + 타깃</span></h2>' +
    '<div class="card table-scroll"><table><thead><tr>' +
      '<th>종목</th><th class="num">시총(억)</th><th class="num">최근분기 영업익(억)</th>' +
      '<th class="num">연환산(억)</th><th class="num">PER(영업이익)</th><th class="num">KRX PER</th>' +
      '<th class="num">기준분기</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<h2>PER 비교 차트</h2>' +
    '<div class="card chart-card"><img class="chart" src="data:image/png;base64,' + res.chart_per_b64 + '"></div>' +
    '<h2>최근 공시 <span class="hint">타깃 · 최근 90일</span></h2>' +
    '<ul class="disc">' + (disc || "<li>최근 공시 없음</li>") + '</ul>' +
    insights +
    '<div class="disclaimer">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.<br>' +
    'PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4). 영업이익 적자 종목은 N/A(적자), ' +
    '실적을 확인하지 못한 경우는 데이터 없음으로 표기됩니다.</div>';

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
