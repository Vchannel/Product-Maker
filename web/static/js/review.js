// Draft review editor: everything that will be sent to WooCommerce, editable,
// autosaved, with a storefront-like preview before publishing.
import { $, $$, api, h, icon, render, fmtVND, fmtNum, debounce, toast, toastError, modal, confirmDialog, withLoading, mediaUrl, hostOf, sanitizeHtml, setSafeHtml } from "./core.js";
import { categoryPicker, tagInput, richEditor, itemList, priceInput, textLength } from "./components.js";

const TITLE_SOFT_MAX = 90;
const STATUSES = window.PM_STATUSES || [["draft", "Nháp"], ["pending", "Chờ duyệt"], ["publish", "Đăng ngay"]];

export function mountReview(container, job, { onPublish, onRegenerate }) {
  const draft = structuredClone(job.draft);
  const variable = draft.kind === "variable";
  const state = { dirty: false, saving: false, error: "" };

  // ------------------------------------------------------------ autosave
  const saveStateEl = h("span", { class: "save-state" });
  function drawSaveState() {
    saveStateEl.className = `save-state ${state.error ? "error" : state.dirty ? "dirty" : ""}`;
    render(saveStateEl,
      state.saving ? [icon("loader", "spin i-sm"), "Đang lưu…"]
      : state.error ? [icon("alert-circle", "i-sm"), state.error]
      : state.dirty ? [icon("edit", "i-sm"), "Có thay đổi chưa lưu"]
      : [icon("check", "i-sm"), "Đã lưu bản nháp"]);
  }
  let inflight = null;
  let editsSinceSave = 0;
  async function save() {
    // Serialize saves: a publish click during an autosave waits for it, then
    // saves again so the server always ends up with the latest edits.
    while (inflight) await inflight;
    const revision = editsSinceSave;
    state.saving = true;
    drawSaveState();
    inflight = (async () => {
      try {
        await api("PUT", `/api/jobs/${job.id}/draft`, { draft: structuredClone(draft) });
        if (editsSinceSave === revision) state.dirty = false;
        state.error = "";
        return true;
      } catch (e) {
        state.error = e.message;
        return false;
      }
    })();
    const ok = await inflight;
    inflight = null;
    state.saving = false;
    drawSaveState();
    drawSummary();
    return ok;
  }
  const saveLater = debounce(save, 1200);
  function changed() {
    editsSinceSave++;
    state.dirty = true;
    state.error = "";
    drawSaveState();
    drawSummary();
    saveLater();
  }

  // ------------------------------------------------------------ content card
  const titleCounter = h("span", { class: "counter" });
  const titleInput = h("input", { class: "input input-lg", value: draft.title, maxlength: "200" });
  const drawTitleCounter = () => {
    titleCounter.textContent = `${draft.title.length}/${TITLE_SOFT_MAX}`;
    titleCounter.classList.toggle("over", draft.title.length > TITLE_SOFT_MAX);
  };
  titleInput.addEventListener("input", () => { draft.title = titleInput.value; drawTitleCounter(); changed(); });
  drawTitleCounter();

  const shortHost = h("div");
  const descHost = h("div");
  const descCounter = h("span", { class: "counter" });
  richEditor(shortHost, draft.short_description, (v) => { draft.short_description = v; changed(); }, { compact: true });
  const drawDescCounter = () => { descCounter.textContent = `${fmtNum(textLength(draft.description))} ký tự`; };
  richEditor(descHost, draft.description, (v) => { draft.description = v; drawDescCounter(); changed(); }, { tall: true });
  drawDescCounter();

  const optSwitch = (key, title, text) => {
    const input = h("input", { type: "checkbox", checked: draft[key] });
    input.addEventListener("change", () => { draft[key] = input.checked; changed(); });
    return h("label", { class: "switch" }, input, h("span", { class: "track" }), h("span", { class: "text" }, h("b", null, title), h("span", null, text)));
  };

  const existingAlert = draft.existing ? h("div", { class: "alert warning" }, icon("alert"),
    h("div", { class: "alert-body" },
      h("b", null, `Sản phẩm này đã có trên website (#${draft.existing.id})`),
      h("span", null, `"${draft.existing.name}" · trạng thái ${draft.existing.status}. Chọn cách xử lý ở khung "Đăng sản phẩm" bên phải.`),
      h("div", { class: "alert-actions" }, h("a", { class: "btn btn-secondary btn-sm", href: draft.existing.edit_link, target: "_blank", rel: "noopener" }, icon("external"), "Xem sản phẩm hiện có")))) : null;

  const contentCard = h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h2", null, icon("sparkles"), "Nội dung"),
      h("button", { class: "btn btn-ghost btn-sm", title: "Gọi AI viết lại bản mới", onclick: (e) => regenerate(e.currentTarget) }, icon("wand"), "Viết lại bằng AI")),
    h("div", { class: "card-body stack" },
      existingAlert,
      h("div", { class: "field" },
        h("label", { class: "label" }, h("span", null, "Tên sản phẩm"), titleCounter),
        titleInput,
        h("div", { class: "help" }, "Tên gốc: ", h("span", { class: "strong" }, draft.source_title))),
      h("div", { class: "field" },
        h("span", { class: "label" }, "Mô tả ngắn", h("span", { class: "opt" }, "hiển thị cạnh giá")),
        shortHost),
      h("div", { class: "field" },
        h("span", { class: "label" }, "Mô tả chi tiết", descCounter),
        descHost,
        h("div", { class: "grid grid-2", style: { gap: "0 16px" } },
          optSwitch("insert_images", "Chèn ảnh vào mô tả", "Rải ảnh gallery giữa các đoạn"),
          optSwitch("include_specs", "Thêm bảng thông số", draft.spec_sections?.length ? `${draft.spec_sections.reduce((n, s) => n + s.rows.length, 0)} dòng, giữ nguyên số liệu gốc` : "Trang gốc không có bảng thông số"))),
      h("div", { class: "tiny faint" }, `Viết bởi AI · ${draft.model || "Claude"} · luôn kiểm tra lại số liệu trước khi đăng`)));

  // ------------------------------------------------------------ images
  const galleryEl = h("div", { class: "img-grid" });
  const poolEl = h("div", { class: "img-grid" });
  const poolWrap = h("details", { class: "disclosure" }, h("summary", null, icon("chevron-right", "i-sm"), "Thêm ảnh từ các link khác"), h("div", { class: "mt-12" }, poolEl));
  const allImages = Object.values(draft.available_images || {}).flat();
  const logoInfo = Object.fromEntries(allImages.map((i) => [i.id, i.logo_removed]));

  function compareImage(ref) {
    modal({
      title: "So sánh ảnh",
      wide: true,
      body: h("div", { class: "stack-sm" },
        h("div", { class: "compare" },
          h("figure", null, h("img", { src: mediaUrl(ref, true), alt: "Ảnh gốc" }), h("figcaption", null, "Ảnh gốc từ flycampro.vn")),
          h("figure", null, h("img", { src: mediaUrl(ref), alt: "Ảnh sẽ đăng" }), h("figcaption", null, logoInfo[ref] ? "Đã xoá logo - ảnh sẽ được đăng" : "Không phát hiện logo - giữ nguyên"))),
        h("p", { class: "small" }, "Nếu ảnh sau khi xử lý bị lỗi, hãy bỏ ảnh đó khỏi sản phẩm.")),
      actions: [{ label: "Đóng", value: null }],
    });
  }

  let dragFrom = -1;
  function drawGallery() {
    render(galleryEl, draft.images.map((ref, i) => {
      const tile = h("div", { class: `img-tile ${i === 0 ? "is-main" : ""}`, draggable: "true", tabindex: "0", title: "Kéo để sắp xếp" },
        h("img", { src: mediaUrl(ref), alt: "", loading: "lazy" }),
        i === 0 ? h("span", { class: "ribbon" }, "Ảnh đại diện") : null,
        h("span", { class: "idx" }, i + 1),
        h("div", { class: "tools" },
          i > 0 ? h("button", { type: "button", title: "Đặt làm ảnh đại diện", "aria-label": "Đặt làm ảnh đại diện", onclick: () => { draft.images.unshift(draft.images.splice(i, 1)[0]); drawGallery(); changed(); } }, icon("star")) : null,
          h("button", { type: "button", title: "So sánh với ảnh gốc", "aria-label": "So sánh với ảnh gốc", onclick: () => compareImage(ref) }, icon("eye")),
          h("button", { type: "button", title: "Bỏ ảnh này", "aria-label": "Bỏ ảnh này", onclick: () => {
            if (draft.images.length === 1) return toast("Sản phẩm cần ít nhất 1 ảnh", "warning");
            draft.images.splice(i, 1); drawGallery(); changed();
          } }, icon("trash"))));
      tile.addEventListener("dragstart", (e) => { dragFrom = i; tile.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); });
      tile.addEventListener("dragend", () => { tile.classList.remove("dragging"); $$(".drop-before", galleryEl).forEach((t) => t.classList.remove("drop-before")); });
      tile.addEventListener("dragover", (e) => { e.preventDefault(); $$(".drop-before", galleryEl).forEach((t) => t.classList.remove("drop-before")); if (i !== dragFrom) tile.classList.add("drop-before"); });
      tile.addEventListener("drop", (e) => {
        e.preventDefault();
        if (dragFrom < 0 || dragFrom === i) return;
        const [moved] = draft.images.splice(dragFrom, 1);
        draft.images.splice(dragFrom < i ? i - 1 : i, 0, moved);
        dragFrom = -1;
        drawGallery();
        changed();
      });
      tile.addEventListener("keydown", (e) => {
        if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && e.altKey) {
          const j = i + (e.key === "ArrowLeft" ? -1 : 1);
          if (j < 0 || j >= draft.images.length) return;
          [draft.images[i], draft.images[j]] = [draft.images[j], draft.images[i]];
          drawGallery(); changed();
          galleryEl.children[j]?.focus();
        }
      });
      return tile;
    }));
    const pool = allImages.filter((img) => !draft.images.includes(img.id));
    poolWrap.hidden = !pool.length;
    render(poolEl, pool.map((img) => h("div", { class: "img-tile pool", title: "Thêm vào sản phẩm", tabindex: "0", role: "button",
      onclick: () => { draft.images.push(img.id); drawGallery(); changed(); },
      onkeydown: (e) => { if (e.key === "Enter") { draft.images.push(img.id); drawGallery(); changed(); } } },
      h("img", { src: mediaUrl(img.id), alt: "", loading: "lazy" }), h("span", { class: "add" }, icon("plus", "i-sm")))));
    const count = $(".img-count", imagesCard);
    if (count) count.textContent = `${draft.images.length} ảnh`;
  }

  const imagesCard = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, icon("image"), "Hình ảnh ", h("span", { class: "hint img-count" })), h("span", { class: "hint" }, "Kéo thả để sắp xếp · ảnh đầu là ảnh đại diện")),
    h("div", { class: "card-body stack" }, galleryEl, poolWrap));
  drawGallery();

  // ------------------------------------------------------------ prices / variants
  function discountNote(obj) {
    const el = h("div", { class: "help" });
    const draw = () => {
      if (obj.sale_price && obj.regular_price && obj.sale_price < obj.regular_price) {
        const pct = (1 - obj.sale_price / obj.regular_price) * 100;
        const pctText = pct < 1 ? pct.toLocaleString("vi-VN", { maximumFractionDigits: 1 }) : Math.round(pct);
        el.textContent = `Giảm ${fmtVND(obj.regular_price - obj.sale_price)} (−${pctText}%)`;
        el.style.color = "var(--success-text)";
      } else if (obj.sale_price && obj.sale_price >= obj.regular_price) {
        el.textContent = "Giá khuyến mãi phải nhỏ hơn giá gốc";
        el.style.color = "var(--danger-text)";
      } else {
        el.textContent = "Không có giá khuyến mãi";
        el.style.color = "";
      }
    };
    draw();
    return { el, draw };
  }

  function priceFields(obj) {
    const note = discountNote(obj);
    return h("div", { class: "stack-sm" },
      h("div", { class: "grid grid-2", style: { gap: "12px" } },
        h("div", { class: "field" }, h("span", { class: "label" }, "Giá gốc"),
          priceInput({ value: obj.regular_price, onInput: (n) => { obj.regular_price = n; note.draw(); changed(); } })),
        h("div", { class: "field" }, h("span", { class: "label" }, "Giá khuyến mãi", h("span", { class: "opt" }, "để trống = không giảm")),
          priceInput({ value: obj.sale_price, onInput: (n) => { obj.sale_price = n; note.draw(); changed(); } }))),
      note.el);
  }

  let pricingCard;
  if (!variable) {
    const boxHost = h("div");
    itemList(boxHost, draft.box_items || [], (items) => { draft.box_items = items; changed(); });
    pricingCard = h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", null, icon("tag"), "Giá & phụ kiện")),
      h("div", { class: "card-body stack" },
        priceFields(draft),
        h("div", { class: "field" },
          h("span", { class: "label" }, "Trong hộp có gì", h("span", { class: "opt" }, "thêm vào cuối mô tả")),
          boxHost)));
  } else {
    pricingCard = h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", null, icon("layers"), `Phiên bản (${draft.variants.length})`), h("span", { class: "hint" }, "Khách chọn ở thuộc tính “Phiên bản”")),
      h("div", { class: "card-body" }, draft.variants.map((v) => variantBlock(v))));
  }

  function variantBlock(v) {
    const headImg = h("img", { src: mediaUrl(v.image), alt: "" });
    const headLabel = h("div", { class: "strong ellipsis" }, v.label);
    const labelInput = h("input", { class: "input", value: v.label });
    labelInput.addEventListener("input", () => { v.label = labelInput.value; headLabel.textContent = v.label || "(chưa đặt tên)"; changed(); });
    const skuInput = h("input", { class: "input mono small", value: v.sku });
    skuInput.addEventListener("input", () => { v.sku = skuInput.value.trim(); changed(); });

    const choices = [...new Set([...(draft.available_images?.[v.slug] || []).map((i) => i.id), ...draft.images])];
    const thumbs = h("div", { class: "variant-thumbs" });
    const drawThumbs = () => render(thumbs, choices.map((ref) => h("div", {
      class: `img-tile selectable ${ref === v.image ? "selected" : ""}`, role: "button", tabindex: "0", title: "Chọn làm ảnh của phiên bản",
      onclick: () => { v.image = ref; headImg.src = mediaUrl(ref); drawThumbs(); changed(); },
    }, h("img", { src: mediaUrl(ref), alt: "", loading: "lazy" }))));
    drawThumbs();

    const boxHost = h("div");
    itemList(boxHost, v.box_items || [], (items) => { v.box_items = items; changed(); });

    return h("div", { class: "variant" },
      h("div", { class: "variant-head" }, headImg,
        h("div", { class: "grow", style: { minWidth: 0 } }, headLabel,
          h("a", { class: "tiny faint ellipsis", href: v.source_url, target: "_blank", rel: "noopener", style: { display: "block" } }, v.source_title)),
        h("span", { class: "badge outline num" }, fmtVND(v.regular_price))),
      h("div", { class: "variant-body" },
        h("div", { class: "grid grid-2", style: { gap: "12px" } },
          h("div", { class: "field" }, h("span", { class: "label" }, "Tên phiên bản"), labelInput),
          h("div", { class: "field" }, h("span", { class: "label" }, "SKU"), skuInput)),
        priceFields(v),
        h("div", { class: "field" }, h("span", { class: "label" }, "Ảnh của phiên bản"), thumbs),
        h("div", { class: "field" }, h("span", { class: "label" }, "Trong hộp có gì"), boxHost)));
  }

  // ------------------------------------------------------------ specs
  const specRows = (draft.spec_sections || []).reduce((n, s) => n + s.rows.length, 0);
  const specsCard = specRows ? h("section", { class: "card" },
    h("details", null,
      h("summary", { class: "card-head", style: { cursor: "pointer", listStyle: "none", borderBottom: "0" } },
        h("h2", null, icon("list"), "Thông số kỹ thuật ", h("span", { class: "hint" }, `${draft.spec_sections.length} nhóm · ${specRows} dòng`)),
        h("span", { class: "hint" }, "Bấm để xem")),
      h("div", { class: "card-body prose", style: { borderTop: "1px solid var(--border)" } }, specsTable(draft.spec_sections)))) : null;

  // ------------------------------------------------------------ sidebar
  const statusSeg = h("div", { class: "segmented", role: "radiogroup" }, STATUSES.map(([value, label]) => {
    const b = h("button", { type: "button", class: value === draft.status ? "on" : "", role: "radio", onclick: () => {
      draft.status = value;
      $$("button", statusSeg).forEach((x) => x.classList.toggle("on", x === b));
      publishBtnLabel();
      changed();
    } }, label.split(" (")[0]);
    return b;
  }));

  const catHost = h("div");
  categoryPicker(catHost, draft.categories || [], (v) => { draft.categories = v; changed(); });
  const tagHost = h("div");
  tagInput(tagHost, draft.tags || [], (v) => { draft.tags = v; changed(); });

  const existsChoice = draft.existing ? h("div", { class: "field" },
    h("span", { class: "label" }, "Sản phẩm đã tồn tại"),
    ["skip", "update"].map((value) => {
      const input = h("input", { type: "radio", name: "on_exists", value, checked: draft.on_exists === value });
      const card = h("label", { class: `radio-card ${draft.on_exists === value ? "on" : ""}` }, input,
        h("div", null,
          h("b", null, value === "skip" ? "Giữ nguyên nội dung" : "Cập nhật toàn bộ"),
          h("span", null, value === "skip" ? (variable ? "Chỉ thêm phiên bản còn thiếu" : "Không thay đổi gì trên site") : "Ghi đè tên, mô tả, ảnh, giá")));
      input.addEventListener("change", () => {
        draft.on_exists = value;
        $$(".radio-card", existsChoice).forEach((c) => c.classList.toggle("on", c === card));
        publishBtnLabel();
        changed();
      });
      return card;
    })) : null;

  const skuInput = h("input", { class: "input mono small", value: draft.sku });
  skuInput.addEventListener("input", () => { draft.sku = skuInput.value.trim(); changed(); });

  const summaryEl = h("div");
  function drawSummary() {
    const prices = variable ? draft.variants.map((v) => v.sale_price || v.regular_price).filter(Boolean) : [draft.sale_price || draft.regular_price];
    const min = Math.min(...prices), max = Math.max(...prices);
    render(summaryEl,
      h("div", { class: "summary-row" }, h("span", null, "Loại"), h("span", null, variable ? `Nhiều phiên bản (${draft.variants.length})` : "Sản phẩm đơn")),
      h("div", { class: "summary-row" }, h("span", null, "Giá bán"), h("span", { class: "num", style: { color: "var(--accent-text)" } }, prices.length ? (min === max ? fmtVND(min) : `${fmtVND(min)} – ${fmtVND(max)}`) : "—")),
      h("div", { class: "summary-row" }, h("span", null, "Ảnh"), h("span", null, `${draft.images.length} ảnh`)),
      h("div", { class: "summary-row" }, h("span", null, "Website"), h("span", { class: "ellipsis" }, hostOf(document.querySelector(".site-pill")?.href || "") || "—")));
  }
  drawSummary();

  const publishBtn = h("button", { class: "btn btn-primary btn-lg btn-block", onclick: (e) => publish(e.currentTarget) });
  function publishBtnLabel() {
    const text = draft.existing ? (draft.on_exists === "update" ? "Cập nhật lên website" : variable ? "Bổ sung lên website" : "Xác nhận") : draft.status === "publish" ? "Đăng & mở bán" : "Đăng lên WooCommerce";
    render(publishBtn, icon("send"), text);
  }
  publishBtnLabel();

  const sidebar = h("aside", { class: "stack sticky" },
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", null, icon("upload"), "Đăng sản phẩm")),
      h("div", { class: "card-body" },
        h("div", { class: "field" }, h("span", { class: "label" }, "Trạng thái"), statusSeg),
        h("div", { class: "field" }, h("span", { class: "label" }, "Danh mục"), catHost),
        h("div", { class: "field" }, h("span", { class: "label" }, "Tag"), tagHost),
        existsChoice,
        h("details", { class: "disclosure mt-16" },
          h("summary", null, icon("chevron-right", "i-sm"), "Nâng cao"),
          h("div", { class: "field mt-12" }, h("span", { class: "label" }, "SKU sản phẩm"), skuInput,
            h("div", { class: "help" }, "Dùng để nhận biết sản phẩm đã nhập, tránh tạo trùng."))),
        h("div", { class: "mt-16" }, summaryEl)),
      h("div", { class: "card-foot stack-sm" },
        publishBtn,
        h("button", { class: "btn btn-secondary btn-block", onclick: () => showPreview() }, icon("eye"), "Xem trước"),
        h("div", { style: { textAlign: "center" } }, saveStateEl))));
  drawSaveState();

  render(container, h("div", { class: "split" },
    h("div", { class: "stack" }, contentCard, imagesCard, pricingCard, specsCard),
    sidebar));

  // ------------------------------------------------------------ actions
  async function publish(btn) {
    saveLater.cancel();
    const ok = await withLoading(btn, save);
    if (!ok) {
      toast(state.error || "Bản nháp chưa hợp lệ", "error", 6000);
      return;
    }
    const isPublic = draft.status === "publish";
    const confirmed = await confirmDialog({
      title: draft.existing ? "Xác nhận đẩy lên website" : isPublic ? "Đăng và mở bán ngay?" : "Đăng sản phẩm lên WooCommerce?",
      message: h("div", { class: "stack-sm" },
        h("p", null, h("b", null, draft.title)),
        h("p", null, draft.existing
          ? (draft.on_exists === "update" ? "Nội dung, ảnh và giá của sản phẩm hiện có sẽ bị ghi đè." : "Nội dung sản phẩm hiện có được giữ nguyên; chỉ bổ sung phần còn thiếu.")
          : isPublic ? "Sản phẩm sẽ hiển thị công khai cho khách ngay sau khi tạo." : `Sản phẩm sẽ được tạo với trạng thái "${STATUSES.find((s) => s[0] === draft.status)?.[1] || draft.status}".`),
        h("p", { class: "small faint" }, `${draft.images.length} ảnh sẽ được upload (ảnh đã có trên site sẽ được dùng lại).`)),
      confirm: draft.existing ? "Tiếp tục" : isPublic ? "Đăng & mở bán" : "Đăng",
    });
    if (!confirmed) return;
    await withLoading(btn, async () => {
      try {
        await api("POST", `/api/jobs/${job.id}/publish`, { draft });
        toast("Đang đăng sản phẩm…", "info");
        onPublish();
      } catch (e) { toastError(e); }
    });
  }

  async function regenerate(btn) {
    const ok = await confirmDialog({
      title: "Viết lại nội dung bằng AI?",
      message: "AI sẽ viết một bản hoàn toàn mới cho tên, mô tả và danh sách phụ kiện. Những chỉnh sửa bạn đã làm trên bản nháp này sẽ bị thay thế.",
      confirm: "Viết lại",
    });
    if (!ok) return;
    await withLoading(btn, async () => {
      try {
        saveLater.cancel();
        state.dirty = false;
        await api("POST", `/api/jobs/${job.id}/regenerate`, {});
        toast("AI đang viết bản mới…", "info");
        onRegenerate();
      } catch (e) { toastError(e); }
    });
  }

  function showPreview() {
    const priceNode = variable
      ? (() => {
          const prices = draft.variants.map((v) => v.sale_price || v.regular_price);
          return h("div", { class: "sp-price" }, `${fmtVND(Math.min(...prices))} – ${fmtVND(Math.max(...prices))}`);
        })()
      : h("div", { class: "sp-price" }, fmtVND(draft.sale_price || draft.regular_price), draft.sale_price ? h("s", null, fmtVND(draft.regular_price)) : null);

    const desc = h("div", { class: "prose" });
    setSafeHtml(desc, interleave(sanitizeHtml(draft.description), draft.insert_images ? draft.images.map((r) => mediaUrl(r)) : []));
    if (!variable && draft.box_items?.length) desc.appendChild(boxList(draft.box_items));
    if (draft.include_specs && draft.spec_sections?.length) {
      desc.appendChild(h("h3", null, "Thông số kỹ thuật"));
      desc.appendChild(specsTable(draft.spec_sections));
    }
    const short = h("div", { class: "prose small" });
    setSafeHtml(short, draft.short_description);

    modal({
      title: "Xem trước trên cửa hàng",
      wide: true,
      body: h("div", { class: "store-preview" },
        h("div", { class: "grid grid-2", style: { gap: "24px", alignItems: "start" } },
          h("img", { src: mediaUrl(draft.images[0]), alt: "", style: { width: "100%", aspectRatio: "1", objectFit: "contain", border: "1px solid #eee", borderRadius: "10px" } }),
          h("div", { class: "stack-sm" },
            h("h1", null, draft.title),
            priceNode,
            short,
            variable ? h("div", { class: "stack-sm" }, h("b", { class: "small" }, "Phiên bản:"),
              h("div", { class: "chips" }, draft.variants.map((v) => h("span", { class: "chip static" }, `${v.label} · ${fmtVND(v.sale_price || v.regular_price)}`)))) : null)),
        h("hr"),
        desc),
      actions: [{ label: "Đóng", value: null }],
    });
  }

  return {
    get dirty() { return state.dirty; },
  };
}

// ------------------------------------------------------------ helpers
function specsTable(sections) {
  const frag = h("div");
  for (const s of sections) {
    if (!s.rows?.length) continue;
    frag.appendChild(h("h4", null, s.heading));
    frag.appendChild(h("table", null, h("tbody", null, s.rows.map(([k, v]) =>
      h("tr", null, h("td", null, multiline(k)), h("td", null, multiline(v)))))));
  }
  return frag;
}

function multiline(text) {
  const parts = String(text).split("\n");
  return parts.flatMap((p, i) => (i ? [h("br"), p] : [p]));
}

function boxList(items) {
  return h("div", null, h("p", null, h("strong", null, "Trong hộp có gì:")), h("ul", null, items.map((i) => h("li", null, i))));
}

function interleave(html, urls) {
  // Works on an inert parsed document; the caller sanitizes before and after.
  const tmp = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html").body;
  const blocks = [...tmp.children];
  if (!urls.length || !blocks.length) return tmp.innerHTML;
  const gap = Math.max(1, Math.floor(blocks.length / urls.length));
  let u = 0, since = 0;
  blocks.forEach((b, i) => {
    if (i === 0) return;
    since++;
    if (since >= gap && u < urls.length) {
      b.after(imgPara(tmp.ownerDocument, urls[u++]));
      since = 0;
    }
  });
  while (u < urls.length) tmp.appendChild(imgPara(tmp.ownerDocument, urls[u++]));
  return tmp.innerHTML;
}

function imgPara(doc, url) {
  const p = doc.createElement("p");
  const img = doc.createElement("img");
  img.setAttribute("src", url);
  img.setAttribute("alt", "");
  p.appendChild(img);
  return p;
}
