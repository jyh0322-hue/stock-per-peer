const $ = (s) => document.querySelector(s);

const STEP_LABELS = ["종목 해석", "업종·시총 조회", "PEER 실적 수집", "PER 계산", "공시·결과 조립"];

const fmt = (v, dp = 1) => (typeof v === "number" && !Number.isNaN(v)
  ? v.toLocaleString("ko-KR", { minimumFractionDigits: dp, maximumFractionDigits: dp })
  : "-");
const per = (v) => (typeof v === "number" && !Number.isNaN(v) ? fmt(v, 1) : "N/A(적자)");

function showView(name) {
  ["search", "progress", "result"].forEach((v) => {
    $("#view-" + v).classList.toggle("hidden", v !== name);
  });
}

function showError(msg) {
  showView("search");
  const box = $("#error");
  box.textContent = msg;
  box.classList.remove("hidden");
}

function clearError() {
  $("#error").classList.add("hidden");
}

$("#f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#q").value.trim();
  if (!name) return;
  clearError();
  await startAnalysis(name);
});

document.querySelectorAll(".tagpill").forEach((tag) => {
  tag.addEventListener("click", () => {
    $("#q").value = tag.dataset.name || tag.textContent.trim();
    $("#q").focus();
  });
});

async function startAnalysis(name) {
  $("#q-echo").value = name;
  showView("progress");
  setProgress({ step: "요청 전송", current: 0, total: 5, pct: 0 });
  try {
    const r = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (r.status === 400) {
      const body = await r.json().catch(() => ({}));
      showError(body.detail || "종목을 찾을 수 없습니다.");
      return;
    }
    if (!r.ok) {
      showError("요청 실패 (HTTP " + r.status + ")");
      return;
    }
    const { job_id } = await r.json();
    poll(job_id);
  } catch (err) {
    showError("네트워크 오류: " + err);
  }
}

function setProgress(p) {
  const pct = p.pct || 0;
  $("#fill").style.width = pct + "%";
  $("#prog-caption").innerHTML = "<b>" + (p.step || "진행 중") + "</b> · " +
    (p.current || 0) + "/" + (p.total || 5) + " 단계 (" + pct + "%)";
  updateSteps(p.current || 0, p.total || 5);
}

function updateSteps(current, total) {
  for (let i = 1; i <= 5; i++) {
    const li = document.querySelector('#steps [data-step="' + i + '"]');
    if (!li) continue;
    const ic = li.querySelector(".ic");
    const label = li.querySelector(".label");
    if (i < current) {
      li.className = "done";
      ic.textContent = "✓";
      label.textContent = STEP_LABELS[i - 1];
    } else if (i === current) {
      li.className = "now";
      ic.innerHTML = '<span class="spin"></span>';
      label.textContent = STEP_LABELS[i - 1] + " (" + current + "/" + total + ")";
    } else {
      li.className = "";
      ic.textContent = "·";
      label.textContent = STEP_LABELS[i - 1];
    }
  }
}

async function poll(jobId) {
  let st;
  try {
    st = await (await fetch("/api/status/" + jobId)).json();
  } catch (err) {
    showError("네트워크 오류: " + err);
    return;
  }
  if (st.state === "error") {
    showError(st.error || "분석 실패");
    return;
  }
  setProgress(st.progress || {});
  if (st.state === "done") {
    const res = await (await fetch("/api/result/" + jobId)).json();
    render(res);
    showView("result");
    return;
  }
  setTimeout(() => poll(jobId), 1500);
}

function perBadgeClass(v, median) {
  if (typeof v !== "number" || typeof median !== "number") return "";
  if (v < median) return "badge-under";
  if (v > median * 1.3) return "badge-over";
  return "";
}

function perNote(v, median) {
  if (typeof v !== "number" || typeof median !== "number") {
    return '<div class="note muted">업종 비교 데이터 부족</div>';
  }
  if (v < median) return '<div class="note good">업종 중앙값 ' + fmt(median) + '배 대비 저평가</div>';
  if (v > median) return '<div class="note warn">업종 중앙값 ' + fmt(median) + '배 대비 고평가</div>';
  return '<div class="note muted">업종 중앙값과 동일</div>';
}

function render(res) {
  const t = res.target;
  const s = res.stats || {};

  const rows = (res.peers || []).map((p) => {
    const cls = p.is_target ? ' class="target"' : "";
    const badge = p.is_target ? "" : perBadgeClass(p.per_op, s.median);
    return (
      "<tr" + cls + "><td>" + p.name + ' <span class="code">' + (p.stock_code || "") + "</span></td>" +
      '<td class="num">' + fmt(p.market_cap, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_3m, 0) + "</td>" +
      '<td class="num">' + fmt(p.op_annualized, 0) + "</td>" +
      '<td class="num ' + badge + '">' + per(p.per_op) + "</td>" +
      '<td class="num">' + per(p.krx_per) + "</td></tr>"
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

  const rankLine = (s.rank && s.total) ? (s.rank + "<small> / " + s.total + "</small>") : "-";

  const insights = renderInsights(res.deepdive);

  $("#result").innerHTML =
    '<div class="result-head"><h1>' + t.name + '</h1><span class="code">(' + (t.stock_code || "") + ")</span></div>" +
    '<div class="kpis">' +
      '<div class="kpi"><div class="lab">시가총액</div><div class="val">' + fmt(t.market_cap, 0) + '<small> 억</small></div></div>' +
      '<div class="kpi"><div class="lab">최근 분기 영업이익</div><div class="val">' + fmt(t.op_3m, 0) + '<small> 억</small></div></div>' +
      '<div class="kpi hl"><div class="lab">PER (영업이익 기준·연환산)</div><div class="val">' + per(t.per_op) + '<small> 배</small></div>' +
        perNote(t.per_op, s.median) + '</div>' +
      '<div class="kpi"><div class="lab">업종 내 순위</div><div class="val">' + rankLine + '</div>' +
        '<div class="note muted">PER 낮을수록 상위</div></div>' +
    '</div>' +
    '<h2>업종 PEER 비교 <span class="hint">시총 상위 5 + 타깃</span></h2>' +
    '<div class="card table-scroll"><table><thead><tr>' +
      '<th>종목</th><th class="num">시총(억)</th><th class="num">최근분기 영업익(억)</th>' +
      '<th class="num">연환산(억)</th><th class="num">PER(영업이익)</th><th class="num">KRX PER</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<h2>PER 비교 차트</h2>' +
    '<div class="card chart-card"><img class="chart" src="data:image/png;base64,' + res.chart_per_b64 + '"></div>' +
    '<h2>최근 공시 <span class="hint">타깃 · 최근 90일</span></h2>' +
    '<ul class="disc">' + (disc || "<li>최근 공시 없음</li>") + '</ul>' +
    insights +
    '<div class="disclaimer">※ OpenDART·KRX 데이터를 자동 집계한 자료로, 투자자문·매매판단을 제공하지 않습니다.<br>' +
    'PER(영업이익 기준, 연환산) = 시가총액 ÷ (최근 분기 영업이익 × 4). 영업이익 적자 종목은 N/A(적자)로 표기됩니다.</div>' +
    '<div style="margin-top:24px"><button class="btn" id="again" type="button">새로 검색</button></div>';

  $("#again").addEventListener("click", () => {
    $("#q").value = "";
    clearError();
    showView("search");
    $("#q").focus();
  });
}

function fmtDate(d) {
  if (typeof d === "string" && d.length === 8) {
    return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6, 8);
  }
  return d || "-";
}

function renderInsights(deepdive) {
  if (!deepdive) return "";
  const points = (deepdive.points || []).map((x) => "<li>" + x + "</li>").join("");
  const risks = (deepdive.risks || []).map((x) => "<li>" + x + "</li>").join("");
  return (
    '<h2>투자포인트 · 리스크 <span class="hint">최근 1개월 뉴스·블로그 요약</span></h2>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">' +
      '<div class="card" style="padding:16px 18px;border-left:4px solid var(--good)">' +
        '<div style="font-weight:700;color:var(--good);margin-bottom:8px">투자포인트</div>' +
        '<ul style="margin:0;padding-left:18px;font-size:.9rem">' + points + '</ul></div>' +
      '<div class="card" style="padding:16px 18px;border-left:4px solid var(--warn)">' +
        '<div style="font-weight:700;color:var(--warn);margin-bottom:8px">리스크</div>' +
        '<ul style="margin:0;padding-left:18px;font-size:.9rem">' + risks + '</ul></div>' +
    '</div>'
  );
}
