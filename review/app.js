const STORAGE_KEY = "alignlab-review-pocket-v1";
const REASONS = ["問題なし", "物理・常識", "年齢・人物一貫性", "関係性", "構図・視線", "台詞・可読性", "動き", "その他"];
const LABELS = { pending: "未確認", ok: "見た限りOK", hold: "保留", retake: "要修正" };

let queueData = { generated_at: "-", items: [] };
let reviewState = loadState();
let activeFilter = "all";

const queueNode = document.querySelector("#queue");
const cardTemplate = document.querySelector("#cardTemplate");
const searchInput = document.querySelector("#searchInput");
const exportText = document.querySelector("#exportText");
const copyStatus = document.querySelector("#copyStatus");

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch { return {}; }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(reviewState));
  refreshSummary();
  refreshExport();
}

function stateFor(id) {
  return reviewState[id] || { decision: "pending", reasons: [], note: "", reviewed_at: null };
}

async function loadQueue({ bypassCache = false } = {}) {
  queueNode.innerHTML = '<div class="empty-state">確認キューを読み込んでいます…</div>';
  try {
    const suffix = bypassCache ? `?t=${Date.now()}` : "";
    const response = await fetch(`queue.json${suffix}`, { cache: bypassCache ? "no-store" : "default" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    queueData = await response.json();
    document.querySelector("#updatedAt").textContent = `queue: ${queueData.generated_at}`;
    renderQueue();
  } catch (error) {
    queueNode.innerHTML = `<div class="empty-state">キューを取得できませんでした。通信を確認して再読み込みしてください。<br><small>${escapeHtml(String(error))}</small></div>`;
  }
}

function renderQueue() {
  queueNode.innerHTML = "";
  const query = searchInput.value.trim().toLowerCase();
  const items = queueData.items.filter((item) => {
    const current = stateFor(item.id).decision;
    const matchFilter = activeFilter === "all" || current === activeFilter;
    const haystack = [item.id, item.title, item.lane, item.summary, ...(item.review_focus || [])].join(" ").toLowerCase();
    return matchFilter && (!query || haystack.includes(query));
  });

  if (!items.length) {
    queueNode.innerHTML = '<div class="empty-state">この条件の確認項目はありません。</div>';
  } else {
    items.forEach((item) => queueNode.appendChild(buildCard(item)));
  }
  refreshSummary();
  refreshExport();
}

function buildCard(item) {
  const card = cardTemplate.content.firstElementChild.cloneNode(true);
  const current = stateFor(item.id);
  card.dataset.id = item.id;
  card.querySelector(".card-meta").textContent = `${item.lane} · ${item.kind}`;
  card.querySelector(".card-title").textContent = item.title;
  card.querySelector(".card-summary").textContent = item.summary;
  updateDecisionVisuals(card, current.decision);

  const mediaSlot = card.querySelector(".media-slot");
  if (item.media?.type === "video") {
    const video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.src = item.media.src;
    if (item.media.poster) video.poster = item.media.poster;
    video.setAttribute("aria-label", item.title);
    mediaSlot.appendChild(video);
  } else if (item.media?.type === "image") {
    const image = document.createElement("img");
    image.loading = "lazy";
    image.src = item.media.src;
    image.alt = item.media.alt || item.title;
    mediaSlot.appendChild(image);
  } else {
    mediaSlot.innerHTML = '<div class="no-media">プレビューなし</div>';
  }

  const focusList = card.querySelector(".focus-list");
  (item.review_focus || []).forEach((focus) => {
    const li = document.createElement("li");
    li.textContent = focus;
    focusList.appendChild(li);
  });

  const badgeWrap = card.querySelector(".audit-badges");
  (item.audit_badges || []).forEach((badge) => {
    const span = document.createElement("span");
    span.textContent = badge;
    badgeWrap.appendChild(span);
  });

  const chips = card.querySelector(".reason-chips");
  REASONS.forEach((reason) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = current.reasons.includes(reason);
    input.addEventListener("change", () => {
      const next = new Set(stateFor(item.id).reasons);
      input.checked ? next.add(reason) : next.delete(reason);
      reviewState[item.id] = { ...stateFor(item.id), reasons: [...next] };
      saveState();
    });
    const span = document.createElement("span");
    span.textContent = reason;
    label.append(input, span);
    chips.appendChild(label);
  });

  const note = card.querySelector(".note-input");
  note.value = current.note;
  note.addEventListener("input", () => {
    reviewState[item.id] = { ...stateFor(item.id), note: note.value };
    saveState();
  });

  card.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => {
      const decision = button.dataset.decision;
      reviewState[item.id] = {
        ...stateFor(item.id),
        decision,
        reviewed_at: decision === "pending" ? null : new Date().toISOString()
      };
      saveState();
      updateDecisionVisuals(card, decision);
      if (activeFilter !== "all" && activeFilter !== decision) renderQueue();
    });
  });

  const provenance = card.querySelector(".provenance");
  const rows = [
    ["ID", item.id], ["制作状態", item.production_status], ["正典境界", item.canon_boundary],
    ["由来", item.source], ["SHA-256", item.sha256 || "未記載"]
  ];
  rows.forEach(([term, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    provenance.append(dt, dd);
  });
  return card;
}

function updateDecisionVisuals(card, decision) {
  const pill = card.querySelector(".decision-pill");
  pill.textContent = LABELS[decision] || LABELS.pending;
  pill.dataset.decision = decision;
  card.querySelectorAll("[data-decision]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.decision === decision);
  });
}

function refreshSummary() {
  const counts = { pending: 0, ok: 0, hold: 0, retake: 0 };
  queueData.items.forEach((item) => counts[stateFor(item.id).decision]++);
  Object.entries(counts).forEach(([key, value]) => {
    document.querySelector(`#${key}Count`).textContent = value;
  });
}

function buildExport() {
  const reviewed = queueData.items
    .map((item) => ({ item, state: stateFor(item.id) }))
    .filter(({ state }) => state.decision !== "pending");
  const lines = [
    "[ALIGN_LAB_VISUAL_REVIEW v1]",
    `queue_generated_at: ${queueData.generated_at}`,
    `exported_at: ${new Date().toISOString()}`,
    "scope: visual_review_only; NOT HUMAN_LOCK; NOT SHIP; NOT canon",
    ""
  ];
  if (!reviewed.length) lines.push("reviewed_items: 0");
  reviewed.forEach(({ item, state }, index) => {
    lines.push(`${index + 1}. ${item.id}`);
    lines.push(`decision: ${state.decision}`);
    lines.push(`reasons: ${state.reasons.join(" / ") || "none"}`);
    lines.push(`note: ${state.note.trim() || "none"}`);
    lines.push(`reviewed_at: ${state.reviewed_at || "unknown"}`);
    lines.push("");
  });
  return lines.join("\n");
}

function refreshExport() { exportText.value = buildExport(); }

async function copyExport() {
  const text = buildExport();
  exportText.value = text;
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.textContent = "コピーしました。ChatGPTのタスクへ貼り付けてください。";
  } catch {
    exportText.focus();
    exportText.select();
    copyStatus.textContent = "自動コピーできませんでした。選択済みの文章を手動でコピーしてください。";
  }
}

async function shareExport() {
  const text = buildExport();
  if (navigator.share) {
    try { await navigator.share({ title: "ALIGN Lab Visual Review", text }); }
    catch (error) { if (error.name !== "AbortError") copyStatus.textContent = String(error); }
  } else {
    await copyExport();
  }
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

document.querySelector("#filterBar").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  activeFilter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
  renderQueue();
});

searchInput.addEventListener("input", renderQueue);
document.querySelector("#refreshButton").addEventListener("click", () => loadQueue({ bypassCache: true }));
document.querySelector("#copyButton").addEventListener("click", copyExport);
document.querySelector("#shareButton").addEventListener("click", shareExport);

if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

loadQueue();
