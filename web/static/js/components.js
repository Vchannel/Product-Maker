// Reusable form widgets: category picker, tag input, rich text editor,
// editable string list, and VND price input.
import { api, h, icon, render, clear, fmtNum, parseNum, sanitizeHtml, setSafeHtml } from "./core.js";

// ---------------------------------------------------------------- categories
let categoriesPromise = null;
export function loadCategories(refresh = false) {
  if (!categoriesPromise || refresh) {
    categoriesPromise = api("GET", `/api/categories${refresh ? "?refresh=1" : ""}`).then((d) => d.items).catch((e) => {
      categoriesPromise = null;
      throw e;
    });
  }
  return categoriesPromise;
}

const norm = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/\u0111/gi, "d").toLowerCase();

export function categoryPicker(container, initial = [], onChange = () => {}) {
  let selected = initial.map((c) => ({ id: c.id ?? null, name: c.name || "", path: c.path || c.name || "" }));
  let items = null;
  let error = null;
  let open = false;
  let active = 0;

  const chips = h("div", { class: "chips" });
  const input = h("input", { placeholder: "Tìm danh mục…", autocomplete: "off", "aria-label": "Tìm danh mục" });
  const box = h("div", { class: "input tag-input", onclick: () => input.focus() }, chips, input);
  const menu = h("div", { class: "combo-menu", hidden: true, role: "listbox" });
  const wrap = h("div", { class: "combo" }, box, menu);
  render(container, wrap);

  const emit = () => onChange(selected.map(({ id, name }) => ({ id, name })));

  function drawChips() {
    render(chips, selected.map((c, i) =>
      h("span", { class: "chip accent", title: c.path },
        h("span", { class: "ellipsis" }, c.name),
        h("button", { type: "button", "aria-label": `Bỏ ${c.name}`, onclick: (e) => { e.stopPropagation(); selected.splice(i, 1); drawChips(); drawMenu(); emit(); } }, icon("x")))));
    input.placeholder = selected.length ? "" : "Tìm danh mục…";
  }

  function matches() {
    if (!items) return [];
    const q = norm(input.value.trim());
    return items.filter((c) => !q || norm(c.path).includes(q)).slice(0, 80);
  }

  function drawMenu() {
    if (!open) { menu.hidden = true; return; }
    menu.hidden = false;
    if (error) return render(menu, h("div", { class: "combo-empty" }, error));
    if (!items) return render(menu, h("div", { class: "combo-empty" }, icon("loader", "spin"), " Đang tải danh mục từ website…"));
    const list = matches();
    if (!list.length) return render(menu, h("div", { class: "combo-empty" }, "Không có danh mục phù hợp"));
    active = Math.min(active, list.length - 1);
    render(menu, list.map((c, i) => {
      const isOn = selected.some((s) => s.id === c.id);
      const parts = c.path.split(" › ");
      return h("div", {
        class: `combo-item ${i === active ? "active" : ""}`, role: "option", "aria-selected": String(isOn),
        onmousedown: (e) => { e.preventDefault(); toggle(c); },
      },
      h("span", { class: "grow ellipsis" }, parts.length > 1 ? h("span", { class: "path" }, parts.slice(0, -1).join(" › ") + " › ") : null, parts[parts.length - 1]),
      c.count ? h("span", { class: "tiny faint" }, c.count) : null,
      isOn ? h("span", { class: "check" }, icon("check", "i-sm")) : null);
    }));
  }

  function toggle(c) {
    const idx = selected.findIndex((s) => s.id === c.id);
    if (idx >= 0) selected.splice(idx, 1);
    else selected.push({ id: c.id, name: c.name, path: c.path });
    input.value = "";
    drawChips();
    drawMenu();
    emit();
  }

  input.addEventListener("focus", async () => {
    open = true;
    drawMenu();
    if (!items && !error) {
      try {
        items = await loadCategories();
        // Fill in names/paths for ids we only knew by name (and vice versa).
        selected = selected.map((s) => {
          const found = items.find((c) => (s.id && c.id === s.id) || (!s.id && norm(c.name) === norm(s.name)));
          return found ? { id: found.id, name: found.name, path: found.path } : s;
        });
        drawChips();
      } catch (e) {
        error = e.message;
      }
      drawMenu();
    }
  });
  input.addEventListener("blur", () => { open = false; drawMenu(); });
  input.addEventListener("input", () => { active = 0; drawMenu(); });
  input.addEventListener("keydown", (e) => {
    const list = matches();
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, list.length - 1); drawMenu(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); drawMenu(); }
    else if (e.key === "Enter") { e.preventDefault(); if (list[active]) toggle(list[active]); }
    else if (e.key === "Backspace" && !input.value && selected.length) { selected.pop(); drawChips(); emit(); }
    else if (e.key === "Escape") { input.blur(); }
  });

  drawChips();
  return { get value() { return selected.map(({ id, name }) => ({ id, name })); } };
}

// ---------------------------------------------------------------- tags
export function tagInput(container, initial = [], onChange = () => {}) {
  const tags = [...initial];
  const chips = h("div", { class: "chips" });
  const input = h("input", { placeholder: "Thêm tag rồi nhấn Enter", "aria-label": "Thêm tag" });
  const box = h("div", { class: "input tag-input", onclick: () => input.focus() }, chips, input);
  render(container, box);
  const draw = () => render(chips, tags.map((t, i) =>
    h("span", { class: "chip" }, t, h("button", { type: "button", "aria-label": `Xoá tag ${t}`, onclick: (e) => { e.stopPropagation(); tags.splice(i, 1); draw(); onChange([...tags]); } }, icon("x")))));
  const add = () => {
    const v = input.value.replace(/,/g, " ").trim();
    if (v && !tags.some((t) => t.toLowerCase() === v.toLowerCase())) { tags.push(v); onChange([...tags]); }
    input.value = "";
    draw();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    else if (e.key === "Backspace" && !input.value && tags.length) { tags.pop(); draw(); onChange([...tags]); }
  });
  input.addEventListener("blur", add);
  draw();
}

// ---------------------------------------------------------------- rich text
export function richEditor(container, html, onChange = () => {}, { tall = false, compact = false } = {}) {
  let sourceMode = false;
  const area = h("div", { class: `editor-area prose ${tall ? "tall" : ""}`, contenteditable: "true", spellcheck: "true" });
  setSafeHtml(area, html);
  const source = h("textarea", { class: "textarea editor-source", spellcheck: "false", hidden: true });

  const exec = (cmd, value = null) => {
    if (sourceMode) return;
    area.focus();
    document.execCommand(cmd, false, value);
    changed();
  };
  const btn = (name, title, fn) => h("button", { type: "button", title, "aria-label": title, onmousedown: (e) => e.preventDefault(), onclick: fn }, icon(name));
  const toggleSource = h("button", { type: "button", title: "Sửa HTML", "aria-label": "Sửa HTML", onclick: () => setSource(!sourceMode) }, icon("code"));

  const tools = [
    btn("bold", "In đậm (Ctrl+B)", () => exec("bold")),
    btn("italic", "In nghiêng (Ctrl+I)", () => exec("italic")),
    compact ? null : btn("heading", "Tiêu đề phụ", () => {
      const block = document.queryCommandValue("formatBlock").toLowerCase();
      exec("formatBlock", block === "h3" ? "<p>" : "<h3>");
    }),
    btn("list", "Danh sách gạch đầu dòng", () => exec("insertUnorderedList")),
    btn("link", "Chèn liên kết", () => {
      const url = prompt("Địa chỉ liên kết (https://…)");
      if (url && /^https?:\/\//i.test(url)) exec("createLink", url);
    }),
    h("span", { class: "sep" }),
    btn("eraser", "Xoá định dạng", () => { exec("removeFormat"); exec("formatBlock", "<p>"); }),
    h("span", { class: "spacer" }),
    toggleSource,
  ];
  const toolbar = h("div", { class: "editor-toolbar" }, tools);
  const root = h("div", { class: "editor" }, toolbar, area, source);
  render(container, root);

  function value() { return sourceMode ? source.value : area.innerHTML; }
  function changed() { onChange(value()); }
  function setSource(on) {
    sourceMode = on;
    toggleSource.classList.toggle("on", on);
    if (on) { source.value = prettyHtml(area.innerHTML); source.style.height = Math.max(280, area.offsetHeight) + "px"; }
    else setSafeHtml(area, source.value);
    area.hidden = on;
    source.hidden = !on;
    [...toolbar.querySelectorAll("button")].forEach((b) => { if (b !== toggleSource) b.disabled = on; });
  }

  area.addEventListener("input", changed);
  source.addEventListener("input", changed);
  area.addEventListener("paste", (e) => {
    // Paste as plain text so formatting from other sites doesn't sneak in.
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, text);
  });
  try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) { /* older browsers */ }

  return {
    get value() { return value(); },
    set value(v) { setSafeHtml(area, v); if (sourceMode) source.value = prettyHtml(sanitizeHtml(v)); },
  };
}

function prettyHtml(html) {
  return String(html).replace(/>\s*</g, ">\n<").replace(/\n(<\/?(?:li|strong|em|b|i|a|span|br)[^>]*>)/g, "$1");
}

export function textLength(html) {
  // Inert document: counting characters must never load images or run handlers.
  const body = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html").body;
  return (body.textContent || "").trim().length;
}

// ---------------------------------------------------------------- string list
export function itemList(container, initial = [], onChange = () => {}, { placeholder = "Tên phụ kiện", addLabel = "Thêm dòng" } = {}) {
  const items = [...initial];
  const list = h("div", { class: "item-list" });
  const addBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm", style: { alignSelf: "flex-start" }, onclick: () => { items.push(""); draw(items.length - 1); } }, icon("plus"), addLabel);
  render(container, h("div", { class: "stack-sm" }, list, addBtn));

  function draw(focusIndex = -1) {
    render(list, items.map((item, i) => {
      const input = h("input", { class: "input", value: item, placeholder });
      input.addEventListener("input", () => { items[i] = input.value; onChange(items.filter((x) => x.trim())); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); items.splice(i + 1, 0, ""); draw(i + 1); }
        else if (e.key === "Backspace" && !input.value && items.length > 1) { e.preventDefault(); items.splice(i, 1); onChange(items.filter((x) => x.trim())); draw(Math.max(0, i - 1)); }
      });
      if (i === focusIndex) queueMicrotask(() => input.focus());
      return h("div", { class: "item-row" },
        h("span", { class: "faint tiny num", style: { width: "18px", textAlign: "right" } }, i + 1),
        input,
        h("button", { type: "button", class: "btn btn-ghost btn-icon btn-sm", title: "Xoá dòng", "aria-label": "Xoá dòng", onclick: () => { items.splice(i, 1); onChange(items.filter((x) => x.trim())); draw(); } }, icon("x")));
    }));
    if (!items.length) list.appendChild(h("div", { class: "small faint" }, "Chưa có mục nào."));
  }
  draw();
}

// ---------------------------------------------------------------- price input
export function priceInput({ value, placeholder = "", onInput = () => {}, id } = {}) {
  const input = h("input", { class: "input num", inputmode: "numeric", id, placeholder, value: value ? fmtNum(value) : "" });
  input.addEventListener("input", () => {
    const n = parseNum(input.value);
    const caretFromEnd = input.value.length - input.selectionStart;
    input.value = n === null ? "" : fmtNum(n);
    const pos = Math.max(0, input.value.length - caretFromEnd);
    input.setSelectionRange(pos, pos);
    onInput(n);
  });
  return h("div", { class: "input-group" }, input, h("span", { class: "suffix" }, "đ"));
}

export { clear };
