import { $, $$, api, h, icon, render, fmtVND, fmtNum, parseNum, debounce, toast, toastError, modal, thumbImg, withLoading, pathTail } from "./core.js";
import { categoryPicker } from "./components.js";

const MAX_URLS = 12;
const STORE_KEY = "pm-import-options";

const urlsEl = $("#urls");
const previewEl = $("#preview");
const startBtn = $("#start");
const startHelp = $("#start-help");
const discountEl = $("#discount");

let categories = [];
let lastPreview = null;
let previewSeq = 0;

// ---------------------------------------------------------------- remembered options
function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { return {}; }
}
function saveOptions() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      categories, review: $("#opt-review").checked, read_box: $("#opt-box").checked,
      insert_images: $("#opt-images").checked, include_specs: $("#opt-specs").checked,
    }));
  } catch (e) { /* ignore */ }
}
const saved = loadSaved();
for (const [id, key] of [["#opt-review", "review"], ["#opt-box", "read_box"], ["#opt-images", "insert_images"], ["#opt-specs", "include_specs"]]) {
  if (typeof saved[key] === "boolean") $(id).checked = saved[key];
  $(id).addEventListener("change", saveOptions);
}
categories = Array.isArray(saved.categories) ? saved.categories : [];
categoryPicker($("#categories"), categories, (v) => { categories = v; saveOptions(); });

// ---------------------------------------------------------------- status segmented
$$("#status button").forEach((b) => b.addEventListener("click", () => {
  $$("#status button").forEach((x) => x.classList.toggle("on", x === b));
}));
const currentStatus = () => $("#status button.on")?.dataset.value || "draft";

// ---------------------------------------------------------------- discount
discountEl.addEventListener("input", () => {
  const n = parseNum(discountEl.value);
  discountEl.value = n === null ? "" : fmtNum(n);
  drawPreview();
});
const discount = () => parseNum(discountEl.value) || 0;

// ---------------------------------------------------------------- urls + preview
function parseUrls() {
  const lines = urlsEl.value.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  return [...new Set(lines)];
}

function validUrl(u) {
  try {
    const url = new URL(u);
    return /(^|\.)flycampro\.vn$/.test(url.hostname) && url.pathname.includes("/products/");
  } catch (e) { return false; }
}

const runPreview = debounce(async () => {
  const urls = parseUrls();
  const seq = ++previewSeq;
  updateCount(urls);
  if (!urls.length) { lastPreview = null; render(previewEl); updateStart(); return; }
  const invalid = urls.filter((u) => !validUrl(u));
  if (invalid.length || urls.length > MAX_URLS) {
    lastPreview = null;
    render(previewEl, h("div", { class: "alert danger" }, icon("alert-circle"), h("div", { class: "alert-body" },
      urls.length > MAX_URLS ? h("b", null, `Tối đa ${MAX_URLS} link một lần`) : h("b", null, "Link không hợp lệ"),
      invalid.map((u) => h("span", { class: "small mono ellipsis" }, u)),
      invalid.length ? h("span", { class: "small" }, "Link phải có dạng https://flycampro.vn/products/…") : null)));
    updateStart();
    return;
  }
  render(previewEl, urls.map(() => h("div", { class: "preview-item" },
    h("div", { class: "thumb skeleton" }), h("div", { class: "grow stack-sm" }, h("div", { class: "skeleton", style: { height: "14px", width: "70%" } }), h("div", { class: "skeleton", style: { height: "12px", width: "40%" } })))));
  updateStart(true);
  try {
    const data = await api("POST", "/api/preview", { urls });
    if (seq !== previewSeq) return;
    lastPreview = data;
  } catch (e) {
    if (seq !== previewSeq) return;
    lastPreview = null;
    render(previewEl, h("div", { class: "alert danger" }, icon("alert-circle"), h("div", { class: "alert-body" }, e.message)));
  }
  drawPreview();
  updateStart();
}, 600);

function updateCount(urls) {
  $("#url-count").textContent = urls.length ? `${urls.length} link` : "";
}

function priceBlock(price) {
  if (!price) return h("div", { class: "price", title: "flycampro không hiển thị giá - nhập giá ở bước duyệt", style: { color: "var(--warning-text)" } }, "Liên hệ");
  const d = discount();
  const sale = d > 0 && d < price ? price - d : null;
  return h("div", { class: "price" }, sale ? [fmtVND(sale), h("s", null, fmtVND(price))] : fmtVND(price));
}

function drawPreview() {
  if (!lastPreview) return;
  const { items, plan } = lastPreview;
  const nodes = items.map((it) => it.ok
    ? h("div", { class: "preview-item" },
        thumbImg(it.thumb),
        h("div", { class: "grow", style: { minWidth: 0 } },
          h("div", { class: "title ellipsis" }, it.title),
          h("div", { class: "meta" },
            h("span", null, icon("image", "i-sm"), ` ${it.image_count} ảnh`),
            h("span", { class: it.spec_rows ? "" : "faint" }, icon("list", "i-sm"), it.spec_rows ? ` ${it.spec_rows} thông số` : " không có bảng thông số"),
            h("a", { href: it.url, target: "_blank", rel: "noopener", class: "faint" }, icon("external", "i-sm"), " nguồn"))),
        priceBlock(it.price_vnd))
    : h("div", { class: "preview-item error" },
        h("div", { class: "thumb thumb-empty" }, icon("alert-circle")),
        h("div", { class: "grow", style: { minWidth: 0 } },
          h("div", { class: "title ellipsis mono small" }, pathTail(it.url)),
          h("div", { class: "meta", style: { color: "var(--danger-text)" } }, it.error))));

  if (plan) {
    const variable = plan.kind === "variable";
    nodes.unshift(h("div", { class: "plan" },
      h("div", { class: "plan-icon" }, icon(variable ? "layers" : "box")),
      h("div", { class: "grow stack-sm", style: { gap: "6px" } },
        h("div", { class: "row-wrap" },
          h("h4", null, variable ? "Sẽ tạo 1 sản phẩm nhiều phiên bản" : "Sẽ tạo 1 sản phẩm đơn")),
        h("div", { class: "small muted" }, "Tên gốc: ", h("b", null, plan.title)),
        variable ? h("div", { class: "chips" }, plan.labels.map((l) => h("span", { class: "chip static" }, l))) : null,
        plan.warning ? h("div", { class: "small", style: { color: "var(--warning-text)" } }, icon("alert", "i-sm"), " ", plan.warning) : null,
        h("div", { class: "tiny faint mono" }, `SKU ${plan.sku}`))));
  }
  render(previewEl, nodes);
}

function updateStart(loading = false) {
  const urls = parseUrls();
  const ok = lastPreview && lastPreview.plan && lastPreview.items.every((i) => i.ok);
  startBtn.disabled = loading || !ok;
  startHelp.textContent = !urls.length ? "Dán ít nhất 1 link để bắt đầu"
    : loading ? "Đang kiểm tra link…"
    : !ok ? "Sửa các link bị lỗi ở bên trái"
    : $("#opt-review").checked ? "Bạn sẽ được duyệt nội dung trước khi đăng" : "Sản phẩm sẽ được đăng tự động khi xong";
}
$("#opt-review").addEventListener("change", () => updateStart());

urlsEl.addEventListener("input", runPreview);
urlsEl.addEventListener("paste", () => setTimeout(runPreview.flush, 0));

// ---------------------------------------------------------------- start
async function start(allowDuplicate = false) {
  const body = {
    urls: parseUrls(),
    allow_duplicate: allowDuplicate,
    options: {
      discount_vnd: discount(),
      status: currentStatus(),
      categories,
      review: $("#opt-review").checked,
      read_box: $("#opt-box").checked,
      insert_images: $("#opt-images").checked,
      include_specs: $("#opt-specs").checked,
      on_exists: $("#on-exists").value,
      force_scrape: $("#opt-force-scrape").checked,
      force_rewrite: $("#opt-force-rewrite").checked,
    },
  };
  if (body.options.status === "publish" && !body.options.review) {
    const go = await new Promise((resolve) => modal({
      title: "Đăng công khai ngay?",
      body: h("p", null, "Bạn đã tắt bước duyệt và chọn trạng thái 'Đăng ngay' - sản phẩm sẽ hiển thị cho khách ngay khi AI viết xong mà không qua kiểm tra."),
      actions: [{ label: "Để tôi xem lại", value: false }, { label: "Vẫn tiếp tục", value: true, cls: "btn-primary" }],
      onClose: resolve,
    }));
    if (!go) return;
  }
  try {
    const res = await api("POST", "/api/jobs", body);
    location.href = `/jobs/${res.id}`;
  } catch (e) {
    if (e.status === 409 && e.data.job_id) {
      modal({
        title: "Link này đang được xử lý",
        body: h("p", null, "Đã có một phiên nhập đang chạy hoặc chờ duyệt với ít nhất một link trong danh sách. Mở phiên đó để tiếp tục, hay vẫn tạo phiên mới?"),
        actions: [
          { label: "Tạo phiên mới", onClick: (close) => { close(); start(true); } },
          { label: "Mở phiên đang có", cls: "btn-primary", onClick: () => { location.href = `/jobs/${e.data.job_id}`; } },
        ],
      });
      return;
    }
    toastError(e);
  }
}
startBtn.addEventListener("click", () => withLoading(startBtn, () => start()));

// ---------------------------------------------------------------- prefill from ?urls=
const prefill = new URLSearchParams(location.search).get("urls");
if (prefill) {
  urlsEl.value = prefill.split(/[\s,]+/).filter(Boolean).join("\n");
  runPreview.flush();
} else {
  urlsEl.focus();
}
updateStart();
