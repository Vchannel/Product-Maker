import { $, $$, api, h, icon, render, timeAgo, fmtDate, statusBadge, kindBadge, thumbImg, emptyState, toastError, pathTail } from "./core.js";

const TABS = {
  all: () => true,
  active: (j) => ["queued", "running"].includes(j.status),
  review: (j) => j.status === "review",
  done: (j) => j.status === "done",
  failed: (j) => ["failed", "cancelled"].includes(j.status),
};
const STAGE_ORDER = ["scrape", "images", "content", "review", "upload", "publish"];

let tab = new URLSearchParams(location.search).get("tab") || "all";
if (!TABS[tab]) tab = "all";
let jobs = [];

$$("#tabs button").forEach((b) => {
  b.classList.toggle("on", b.dataset.tab === tab);
  b.addEventListener("click", () => {
    tab = b.dataset.tab;
    $$("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
    history.replaceState(null, "", tab === "all" ? "/jobs" : `/jobs?tab=${tab}`);
    draw();
  });
});

function progressCell(job) {
  const stages = job.stages || {};
  const done = STAGE_ORDER.filter((k) => ["done", "skipped"].includes(stages[k]?.status)).length;
  const pct = job.status === "done" ? 100 : Math.round((done / STAGE_ORDER.length) * 100);
  return h("div", { style: { width: "110px" } },
    h("div", { class: `progress ${job.status === "running" && !done ? "indeterminate" : ""}` }, h("span", { style: { width: `${pct}%`, background: job.status === "failed" ? "var(--danger)" : undefined } })),
    h("div", { class: "tiny faint mt-8" }, `${done}/${STAGE_ORDER.length} bước`));
}

function draw() {
  for (const [key, fn] of Object.entries(TABS)) {
    const n = jobs.filter(fn).length;
    const el = $(`[data-count="${key}"]`);
    el.textContent = n || "";
    el.hidden = !n;
  }
  const list = jobs.filter(TABS[tab]);
  if (!list.length) {
    render($("#jobs-table"), emptyState("layers", tab === "all" ? "Chưa có phiên nhập nào" : "Không có phiên nào ở mục này",
      tab === "all" ? "Mỗi lần bạn nhập sản phẩm sẽ tạo một phiên để theo dõi." : null,
      tab === "all" ? h("a", { class: "btn btn-primary btn-sm", href: "/import" }, icon("plus"), "Nhập sản phẩm") : null));
    return;
  }
  render($("#jobs-table"), h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", null, h("tr", null,
      h("th", null, "Sản phẩm"), h("th", { class: "hide-sm" }, "Loại"), h("th", null, "Trạng thái"),
      h("th", { class: "hide-sm" }, "Tiến trình"), h("th", { class: "hide-sm" }, "Thời gian"), h("th"))),
    h("tbody", null, list.map((j) => {
      const title = j.title || j.urls.map(pathTail).join(", ");
      const tr = h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a")) location.href = `/jobs/${j.id}`; } },
        h("td", null, h("div", { class: "row", style: { minWidth: "240px" } }, thumbImg(j.thumb),
          h("div", { class: "grow", style: { minWidth: 0 } },
            h("a", { class: "strong ellipsis", href: `/jobs/${j.id}`, style: { color: "var(--text)", display: "block", maxWidth: "420px" } }, title),
            h("div", { class: "tiny faint ellipsis", style: { maxWidth: "420px" } }, j.status === "failed" && j.error ? j.error : j.urls.map(pathTail).join(" · "))))),
        h("td", { class: "hide-sm" }, j.kind ? kindBadge(j.kind, j.kind === "variable" ? j.urls.length : 0) : h("span", { class: "faint small" }, "—")),
        h("td", null, statusBadge(j.status)),
        h("td", { class: "hide-sm" }, progressCell(j)),
        h("td", { class: "hide-sm nowrap small muted", title: fmtDate(j.created_at) }, timeAgo(j.created_at)),
        h("td", { class: "right nowrap" },
          j.result?.edit_link ? h("a", { class: "btn btn-ghost btn-sm btn-icon", href: j.result.edit_link, target: "_blank", rel: "noopener", title: "Mở trong WordPress" }, icon("external")) : null,
          j.status === "review" ? h("a", { class: "btn btn-secondary btn-sm", href: `/jobs/${j.id}` }, "Duyệt") : h("span", { class: "faint" }, icon("chevron-right"))));
      return tr;
    })))));
}

async function load() {
  try {
    jobs = (await api("GET", "/api/jobs?limit=300")).items;
    draw();
  } catch (e) { toastError(e); }
  const busy = jobs.some((j) => ["queued", "running"].includes(j.status));
  setTimeout(() => { if (!document.hidden) load(); else setTimeout(load, 3000); }, busy ? 2000 : 8000);
}
load();
