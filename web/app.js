// 라이브 SPA(검색 → 진행바 폴링 → 결과 표시) 전용 로직.
// 결과 렌더링(render, perCell 등 순수 헬퍼)은 web/render.js로 분리되어 정적 리포트
// 생성기(scripts/build_site.py)와 공유된다 — index.html이 render.js를 이 스크립트보다
// 먼저 로드하므로 render()/기타 헬퍼가 이미 전역에 존재한다.
const STEP_LABELS = ["종목 해석", "업종·시총 조회", "PEER 실적 수집", "PER 계산", "공시 수집", "뉴스·요약"];
const STEP_TOTAL = STEP_LABELS.length;

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
  setProgress({ step: "요청 전송", current: 0, total: STEP_TOTAL, pct: 0 });
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
    (p.current || 0) + "/" + (p.total || STEP_TOTAL) + " 단계 (" + pct + "%)";
  updateSteps(p.current || 0, p.total || STEP_TOTAL);
}

function updateSteps(current, total) {
  for (let i = 1; i <= STEP_TOTAL; i++) {
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
