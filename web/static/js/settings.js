import { $, $$, api, h, icon, render, toast, toastError, withLoading, fmtNum, parseNum } from "./core.js";

const form = $("#settings-form");
let loaded = null;

function markDirty() { $("#savebar").hidden = !isDirty(); }

function isDirty() {
  if (!loaded) return false;
  return Object.entries(collect()).some(([k, v]) => {
    const meta = loaded[k];
    if (!meta) return false;
    return meta.secret ? v !== "" : v !== (meta.value || "");
  });
}

function collect() {
  const values = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "radio") { if (el.checked) values[el.name] = el.value; continue; }
    values[el.name] = el.name === "PRICE_DISCOUNT_VND" ? String(parseNum(el.value) ?? "") : el.value.trim();
  }
  return values;
}

function fill(values) {
  loaded = values;
  for (const [key, meta] of Object.entries(values)) {
    const el = form.elements[key];
    if (!el) continue;
    if (el instanceof RadioNodeList || (el.length && el[0]?.type === "radio")) {
      let matched = false;
      for (const r of el) { r.checked = r.value === meta.value; matched ||= r.checked; }
      if (!matched && meta.value) {
        // A model not in the list (set manually in .env) - show it as its own choice.
        const card = h("label", { class: "radio-card on", "data-model": meta.value },
          h("input", { type: "radio", name: key, value: meta.value, checked: true }),
          h("div", null, h("b", null, meta.value), h("span", null, "Đang dùng (cấu hình thủ công)")));
        $("#model-choices").appendChild(card);
      }
      continue;
    }
    if (meta.secret) {
      el.value = "";
      el.placeholder = meta.set ? `${meta.value}  (đã lưu - nhập để thay)` : el.getAttribute("placeholder") || "";
      const stateEl = $(`[data-secret-state="${key}"]`);
      if (stateEl) render(stateEl, meta.set ? h("span", { class: "badge success" }, icon("check"), "Đã lưu") : h("span", { class: "badge warning" }, "Chưa có"));
    } else if (key === "PRICE_DISCOUNT_VND") {
      el.value = meta.value ? fmtNum(meta.value) : "";
    } else {
      el.value = meta.value || "";
    }
  }
  syncRadioCards();
  markDirty();
}

function syncRadioCards() {
  $$(".radio-card").forEach((c) => c.classList.toggle("on", c.querySelector("input").checked));
}

async function load() {
  try {
    const data = await api("GET", "/api/settings");
    fill(data.values);
    $("#env-path").textContent = `File cấu hình: ${data.env_path}`;
  } catch (e) { toastError(e); }
}

form.addEventListener("input", (e) => {
  if (e.target.name === "PRICE_DISCOUNT_VND") {
    const n = parseNum(e.target.value);
    e.target.value = n === null ? "" : fmtNum(n);
  }
  syncRadioCards();
  markDirty();
});
form.addEventListener("change", () => { syncRadioCards(); markDirty(); });

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  await withLoading($("#save"), async () => {
    try {
      const data = await api("PUT", "/api/settings", { values: collect() });
      fill(data.values);
      toast(data.missing.length ? `Đã lưu · còn thiếu: ${data.missing.join(", ")}` : "Đã lưu cài đặt", data.missing.length ? "warning" : "success");
    } catch (err) { toastError(err); }
  });
});

$("#discard").addEventListener("click", load);

$$("[data-reveal]").forEach((btn) => btn.addEventListener("click", () => {
  const input = form.elements[btn.dataset.reveal];
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  render(btn, icon(show ? "eye-off" : "eye", "i-sm"));
}));

$$("[data-test]").forEach((btn) => btn.addEventListener("click", async () => {
  const service = btn.dataset.test;
  const out = $(`#test-${service}`);
  if (isDirty()) {
    toast("Lưu cài đặt trước rồi mới kiểm tra kết nối", "warning");
    return;
  }
  await withLoading(btn, async () => {
    try {
      const res = await api("POST", "/api/settings/test", { service });
      render(out, h("div", { class: `test-result ${res.ok ? "ok" : "fail"}` }, icon(res.ok ? "check-circle" : "x-circle"), h("span", null, res.message)));
    } catch (e) {
      render(out, h("div", { class: "test-result fail" }, icon("x-circle"), h("span", null, e.message)));
    }
  });
}));

window.addEventListener("beforeunload", (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ""; } });

load();
