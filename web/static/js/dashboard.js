import { $, api, h, icon, render, fmtNum, fmtVND, timeAgo, statusBadge, kindBadge, thumbImg, emptyState, toastError, pathTail } from "./core.js";

$("#quick-import").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = e.target.url.value.trim();
  location.href = url ? `/import?urls=${encodeURIComponent(url)}` : "/import";
});

function stat({ label, value, foot, iconName, tone, href }) {
  return h(href ? "a" : "div", { class: "card stat", href },
    h("div", { class: "stat-top" }, label, h("span", { class: `stat-icon ${tone || ""}` }, icon(iconName))),
    h("div", { class: "stat-value" }, fmtNum(value)),
    h("div", { class: "stat-foot" }, foot));
}

export function jobRow(job) {
  const title = job.title || job.urls.map(pathTail).join(", ");
  const meta = [job.urls.length > 1 ? `${job.urls.length} link` : pathTail(job.urls[0]), timeAgo(job.created_at)];
  return h("a", { class: "list-item", href: `/jobs/${job.id}` },
    thumbImg(job.thumb),
    h("div", { class: "grow" },
      h("div", { class: "title ellipsis" }, title),
      h("div", { class: "meta ellipsis" }, job.status === "failed" && job.error ? job.error : meta.join(" · "))),
    statusBadge(job.status));
}

function productImage(src) {
  const ph = () => h("div", { class: "ph" }, icon("image"));
  if (!src) return ph();
  const img = h("img", { src, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(ph()), { once: true });
  return img;
}

async function load() {
  let data;
  try {
    data = await api("GET", "/api/overview");
  } catch (e) {
    toastError(e);
    return;
  }
  const s = data.stats;
  render($("#stats"),
    stat({ label: "Sản phẩm đã đăng", value: s.products, foot: s.products_week ? `+${s.products_week} trong 7 ngày` : "Tổng số trên site", iconName: "package", tone: "accent", href: "/products" }),
    stat({ label: "Đang xử lý", value: s.active, foot: s.active ? "Đang chạy hoặc chờ tới lượt" : "Không có phiên nào đang chạy", iconName: "loader", tone: "info", href: "/jobs?tab=active" }),
    stat({ label: "Chờ duyệt", value: s.review, foot: s.review ? "Bản nháp đang chờ bạn kiểm tra" : "Không có bản nháp nào", iconName: "edit", tone: "warning", href: "/jobs?tab=review" }),
    stat({ label: "Bị lỗi", value: s.failed, foot: s.failed ? "Có thể thử lại từ bước đã xong" : "Mọi thứ đều ổn", iconName: "alert-circle", tone: "danger", href: "/jobs?tab=failed" }));

  const attention = data.attention;
  $("#attention-card").hidden = !attention.length;
  render($("#attention"), attention.map(jobRow));

  render($("#recent-jobs"), data.recent_jobs.length
    ? data.recent_jobs.map(jobRow)
    : emptyState("layers", "Chưa có phiên nhập nào", "Dán link sản phẩm flycampro.vn ở ô phía trên để bắt đầu.",
        h("a", { class: "btn btn-primary btn-sm", href: "/import" }, icon("plus"), "Nhập sản phẩm đầu tiên")));

  render($("#recent-products"), data.recent_products.length
    ? h("div", { class: "product-cards" }, data.recent_products.map((p) =>
        h("a", { class: "product-card", href: p.edit_link, target: "_blank", rel: "noopener" },
          productImage(p.thumb_url),
          h("div", { class: "pc-body" },
            h("div", { class: "pc-name" }, p.name),
            h("div", { class: "tiny faint" }, p.kind === "variable" ? `${(p.variations || []).length} phiên bản · từ ${fmtVND(p.price_min)}` : fmtVND(p.price_min))))))
    : emptyState("package", "Chưa có sản phẩm", "Sản phẩm bạn đăng sẽ xuất hiện ở đây."));
}

load();
setInterval(() => { if (!document.hidden) load(); }, 8000);
