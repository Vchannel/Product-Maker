// Shared helpers for every page: API calls, safe DOM building, toasts,
// dialogs and Vietnamese formatting.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data || {};
  }
}

export async function api(method, url, body) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError("Không kết nối được tới ứng dụng. Cửa sổ chạy Product Maker có đang mở không?", 0);
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) throw new ApiError((data && data.error) || `Lỗi ${res.status}`, res.status, data);
  return data;
}

// h("div", {class: "x", onclick: fn}, child, "text", [children])
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function icon(name, cls = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", `i ${cls}`.trim());
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `${window.PM_ICONS}#${name}`);
  svg.appendChild(use);
  return svg;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function render(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

// ---------------------------------------------------------------- formatting
const vnd = new Intl.NumberFormat("vi-VN");
export const fmtVND = (n) => (n === null || n === undefined || n === "" ? "—" : `${vnd.format(Math.round(Number(n)))}đ`);
export const fmtNum = (n) => vnd.format(Number(n) || 0);
export const parseNum = (s) => {
  const digits = String(s ?? "").replace(/[^\d]/g, "");
  return digits ? parseInt(digits, 10) : null;
};

export function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "vừa xong";
  if (s < 3600) return `${Math.round(s / 60)} phút trước`;
  if (s < 86400) return `${Math.round(s / 3600)} giờ trước`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} ngày trước`;
  return fmtDate(ts);
}
export const fmtDate = (ts) =>
  new Date(ts * 1000).toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
export const fmtTime = (ts) =>
  new Date(ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.flush = (...args) => { clearTimeout(t); return fn(...args); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export const hostOf = (url) => { try { return new URL(url).host; } catch (e) { return url; } };
export const pathTail = (url) => { try { return new URL(url).pathname.split("/").filter(Boolean).pop() || url; } catch (e) { return url; } };

// ---------------------------------------------------------------- status
export const JOB_STATUS = {
  queued: { label: "Đang chờ", cls: "info", icon: "clock" },
  running: { label: "Đang xử lý", cls: "accent", icon: "loader" },
  review: { label: "Chờ duyệt", cls: "warning", icon: "edit" },
  done: { label: "Hoàn tất", cls: "success", icon: "check" },
  failed: { label: "Lỗi", cls: "danger", icon: "alert-circle" },
  cancelled: { label: "Đã huỷ", cls: "", icon: "x" },
};

export function statusBadge(status) {
  const s = JOB_STATUS[status] || { label: status, cls: "", icon: "info" };
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon, status === "running" ? "spin" : ""), s.label);
}

export const PRODUCT_STATUS = {
  publish: { label: "Đang bán", cls: "success" },
  draft: { label: "Nháp", cls: "" },
  pending: { label: "Chờ duyệt", cls: "warning" },
  private: { label: "Riêng tư", cls: "info" },
  trash: { label: "Thùng rác", cls: "danger" },
};

export function productStatusBadge(p) {
  if (p.remote_state === "missing") return h("span", { class: "badge danger dot-b" }, "Không còn trên site");
  const s = PRODUCT_STATUS[p.status];
  if (!s) return h("span", { class: "badge outline" }, "Chưa đồng bộ");
  return h("span", { class: `badge dot-b ${s.cls}` }, s.label);
}

export const kindBadge = (kind, n) =>
  kind === "variable"
    ? h("span", { class: "badge info" }, icon("layers"), n ? `${n} phiên bản` : "Nhiều phiên bản")
    : h("span", { class: "badge" }, icon("box"), "Sản phẩm đơn");

// ---------------------------------------------------------------- toasts
export function toast(message, type = "info", ms = 4200) {
  const box = document.getElementById("toasts");
  const icons = { success: "check-circle", error: "x-circle", info: "info", warning: "alert" };
  const el = h("div", { class: `toast ${type}`, role: type === "error" ? "alert" : "status" }, icon(icons[type] || "info"), h("div", { class: "grow" }, message));
  box.appendChild(el);
  const close = () => { el.classList.add("out"); setTimeout(() => el.remove(), 220); };
  el.addEventListener("click", close);
  setTimeout(close, ms);
}

export const toastError = (e) => toast(e && e.message ? e.message : String(e), "error", 6500);

// ---------------------------------------------------------------- dialogs
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const prevFocus = document.activeElement;
  const backdrop = h("div", { class: "modal-backdrop" });
  const close = (value) => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    if (onClose) onClose(value);
  };
  const onKey = (e) => { if (e.key === "Escape") close(null); };
  const foot = actions.length
    ? h("div", { class: "modal-foot" }, actions.map((a) =>
        h("button", { class: `btn ${a.cls || "btn-secondary"}`, onclick: () => (a.onClick ? a.onClick(close) : close(a.value)) }, a.icon ? icon(a.icon) : null, a.label)))
    : null;
  const dialog = h("div", { class: `modal ${wide ? "wide" : ""}`, role: "dialog", "aria-modal": "true" },
    h("div", { class: "modal-head" }, h("h3", null, title),
      h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Đóng", onclick: () => close(null) }, icon("x"))),
    h("div", { class: "modal-body" }, body),
    foot);
  backdrop.appendChild(dialog);
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(null); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(backdrop);
  const focusable = dialog.querySelector(".modal-foot .btn-primary, .modal-foot .btn-danger, .modal-foot .btn");
  if (focusable) focusable.focus();
  return close;
}

export function confirmDialog({ title, message, confirm = "Đồng ý", cancel = "Huỷ", danger = false }) {
  return new Promise((resolve) => {
    modal({
      title,
      body: typeof message === "string" ? h("p", null, message) : message,
      actions: [
        { label: cancel, value: false },
        { label: confirm, value: true, cls: danger ? "btn-danger" : "btn-primary" },
      ],
      onClose: (v) => resolve(Boolean(v)),
    });
  });
}

export async function withLoading(btn, fn) {
  if (!btn) return fn();
  btn.classList.add("loading");
  const original = [...btn.childNodes];
  const spinner = icon("loader", "spin");
  const firstIcon = btn.querySelector("svg");
  if (firstIcon) firstIcon.replaceWith(spinner); else btn.prepend(spinner);
  try {
    return await fn();
  } finally {
    btn.classList.remove("loading");
    render(btn, ...original);
  }
}

export function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast("Đã sao chép", "success", 1800), () => toast("Không sao chép được", "error"));
}

export function emptyState(iconName, title, text, action) {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon(iconName, "i-lg")),
    h("h3", null, title),
    text ? h("p", null, text) : null,
    action || null);
}

export function thumbImg(src, cls = "thumb") {
  if (!src) return h("div", { class: `${cls} thumb-empty` }, icon("image"));
  const img = h("img", { class: cls, src, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(h("div", { class: `${cls} thumb-empty` }, icon("image"))), { once: true });
  return img;
}

// Same allowlist as lib/html_utils.sanitize_html. Parsing happens in an inert
// DOMParser document (no scripts run, no images load), and only allowed
// elements/attributes are copied into the output.
const ALLOWED_TAGS = new Set(["P", "BR", "H2", "H3", "H4", "STRONG", "B", "EM", "I", "U", "UL", "OL", "LI", "A", "BLOCKQUOTE", "TABLE", "THEAD", "TBODY", "TR", "TD", "TH", "IMG", "SPAN"]);
const ALLOWED_ATTRS = { A: ["href", "title", "target", "rel"], IMG: ["src", "alt", "width", "height"], TD: ["colspan", "rowspan"], TH: ["colspan", "rowspan"], TABLE: ["class"] };
const DROP_TAGS = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "INPUT", "BUTTON", "SVG", "NOSCRIPT", "TEMPLATE"]);
const safeUrl = (u) => /^(https?:\/\/|\/(?!\/)|#|mailto:)/i.test(String(u || "").trim());

export function sanitizeHtml(html) {
  const src = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html").body;
  const out = document.createElement("div");
  const copy = (from, to) => {
    for (const node of from.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) { to.appendChild(document.createTextNode(node.nodeValue)); continue; }
      if (node.nodeType !== Node.ELEMENT_NODE) continue; // comments, CDATA, PIs
      const tag = node.tagName.toUpperCase();
      if (DROP_TAGS.has(tag)) continue;
      const name = tag === "DIV" ? "P" : tag;
      if (!ALLOWED_TAGS.has(name)) { copy(node, to); continue; }
      const el = document.createElement(name);
      for (const attr of ALLOWED_ATTRS[name] || []) {
        const v = node.getAttribute(attr);
        if (v === null) continue;
        if ((attr === "href" || attr === "src") && !safeUrl(v)) continue;
        el.setAttribute(attr, v);
      }
      if (name === "A" && el.getAttribute("target") === "_blank") el.setAttribute("rel", "noopener");
      copy(node, el);
      to.appendChild(el);
    }
  };
  copy(src, out);
  return out.innerHTML;
}

export function setSafeHtml(el, html) {
  el.innerHTML = sanitizeHtml(html);
  return el;
}

export function mediaUrl(ref, original = false) {
  return `/media/${ref}${original ? "?original=1" : ""}`;
}
