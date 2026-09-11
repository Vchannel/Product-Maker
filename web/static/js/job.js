import { $, $$, api, h, icon, render, fmtVND, fmtTime, timeAgo, fmtDate, statusBadge, kindBadge, toast, toastError, confirmDialog, withLoading, copyText, pathTail } from "./core.js";
import { mountReview } from "./review.js";

const JOB_ID = window.PM_JOB;
const STAGES = window.PM_STAGES; // [[key, label], ...]
const STAGE_LABEL = Object.fromEntries(STAGES);
const LOG_TAG = { scrape: "Tải trang", images: "Ảnh", content: "AI", review: "Duyệt", upload: "Upload", publish: "Đăng", system: "Hệ thống" };

const TIPS = {
  scrape: "Đang đọc trang sản phẩm trên flycampro.vn…",
  images: "Đang tải ảnh gốc và xoá logo flycampro…",
  content: "AI đang viết lại tiêu đề, mô tả và đọc phụ kiện trong hộp. Bước này thường mất 20–90 giây.",
  upload: "Đang upload ảnh lên thư viện media của WordPress…",
  publish: "Đang tạo sản phẩm trên WooCommerce…",
};

let job = null;
let lastLogId = 0;
let renderedKey = "";
let logLines = [];
let pollTimer = null;
let review = null;

// ---------------------------------------------------------------- header
function renderHead() {
  const title = job.title || (job.urls.length === 1 ? pathTail(job.urls[0]) : `${job.urls.length} link sản phẩm`);
  const canDelete = !["queued", "running"].includes(job.status);
  const canCancel = ["queued", "running", "review"].includes(job.status);
  render($("#job-head"),
    h("div", { class: "grow", style: { minWidth: 0 } },
      h("div", { class: "row-wrap", style: { marginBottom: "6px" } }, statusBadge(job.status), job.kind ? kindBadge(job.kind, job.kind === "variable" ? job.urls.length : 0) : null),
      h("h1", { class: "ellipsis" }, title),
      h("p", { class: "sub small" }, `Tạo ${timeAgo(job.created_at)} · ${fmtDate(job.created_at)} · `,
        job.urls.map((u, i) => [i ? ", " : "", h("a", { href: u, target: "_blank", rel: "noopener" }, pathTail(u))]))),
    h("div", { class: "page-actions" },
      canCancel ? h("button", { class: "btn btn-secondary", onclick: (e) => cancelJob(e.currentTarget) }, icon("x"), "Huỷ phiên") : null,
      canDelete ? h("button", { class: "btn btn-danger-ghost", onclick: (e) => deleteJob(e.currentTarget) }, icon("trash"), "Xoá") : null));
}

async function cancelJob(btn) {
  const running = job.status === "running";
  const ok = await confirmDialog({
    title: "Huỷ phiên nhập?",
    message: running ? "Ứng dụng sẽ dừng sau bước đang chạy. Các bước đã xong vẫn được lưu để lần sau chạy nhanh hơn." : "Phiên sẽ chuyển sang trạng thái đã huỷ. Bạn có thể mở lại sau.",
    confirm: "Huỷ phiên",
    cancel: "Không",
    danger: true,
  });
  if (!ok) return;
  await withLoading(btn, async () => {
    try { await api("POST", `/api/jobs/${JOB_ID}/cancel`, {}); toast(running ? "Đang dừng…" : "Đã huỷ phiên", "info"); poll(true); } catch (e) { toastError(e); }
  });
}

async function deleteJob(btn) {
  const ok = await confirmDialog({ title: "Xoá phiên nhập này?", message: "Chỉ xoá lịch sử trong ứng dụng - sản phẩm đã đăng trên website không bị ảnh hưởng.", confirm: "Xoá", danger: true });
  if (!ok) return;
  await withLoading(btn, async () => {
    try { await api("DELETE", `/api/jobs/${JOB_ID}`, {}); location.href = "/jobs"; } catch (e) { toastError(e); }
  });
}

// ---------------------------------------------------------------- stepper
function stageState(key) {
  const st = (job.stages || {})[key] || {};
  if (key === "review" && job.status === "review") return { cls: "waiting", detail: "chờ bạn duyệt" };
  const map = { running: "running", done: "done", skipped: "skipped", error: "error" };
  let cls = map[st.status] || "";
  if (cls === "running" && job.status !== "running") cls = job.status === "failed" ? "error" : "";
  let detail = st.detail || "";
  if (cls === "running" && st.total) detail = `${st.done || 0}/${st.total}`;
  if (cls === "skipped" && !detail) detail = "bỏ qua";
  return { cls, detail, done: st.done, total: st.total };
}

function renderStepper() {
  $$("#stepper .step").forEach((el, i) => {
    const key = el.dataset.stage;
    const s = stageState(key);
    el.className = `step ${s.cls}`;
    const dot = el.querySelector(".step-dot");
    const glyph = { done: icon("check"), error: icon("x"), running: icon("loader", "spin"), waiting: icon("edit"), skipped: icon("minus") }[s.cls];
    render(dot, glyph || h("span", { class: "step-n tiny strong" }, i + 1));
    el.querySelector(".step-detail").textContent = s.detail;
  });
}

function currentStage() {
  const stages = job.stages || {};
  return STAGES.map(([k]) => k).find((k) => stages[k]?.status === "running");
}

// ---------------------------------------------------------------- main area
function mainKey() {
  // Re-render the main area only when the kind of view changes, so the review
  // editor and progress bars aren't rebuilt on every poll.
  return `${job.status}:${job.phase}`;
}

function renderMain(force = false) {
  const key = mainKey();
  const main = $("#job-main");
  if (!force && key === renderedKey) {
    updateProgress();
    return;
  }
  renderedKey = key;
  review = null;

  if (job.status === "queued" || job.status === "running") {
    render(main, h("section", { class: "card" }, h("div", { class: "hero-state" },
      h("div", { class: "hero-icon accent pulse" }, icon(job.status === "queued" ? "clock" : "loader", job.status === "running" ? "spin" : "")),
      h("h2", { id: "run-title" }), h("p", { id: "run-tip" }),
      h("div", { style: { width: "min(420px, 100%)" } }, h("div", { class: "progress", id: "run-progress" }, h("span", { style: { width: "0%" } }))),
      h("p", { class: "small faint", id: "run-foot" }, "Bạn có thể rời trang này - quá trình vẫn tiếp tục chạy nền."))));
    updateProgress();
    return;
  }

  if (job.status === "review") {
    if (!job.draft) { render(main, h("div", { class: "card card-body" }, "Đang tải bản nháp…")); renderedKey = ""; return; }
    review = mountReview(main, job, {
      onPublish: () => poll(true),
      onRegenerate: () => poll(true),
    });
    return;
  }

  if (job.status === "done") return render(main, resultView());
  if (job.status === "failed") return render(main, failedView());
  if (job.status === "cancelled") return render(main, cancelledView());
}

function updateProgress() {
  const title = $("#run-title");
  if (!title) return;
  if (job.status === "queued") {
    title.textContent = job.queue_position > 1 ? `Đang chờ tới lượt (vị trí ${job.queue_position})` : "Sắp bắt đầu…";
    $("#run-tip").textContent = job.phase === "publish" ? "Bản nháp đã được duyệt, đang chờ để đăng lên website." : "Các phiên được xử lý lần lượt để không ghi đè dữ liệu của nhau.";
    $("#run-progress").className = "progress indeterminate";
    return;
  }
  const key = currentStage();
  title.textContent = key ? `${STAGE_LABEL[key]}…` : "Đang xử lý…";
  $("#run-tip").textContent = TIPS[key] || "";
  const st = (job.stages || {})[key] || {};
  const bar = $("#run-progress");
  if (st.total) {
    bar.className = "progress";
    bar.firstChild.style.width = `${Math.round(((st.done || 0) / st.total) * 100)}%`;
  } else {
    bar.className = "progress indeterminate";
  }
}

function resultView() {
  const r = job.result || {};
  const actionText = {
    created: "Đã tạo sản phẩm mới trên website",
    updated: "Đã cập nhật sản phẩm trên website",
    extended: "Đã bổ sung phiên bản mới cho sản phẩm có sẵn",
    skipped: "Sản phẩm đã có sẵn - không có gì thay đổi",
  }[r.action] || "Hoàn tất";
  const statusText = { draft: "Nháp", publish: "Đang bán", pending: "Chờ duyệt", private: "Riêng tư" }[r.status] || r.status;
  const vars = r.variations || [];
  return h("div", { class: "stack" },
    h("section", { class: "card" },
      h("div", { class: "result-card" },
        resultImage(r.thumb_url),
        h("div", { class: "grow stack-sm" },
          h("div", { class: "row-wrap" }, h("span", { class: "badge success" }, icon("check"), actionText)),
          h("h2", { style: { fontSize: "19px" } }, r.name),
          h("div", { class: "row-wrap small muted" },
            h("span", null, `#${r.product_id}`), "·", h("span", { class: "mono" }, r.sku), "·", h("span", null, `Trạng thái: ${statusText}`), "·",
            h("span", { class: "strong", style: { color: "var(--accent-text)" } }, r.price_min === r.price_max ? fmtVND(r.sale_price || r.price_min) : `${fmtVND(r.price_min)} – ${fmtVND(r.price_max)}`)),
          h("div", { class: "row-wrap mt-8" },
            h("a", { class: "btn btn-primary", href: r.edit_link, target: "_blank", rel: "noopener" }, icon("wordpress"), "Mở trong WordPress"),
            r.permalink ? h("a", { class: "btn btn-secondary", href: r.permalink, target: "_blank", rel: "noopener" }, icon("store"), r.status === "publish" ? "Xem trên cửa hàng" : "Xem trước trên cửa hàng") : null,
            h("a", { class: "btn btn-ghost", href: "/import" }, icon("plus"), "Nhập sản phẩm khác"))))),
    vars.length ? h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h3", null, icon("layers"), `Phiên bản (${vars.length})`)),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, "Phiên bản"), h("th", { class: "hide-sm" }, "SKU"), h("th", { class: "right" }, "Giá"), h("th", null, "Kết quả"))),
        h("tbody", null, vars.map((v) => h("tr", null,
          h("td", { class: "strong" }, v.label),
          h("td", { class: "mono small hide-sm" }, v.sku),
          h("td", { class: "right num" }, v.sale_price ? [fmtVND(v.sale_price), h("div", { class: "tiny faint", style: { textDecoration: "line-through" } }, fmtVND(v.regular_price))] : fmtVND(v.regular_price)),
          h("td", null, h("span", { class: `badge ${v.action === "skipped" ? "" : "success"}` }, { created: "Tạo mới", updated: "Cập nhật", skipped: "Giữ nguyên" }[v.action] || v.action)))))))) : null);
}

function resultImage(src) {
  const fallback = () => h("div", { class: "hero-icon success", style: { width: "96px", height: "96px" } }, icon("check"));
  if (!src) return fallback();
  const img = h("img", { src, alt: "" });
  img.addEventListener("error", () => img.replaceWith(fallback()), { once: true });
  return img;
}

function failedView() {
  const inPublish = job.phase === "publish";
  return h("section", { class: "card" }, h("div", { class: "hero-state" },
    h("div", { class: "hero-icon danger" }, icon("alert-circle")),
    h("h2", null, inPublish ? "Chưa đăng được sản phẩm" : "Chưa chuẩn bị xong bản nháp"),
    h("p", { style: { color: "var(--danger-text)", whiteSpace: "pre-wrap" } }, job.error || "Lỗi không rõ"),
    h("p", { class: "small" }, inPublish
      ? "Bản nháp của bạn vẫn được giữ nguyên. Thử đăng lại, hoặc mở lại bản nháp để chỉnh sửa (ví dụ đổi SKU)."
      : "Các bước đã hoàn thành được lưu lại - thử lại sẽ chạy tiếp từ chỗ bị lỗi."),
    h("div", { class: "actions" },
      h("button", { class: "btn btn-primary", onclick: (e) => act(e.currentTarget, "retry", inPublish ? "Đang thử đăng lại…" : "Đang chạy lại…") }, icon("rotate"), inPublish ? "Thử đăng lại" : "Thử lại"),
      job.has_draft ? h("button", { class: "btn btn-secondary", onclick: (e) => act(e.currentTarget, "reopen", "Đã mở lại bản nháp") }, icon("edit"), "Mở lại bản nháp") : null,
      !inPublish && /cấu hình|api key|Cài đặt/i.test(job.error || "") ? h("a", { class: "btn btn-ghost", href: "/settings" }, icon("settings"), "Mở Cài đặt") : null)));
}

function cancelledView() {
  return h("section", { class: "card" }, h("div", { class: "hero-state" },
    h("div", { class: "hero-icon neutral" }, icon("x")),
    h("h2", null, "Phiên đã bị huỷ"),
    h("p", null, job.error || ""),
    h("div", { class: "actions" },
      job.has_draft ? h("button", { class: "btn btn-primary", onclick: (e) => act(e.currentTarget, "reopen", "Đã mở lại bản nháp") }, icon("edit"), "Mở lại bản nháp") : null,
      h("button", { class: job.has_draft ? "btn btn-secondary" : "btn btn-primary", onclick: (e) => act(e.currentTarget, "retry", "Đang chạy lại…") }, icon("rotate"), job.phase === "publish" && job.has_draft ? "Đăng luôn bản nháp" : "Chạy lại"))));
}

async function act(btn, action, message) {
  await withLoading(btn, async () => {
    try {
      await api("POST", `/api/jobs/${JOB_ID}/${action}`, {});
      toast(message, "success");
      await poll(true);
    } catch (e) { toastError(e); }
  });
}

// ---------------------------------------------------------------- logs
const consoleEl = $("#console");
function appendLogs(logs) {
  if (!logs.length) return;
  if (!logLines.length) consoleEl.textContent = "";
  const nearBottom = consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 60;
  for (const l of logs) {
    logLines.push(l);
    consoleEl.appendChild(h("div", { class: `console-line ${l.level}` },
      h("span", { class: "t" }, fmtTime(l.ts)),
      h("span", { class: "s" }, LOG_TAG[l.stage] || l.stage),
      h("span", { class: "m" }, l.message)));
    lastLogId = Math.max(lastLogId, l.id);
  }
  $("#log-count").textContent = `${logLines.length} dòng`;
  if (nearBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
}

$("#log-toggle").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const open = btn.getAttribute("aria-expanded") === "true";
  consoleEl.hidden = open;
  btn.setAttribute("aria-expanded", String(!open));
  btn.querySelector("span").textContent = open ? "Mở rộng" : "Thu gọn";
  btn.querySelector("svg").style.transform = open ? "rotate(-90deg)" : "";
});
$("#log-copy").addEventListener("click", () => copyText(logLines.map((l) => `${fmtTime(l.ts)} [${l.stage}] ${l.message}`).join("\n")));

// ---------------------------------------------------------------- polling
async function poll(immediate = false) {
  clearTimeout(pollTimer);
  try {
    const data = await api("GET", `/api/jobs/${JOB_ID}?after=${lastLogId}&draft=${job && job.status === "review" && renderedKey.startsWith("review") ? 0 : 1}`);
    const prevStatus = job?.status;
    const keepDraft = job?.draft;
    job = data;
    if (!job.draft && keepDraft && job.status === "review") job.draft = keepDraft;
    renderHead();
    renderStepper();
    renderMain();
    appendLogs(data.logs || []);
    if (prevStatus && prevStatus !== job.status) {
      if (job.status === "review") toast("Bản nháp đã sẵn sàng để duyệt", "success");
      if (job.status === "done") toast("Đã đăng sản phẩm thành công 🎉", "success");
      if (job.status === "failed") toast("Phiên nhập gặp lỗi", "error");
      document.title = `${{ review: "✎ Chờ duyệt", done: "✓ Hoàn tất", failed: "⚠ Lỗi" }[job.status] || "Phiên nhập"} · Product Maker`;
    }
    const logCard = $("#log-card");
    if (job.status === "review" && prevStatus !== "review") {
      consoleEl.hidden = true;
      $("#log-toggle").setAttribute("aria-expanded", "false");
      $("#log-toggle span").textContent = "Mở rộng";
    }
    logCard.hidden = false;
  } catch (e) {
    if (e.status === 404) { render($("#job-main"), h("div", { class: "card card-body" }, "Phiên nhập không còn tồn tại.")); return; }
    if (immediate) toastError(e);
  }
  const active = job && ["queued", "running"].includes(job.status);
  pollTimer = setTimeout(poll, active ? 900 : 5000);
}

window.addEventListener("beforeunload", (e) => {
  if (review && review.dirty) { e.preventDefault(); e.returnValue = ""; }
});

poll(true);
