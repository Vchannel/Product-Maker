import { $, api, h, icon, render, fmtVND, timeAgo, fmtDate, kindBadge, productStatusBadge, thumbImg, emptyState, toast, toastError, withLoading, debounce, copyText } from "./core.js";

let items = [];
const norm = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/\u0111/gi, "d").toLowerCase();

function filtered() {
  const q = norm($("#q").value.trim());
  const kind = $("#kind").value;
  const status = $("#status").value;
  return items.filter((p) =>
    (!q || norm(p.name).includes(q) || norm(p.sku).includes(q)) &&
    (!kind || p.kind === kind) &&
    (!status || (status === "missing" ? p.remote_state === "missing" : p.status === status && p.remote_state !== "missing")));
}

function priceText(p) {
  if (p.kind === "variable" && p.price_min !== p.price_max) return `${fmtVND(p.price_min)} – ${fmtVND(p.price_max)}`;
  return fmtVND(p.sale_price || p.price_min);
}

function draw() {
  const list = filtered();
  if (!items.length) {
    render($("#products"), emptyState("package", "Chưa có sản phẩm nào", "Sản phẩm bạn nhập và đăng thành công sẽ được liệt kê ở đây.",
      h("a", { class: "btn btn-primary btn-sm", href: "/import" }, icon("plus"), "Nhập sản phẩm")));
    return;
  }
  if (!list.length) {
    render($("#products"), emptyState("search", "Không tìm thấy sản phẩm phù hợp", "Thử từ khoá hoặc bộ lọc khác."));
    return;
  }
  render($("#products"), h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", null, h("tr", null,
      h("th", null, "Sản phẩm"), h("th", { class: "hide-sm" }, "Loại"), h("th", { class: "right" }, "Giá"),
      h("th", null, "Trạng thái"), h("th", { class: "hide-sm" }, "Cập nhật"), h("th"))),
    h("tbody", null, list.map((p) => h("tr", null,
      h("td", null, h("div", { class: "row", style: { minWidth: "260px" } }, thumbImg(p.thumb_url),
        h("div", { class: "grow", style: { minWidth: 0 } },
          h("a", { class: "strong", href: p.edit_link, target: "_blank", rel: "noopener", style: { color: "var(--text)", display: "block" } }, p.name),
          h("div", { class: "tiny faint row", style: { gap: "6px" } },
            h("span", null, `#${p.product_id}`), "·",
            h("button", { class: "mono faint", style: { border: 0, background: "none", padding: 0, cursor: "copy" }, title: "Sao chép SKU", onclick: () => copyText(p.sku) }, p.sku))))),
      h("td", { class: "hide-sm" }, kindBadge(p.kind, (p.variations || []).length)),
      h("td", { class: "right num nowrap strong" }, priceText(p)),
      h("td", null, productStatusBadge(p)),
      h("td", { class: "hide-sm small muted nowrap", title: fmtDate(p.updated_at) }, timeAgo(p.updated_at)),
      h("td", { class: "right nowrap" },
        p.job_id ? h("a", { class: "btn btn-ghost btn-sm btn-icon", href: `/jobs/${p.job_id}`, title: "Xem phiên nhập" }, icon("layers")) : null,
        p.permalink ? h("a", { class: "btn btn-ghost btn-sm btn-icon", href: p.permalink, target: "_blank", rel: "noopener", title: "Xem trên cửa hàng" }, icon("store")) : null,
        h("a", { class: "btn btn-secondary btn-sm", href: p.edit_link, target: "_blank", rel: "noopener" }, icon("wordpress"), "Sửa"))))))));
}

async function load() {
  try {
    const data = await api("GET", "/api/products");
    items = data.items;
    const synced = Math.max(0, ...items.map((p) => p.synced_at || 0));
    $("#sync-info").textContent = synced ? `Đồng bộ lần cuối ${timeAgo(synced)}` : "";
    draw();
    return data;
  } catch (e) { toastError(e); }
}

async function sync(btn, silent = false) {
  await withLoading(btn, async () => {
    try {
      const data = await api("POST", "/api/products/sync", {});
      items = data.items;
      $("#sync-info").textContent = "Vừa đồng bộ";
      draw();
      if (!silent) toast("Đã cập nhật trạng thái từ website", "success");
    } catch (e) { if (!silent) toastError(e); }
  });
}

$("#sync").addEventListener("click", (e) => sync(e.currentTarget));
$("#q").addEventListener("input", debounce(draw, 120));
$("#kind").addEventListener("change", draw);
$("#status").addEventListener("change", draw);

load().then((data) => {
  // First visit after products were added by an older version: fetch live status once.
  if (data && data.items.length && data.items.every((p) => !p.synced_at)) sync($("#sync"), true);
});
