"""Import pipeline shared by the CLI and the web app.

Two phases with a reviewable draft in between:

  prepare(urls)  scrape -> images (logo removal) -> AI copy -> existing check
                 => draft (plain JSON dict, editable)
  publish(draft) upload images (deduplicated) -> create/update WooCommerce
                 product (+ variations)

One URL makes a simple product. Several URLs of the same device (different
combos) make ONE variable product with a "Phiên bản" attribute; each option
carries its own price, image, SKU and "what's in the box" list.

Every expensive step is cached under cache/<slug>/ so a failed or cancelled
run resumes where it stopped.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from . import settings
from .html_utils import build_list_html, build_specs_html, interleave_images, sanitize_html
from .images import ImageDownloadError, download_images, sha1_file
from .rewriter import RewriteError, describe_box_contents, make_client, rewrite_content
from .scraper import ProductData, ScrapeError, scrape_product, validate_url
from .wc_client import WooCommerceAPIError, WooCommerceClient, WPMediaClient

VARIATION_ATTRIBUTE_NAME = "Phiên bản"
SKU_PREFIX = "fcp-"
SOURCE_META_KEY = "_flycampro_source_url"
SCRAPER_VERSION = 2
DRAFT_VERSION = 1

STAGES = [
    ("scrape", "Lấy dữ liệu"),
    ("images", "Xử lý ảnh"),
    ("content", "AI viết nội dung"),
    ("review", "Duyệt nội dung"),
    ("upload", "Upload ảnh"),
    ("publish", "Đăng sản phẩm"),
]

PIPELINE_ERRORS = (ScrapeError, ImageDownloadError, RewriteError, WooCommerceAPIError)


class Cancelled(Exception):
    pass


class DraftError(ValueError):
    pass


class Reporter:
    """Receives progress from the pipeline. The default prints to stdout."""

    def log(self, stage: str, msg: str, level: str = "info") -> None:
        prefix = {"warn": "CẢNH BÁO: ", "error": "LỖI: "}.get(level, "")
        print(f"[{stage.upper()}] {prefix}{msg}", flush=True)

    def stage(self, key: str, status: str, detail: str = "") -> None:
        pass

    def progress(self, key: str, done: int, total: int) -> None:
        pass

    def check_cancelled(self) -> None:
        pass


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows: file briefly held by antivirus/OneDrive/a reader
            if attempt == 5:
                raise
            time.sleep(0.15 * (attempt + 1))


def slugify(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def product_dir(slug: str) -> Path:
    return settings.CACHE_DIR / slug


def variable_key(parent_slug: str) -> str:
    return f"_variable-{parent_slug}"


def make_sku(*parts: str) -> str:
    return (SKU_PREFIX + "-".join(p for p in parts if p))[:64]


def vnd(amount: int) -> str:
    return f"{amount:,}đ".replace(",", ".")


def sale_price_for(regular: int, discount: int) -> Optional[int]:
    return regular - discount if 0 < discount < regular else None


def cheapest_label(variants: list) -> str:
    """Label of the variant with the lowest selling price (first one on ties) -
    pre-selected on the product page so shoppers start from the entry price."""
    return min(variants, key=lambda v: v.get("sale_price") or v["regular_price"])["label"]


def split_common_prefix(titles: list) -> tuple:
    """["DJI Pocket 4 Creator Combo", "DJI Pocket 4 Standard Combo"] ->
    ("DJI Pocket 4", ["Creator Combo", "Standard Combo"])."""
    word_lists = [t.split() for t in titles]
    common_words = []
    for words in zip(*word_lists):
        if len({w.lower() for w in words}) == 1:
            common_words.append(words[0])
        else:
            break
    common = " ".join(common_words)
    labels = []
    for t, words in zip(titles, word_lists):
        # A title that *is* the common prefix keeps its full title as label.
        label = " ".join(words[len(common_words):]) if common_words else t
        labels.append(label or t)
    return common, labels


def derive_tags(title: str, brand: str) -> list:
    tags = [brand] if brand else []
    line = title.strip()
    if brand and line.lower().startswith(brand.lower()):
        line = line[len(brand):].strip()
    for suffix in ["Creator Combo", "Fly More Combo", "Combo Sáng Tạo", "Standard Combo", "Vlog Combo", "Combo"]:
        if line.lower().endswith(suffix.lower()):
            line = line[: -len(suffix)].strip()
            break
    if line and line.lower() != (brand or "").lower():
        tags.append(line)
    return tags


def image_id(slug: str, filename: str) -> str:
    return f"{slug}/{filename}"


_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
_SAFE_KEY = re.compile(r"^(_variable-)?[a-z0-9][a-z0-9-]*$")


def resolve_image(image_ref: str, original: bool = False) -> Path:
    try:
        slug, filename = image_ref.split("/", 1)
    except ValueError as e:
        raise DraftError(f"Mã ảnh không hợp lệ: {image_ref}") from e
    if not _SAFE_SLUG.match(slug) or not _SAFE_FILE.match(filename) or ".." in filename:
        raise DraftError(f"Mã ảnh không hợp lệ: {image_ref}")
    base = product_dir(slug) / "images"
    path = (base / "_original" / filename) if original else (base / filename)
    if not path.exists():
        raise DraftError(f"Không tìm thấy ảnh {image_ref} trong cache.")
    return path


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------
@dataclass
class PrepareOptions:
    model: str = ""
    discount_vnd: int = settings.DEFAULT_DISCOUNT_VND
    status: str = settings.DEFAULT_STATUS
    categories: list = field(default_factory=list)  # [{"id": int|None, "name": str}]
    force_scrape: bool = False
    force_rewrite: bool = False
    read_box: bool = True
    insert_images: bool = True
    include_specs: bool = True
    on_exists: str = "skip"  # skip | update

    @classmethod
    def from_dict(cls, d: dict) -> "PrepareOptions":
        d = d or {}
        cats = []
        for c in d.get("categories") or []:
            if isinstance(c, dict) and (c.get("id") or c.get("name")):
                cats.append({"id": int(c["id"]) if c.get("id") else None, "name": str(c.get("name", ""))})
            elif isinstance(c, str) and c.strip():
                cats.append({"id": None, "name": c.strip()})
        status = d.get("status") or settings.default_status()
        return cls(
            model=d.get("model") or settings.model(),
            discount_vnd=_to_int(d.get("discount_vnd"), settings.default_discount()),
            status=status if status in {"draft", "pending", "publish"} else settings.DEFAULT_STATUS,
            categories=cats,
            force_scrape=bool(d.get("force_scrape")),
            force_rewrite=bool(d.get("force_rewrite")),
            read_box=d.get("read_box", True) is not False,
            insert_images=d.get("insert_images", True) is not False,
            include_specs=d.get("include_specs", True) is not False,
            on_exists="update" if d.get("on_exists") == "update" else "skip",
        )

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _to_int(value, default: int = 0) -> int:
    """Whole VND from JSON numbers, WooCommerce strings ("14740000", "14740000.00")
    or formatted input ("14.740.000")."""
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return int(float(text))
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else default


def get_product(url: str, force: bool = False, reporter: Optional[Reporter] = None) -> ProductData:
    reporter = reporter or Reporter()
    slug = validate_url(url)
    raw_path = product_dir(slug) / "raw.json"
    cached = None if force else load_json(raw_path)
    if cached and cached.get("scraper_version", 1) >= SCRAPER_VERSION:
        reporter.log("scrape", f"Dùng dữ liệu đã lưu của '{cached['title']}'")
        return ProductData.from_dict(cached)
    reporter.log("scrape", f"Đang tải {url}")
    product = scrape_product(url)
    data = product.to_dict()
    data["scraper_version"] = SCRAPER_VERSION
    data["scraped_at"] = int(time.time())
    save_json(raw_path, data)
    specs = sum(len(s.rows) for s in product.spec_sections)
    reporter.log(
        "scrape",
        f"'{product.title}' · {vnd(product.price_vnd)} · {len(product.image_urls)} ảnh · {specs} dòng thông số",
    )
    return product


def summarize(product: ProductData) -> dict:
    return {
        "url": product.source_url,
        "slug": product.slug,
        "title": product.title,
        "price_vnd": product.price_vnd,
        "image_count": len(product.image_urls),
        "thumb": product.image_urls[0] if product.image_urls else "",
        "spec_rows": sum(len(s.rows) for s in product.spec_sections),
        "brand": product.brand,
    }


def plan_group(products: list) -> dict:
    """How a set of scraped products will be imported (for previews)."""
    if len(products) == 1:
        p = products[0]
        return {"kind": "simple", "title": p.title, "labels": [], "key": p.slug, "sku": make_sku(p.slug), "warning": ""}
    common, labels = split_common_prefix([p.title for p in products])
    warning = ""
    if len(common.split()) < 2:
        warning = "Tiêu đề các link không có phần chung rõ ràng - kiểm tra lại có đúng là cùng một sản phẩm không."
    if len({l.lower() for l in labels}) != len(labels):
        warning = "Có hai link cho ra cùng tên phiên bản - có thể bạn dán trùng sản phẩm."
    title = common or products[0].title
    parent_slug = slugify(title)
    return {
        "kind": "variable",
        "title": title,
        "labels": labels,
        "key": variable_key(parent_slug),
        "sku": make_sku(parent_slug),
        "warning": warning,
    }


def preview(urls: list, reporter: Optional[Reporter] = None) -> dict:
    """Scrape (cached) every URL in parallel and describe the import plan."""
    reporter = reporter or Reporter()
    items: list = [None] * len(urls)

    def work(i_url):
        i, url = i_url
        try:
            items[i] = {"ok": True, **summarize(get_product(url, reporter=reporter))}
        except PIPELINE_ERRORS as e:
            items[i] = {"ok": False, "url": url, "error": str(e)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, enumerate(urls)))
    result = {"items": items, "plan": None}
    ok = [it for it in items if it["ok"]]
    if ok and len(ok) == len(items):
        products = [ProductData.from_dict(load_json(product_dir(it["slug"]) / "raw.json")) for it in ok]
        result["plan"] = plan_group(products)
    return result


BOX_CACHE_VERSION = 3


def _box_items(product: ProductData, images: list, model: str, force: bool, reporter: Reporter) -> tuple:
    """(in-box accessory list, flat-lay photo filename, plain hero photo filename);
    the filenames are None when no suitable photo was found."""
    path = product_dir(product.slug) / "box_description.json"
    cached = None if force else load_json(path)
    filenames = [img["filename"] for img in images]
    if cached is not None and cached.get("v") == BOX_CACHE_VERSION:
        flatlay, hero = cached.get("flatlay"), cached.get("hero")
        return cached.get("items", []), flatlay if flatlay in filenames else None, hero if hero in filenames else None
    # Older caches don't know which photos are the flat-lay / plain hero shot,
    # so vision runs again once.
    reporter.log("content", f"AI đang xem ảnh để đọc phụ kiện trong hộp của '{product.title}'")
    found = describe_box_contents(
        make_client(),
        model,
        [img["path"] for img in images],
        product.box_image_urls,
        product.box_contents_text,
    )
    def pick(index):
        return filenames[index] if isinstance(index, int) and 0 <= index < len(filenames) else None

    flatlay, hero = pick(found.get("flatlay_index")), pick(found.get("hero_index"))
    items = found.get("items", [])
    save_json(path, {"v": BOX_CACHE_VERSION, "items": items, "flatlay": flatlay, "hero": hero, "model": model})
    msg = f"Tìm thấy {len(items)} phụ kiện" if items else "Không xác định được phụ kiện trong hộp"
    msg += f" · ảnh bày phụ kiện: {flatlay}" if flatlay else ""
    msg += f" · ảnh đại diện: {hero}" if hero else ""
    reporter.log("content", msg)
    return items, flatlay, hero


def _rewrite(key_dir: Path, title: str, product: ProductData, model: str, force: bool, reporter: Reporter,
             box_text: str = "") -> dict:
    path = key_dir / "rewritten.json"
    cached = None if force else load_json(path)
    if cached and all(cached.get(k) for k in ("title", "short_description", "description")):
        reporter.log("content", "Dùng nội dung AI đã viết trước đó (bấm 'Viết lại bằng AI' nếu muốn bản mới)")
        return cached
    reporter.log("content", f"AI ({model}) đang viết lại tiêu đề và mô tả…")
    started = time.time()
    rewritten = rewrite_content(
        make_client(), model, title, product.description_text, product.spec_sections_dicts(), box_text
    )
    rewritten["model"] = model
    save_json(path, rewritten)
    reporter.log("content", f"Xong nội dung mới sau {time.time() - started:.0f}s")
    return rewritten


def _find_existing(sku: str, key_dir: Path, reporter: Reporter) -> Optional[dict]:
    if settings.missing_keys():
        return None
    try:
        wc = _wc()
        state = load_json(key_dir / "state.json") or {}
        pid = state.get("product_id") or state.get("wc_product_id") or state.get("wc_parent_id")
        product = wc.get_product(int(pid)) if pid else None
        if product is None or product.get("sku") != sku:
            product = wc.find_product_by_sku(sku)
    except WooCommerceAPIError as e:
        reporter.log("content", f"Không kiểm tra được sản phẩm trùng trên site: {e}", "warn")
        return None
    if not product:
        return None
    return {
        "id": product["id"],
        "name": product.get("name", ""),
        "status": product.get("status", ""),
        "type": product.get("type", ""),
        "permalink": product.get("permalink", ""),
        "edit_link": edit_link(product["id"]),
    }


def prepare(urls: list, options: PrepareOptions, reporter: Optional[Reporter] = None) -> dict:
    reporter = reporter or Reporter()
    urls = [u.strip() for u in urls if u and u.strip()]
    if not urls:
        raise DraftError("Cần ít nhất 1 link sản phẩm.")
    slugs = [validate_url(u) for u in urls]
    if len(set(slugs)) != len(slugs):
        raise DraftError("Danh sách có link bị trùng.")
    model = options.model or settings.model()

    # 1. scrape
    reporter.stage("scrape", "running")
    products = []
    for i, url in enumerate(urls):
        reporter.check_cancelled()
        products.append(get_product(url, options.force_scrape, reporter))
        reporter.progress("scrape", i + 1, len(urls))
    reporter.stage("scrape", "done", f"{len(products)} trang")

    # 2. images
    reporter.stage("images", "running")
    total = sum(len(p.image_urls) for p in products)
    done_before = 0
    images_by_slug = {}
    for p in products:
        reporter.check_cancelled()
        offset = done_before
        images_by_slug[p.slug] = download_images(
            p.image_urls,
            product_dir(p.slug) / "images",
            p.slug,
            on_progress=lambda d, _t, o=offset: (reporter.check_cancelled(), reporter.progress("images", o + d, total)),
        )
        removed = sum(1 for img in images_by_slug[p.slug] if img["logo_removed"])
        reporter.log("images", f"{p.slug}: {len(images_by_slug[p.slug])} ảnh sẵn sàng, đã xoá logo trên {removed} ảnh")
        done_before += len(p.image_urls)
    reporter.stage("images", "done", f"{total} ảnh")

    # 3. AI content
    reporter.stage("content", "running")
    group = plan_group(products)
    key_dir = settings.CACHE_DIR / group["key"]
    base = products[0]
    box_by_slug, flatlay_by_slug, hero_by_slug = {}, {}, {}
    if options.read_box:
        for p in products:
            reporter.check_cancelled()
            try:
                box_by_slug[p.slug], flatlay_by_slug[p.slug], hero_by_slug[p.slug] = _box_items(
                    p, images_by_slug[p.slug], model, options.force_rewrite, reporter
                )
            except RewriteError as e:
                reporter.log("content", f"Bỏ qua bước đọc phụ kiện: {e}", "warn")
                box_by_slug[p.slug] = []
    reporter.check_cancelled()
    if group["kind"] == "simple":
        rewritten = _rewrite(key_dir, base.title, base, model, options.force_rewrite, reporter, base.box_contents_text)
    else:
        reporter.log("content", f"Gộp {len(products)} link thành 1 sản phẩm: '{group['title']}' · phiên bản: {', '.join(group['labels'])}")
        if group["warning"]:
            reporter.log("content", group["warning"], "warn")
        rewritten = _rewrite(key_dir, group["title"], base, model, options.force_rewrite, reporter)
    reporter.check_cancelled()
    existing = _find_existing(group["sku"], key_dir, reporter)
    reporter.check_cancelled()
    if existing:
        reporter.log("content", f"Sản phẩm SKU {group['sku']} đã có trên site (#{existing['id']} · {existing['status']})", "warn")
    reporter.stage("content", "done")

    # 4. draft
    gallery = [image_id(base.slug, img["filename"]) for img in images_by_slug[base.slug]]
    # The store's main image is a plain device-only photo (flycampro's first
    # photo is usually an infographic with text), so it goes to the front.
    hero = hero_by_slug.get(base.slug)
    if hero:
        hero_ref = image_id(base.slug, hero)
        gallery = [hero_ref] + [ref for ref in gallery if ref != hero_ref]
    draft = {
        "version": DRAFT_VERSION,
        "kind": group["kind"],
        "key": group["key"],
        "sku": group["sku"],
        "source_title": group["title"],
        "title": rewritten["title"],
        "short_description": sanitize_html(rewritten["short_description"]),
        "description": sanitize_html(rewritten["description"]),
        "brand": base.brand,
        "tags": derive_tags(group["title"], base.brand),
        "categories": options.categories,
        "status": options.status,
        "insert_images": options.insert_images,
        "include_specs": options.include_specs,
        "on_exists": options.on_exists,
        "spec_sections": base.spec_sections_dicts(),
        "images": gallery,
        "available_images": {
            p.slug: [
                {"id": image_id(p.slug, img["filename"]), "logo_removed": img["logo_removed"]}
                for img in images_by_slug[p.slug]
            ]
            for p in products
        },
        "source_urls": [p.source_url for p in products],
        "existing": existing,
        "model": rewritten.get("model", model),
        "discount_vnd": options.discount_vnd,
    }
    if group["kind"] == "simple":
        draft.update(
            {
                "regular_price": base.price_vnd,
                "sale_price": sale_price_for(base.price_vnd, options.discount_vnd),
                "box_items": box_by_slug.get(base.slug, []),
                "variants": [],
            }
        )
    else:
        parent_slug = group["key"][len("_variable-"):]
        draft["variants"] = [
            {
                "slug": p.slug,
                "label": label,
                "sku": make_sku(parent_slug, slugify(label)),
                "source_url": p.source_url,
                "source_title": p.title,
                "regular_price": p.price_vnd,
                "sale_price": sale_price_for(p.price_vnd, options.discount_vnd),
                "box_items": box_by_slug.get(p.slug, []),
                # The combo's own image is the photo of everything in its box,
                # so shoppers see what they get when they pick that option.
                "image": image_id(p.slug, flatlay_by_slug.get(p.slug) or images_by_slug[p.slug][0]["filename"]),
            }
            for p, label in zip(products, group["labels"])
        ]
    return normalize_draft(draft)


def normalize_draft(draft: dict) -> dict:
    """Validate and clean a (possibly user-edited) draft. Raises DraftError."""
    if not isinstance(draft, dict) or draft.get("kind") not in ("simple", "variable"):
        raise DraftError("Bản nháp không hợp lệ.")
    d = dict(draft)
    if not isinstance(d.get("key"), str) or not _SAFE_KEY.match(d["key"]):
        raise DraftError("Bản nháp không hợp lệ (key).")
    urls = d.get("source_urls")
    if not isinstance(urls, list) or not urls:
        raise DraftError("Bản nháp không hợp lệ (source_urls).")
    try:
        d["source_urls"] = [str(u) for u in urls if validate_url(str(u))]
    except ScrapeError as e:
        raise DraftError(str(e)) from e
    sections = []
    for sec in d.get("spec_sections") or []:
        if not isinstance(sec, dict):
            continue
        rows = [[str(r[0]), str(r[1])] for r in sec.get("rows") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
        sections.append({"heading": str(sec.get("heading", "")), "rows": rows})
    d["spec_sections"] = sections
    d["brand"] = str(d.get("brand") or "")
    d["source_title"] = str(d.get("source_title") or "")
    d["title"] = " ".join(str(d.get("title", "")).split())
    if not d["title"]:
        raise DraftError("Tên sản phẩm không được để trống.")
    if len(d["title"]) > 200:
        raise DraftError("Tên sản phẩm quá dài (tối đa 200 ký tự).")
    d["short_description"] = sanitize_html(d.get("short_description", ""))
    d["description"] = sanitize_html(d.get("description", ""))
    d["sku"] = str(d.get("sku", "")).strip()[:64]
    if not d["sku"]:
        raise DraftError("SKU không được để trống.")
    d["status"] = d.get("status") if d.get("status") in ("draft", "pending", "publish") else "draft"
    d["on_exists"] = "update" if d.get("on_exists") == "update" else "skip"
    d["insert_images"] = bool(d.get("insert_images", True))
    d["include_specs"] = bool(d.get("include_specs", True))
    d["tags"] = list(dict.fromkeys(str(t).strip() for t in d.get("tags", []) if str(t).strip()))[:20]
    d["categories"] = PrepareOptions.from_dict({"categories": d.get("categories", [])}).categories

    images = []
    for ref in d.get("images", []):
        resolve_image(ref)
        if ref not in images:
            images.append(ref)
    if not images:
        raise DraftError("Cần chọn ít nhất 1 ảnh cho sản phẩm.")
    d["images"] = images

    def clean_items(items):
        return [" ".join(str(i).split()) for i in items or [] if str(i).strip()][:40]

    def clean_prices(obj, label):
        regular = _to_int(obj.get("regular_price"), 0)
        if regular <= 0:
            raise DraftError(f"Giá gốc của {label} phải lớn hơn 0.")
        sale = obj.get("sale_price")
        sale = _to_int(sale, 0) if sale not in (None, "") else None
        if sale is not None and not (0 < sale < regular):
            raise DraftError(f"Giá khuyến mãi của {label} phải nhỏ hơn giá gốc.")
        obj["regular_price"], obj["sale_price"] = regular, sale

    if d["kind"] == "simple":
        clean_prices(d, "sản phẩm")
        d["box_items"] = clean_items(d.get("box_items"))
        d["variants"] = []
    else:
        variants = d.get("variants") or []
        if not variants:
            raise DraftError("Sản phẩm nhiều phiên bản cần ít nhất 1 phiên bản.")
        labels, skus = set(), set()
        cleaned = []
        for v in variants:
            if not isinstance(v, dict):
                raise DraftError("Bản nháp không hợp lệ (variants).")
            v = dict(v)
            v["source_url"] = str(v.get("source_url") or "")
            v["source_title"] = str(v.get("source_title") or "")
            v["slug"] = str(v.get("slug") or "")
            v["label"] = " ".join(str(v.get("label", "")).split())
            if not v["label"]:
                raise DraftError("Tên phiên bản không được để trống.")
            if v["label"].lower() in labels:
                raise DraftError(f"Tên phiên bản '{v['label']}' bị trùng.")
            labels.add(v["label"].lower())
            v["sku"] = str(v.get("sku", "")).strip()[:64]
            if not v["sku"] or v["sku"] in skus or v["sku"] == d["sku"]:
                raise DraftError(f"SKU của phiên bản '{v['label']}' bị trống hoặc trùng.")
            skus.add(v["sku"])
            clean_prices(v, f"phiên bản '{v['label']}'")
            v["box_items"] = clean_items(v.get("box_items"))
            resolve_image(v.get("image", ""))
            cleaned.append(v)
        d["variants"] = cleaned
    return d


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------
class MediaIndex:
    """sha1 of an uploaded file -> WordPress media, per site. Stops the same
    picture being uploaded again for every variant or every re-import."""

    _lock = threading.Lock()

    def __init__(self, path: Optional[Path] = None):
        self.path = path or settings.DATA_DIR / "media_index.json"

    def _load(self) -> dict:
        return load_json(self.path) or {}

    def get(self, site: str, sha1: str) -> Optional[dict]:
        with self._lock:
            return self._load().get(site, {}).get(sha1)

    def put(self, site: str, sha1: str, entry: dict) -> None:
        with self._lock:
            data = self._load()
            data.setdefault(site, {})[sha1] = entry
            save_json(self.path, data)

    def forget(self, site: str, sha1: str) -> None:
        with self._lock:
            data = self._load()
            if data.get(site, {}).pop(sha1, None) is not None:
                save_json(self.path, data)


def site_url() -> str:
    return settings.get("WP_SITE_URL").rstrip("/")


def edit_link(product_id: int) -> str:
    return f"{site_url()}/wp-admin/post.php?post={product_id}&action=edit"


def _wc() -> WooCommerceClient:
    return WooCommerceClient(site_url(), settings.get("WC_CONSUMER_KEY"), settings.get("WC_CONSUMER_SECRET"))


def _wp() -> WPMediaClient:
    return WPMediaClient(site_url(), settings.get("WP_USERNAME"), settings.get("WP_APP_PASSWORD"))


def _upload_images(needed: list, titles: dict, wp: WPMediaClient, index: MediaIndex, reporter: Reporter) -> dict:
    site = site_url()
    media = {}
    uploaded = reused = 0
    for i, ref in enumerate(needed, start=1):
        reporter.check_cancelled()
        path = resolve_image(ref)
        digest = sha1_file(path)
        entry = index.get(site, digest)
        if entry and wp.get_media(entry["id"]):
            reused += 1
        else:
            if entry:
                index.forget(site, digest)
            result = wp.upload_media(str(path), title=titles[ref], alt_text=titles[ref])
            entry = {"id": result["id"], "url": result.get("source_url", ""), "uploaded_at": int(time.time())}
            index.put(site, digest, entry)
            uploaded += 1
            reporter.log("upload", f"{path.name} → media #{entry['id']}")
        media[ref] = entry
        reporter.progress("upload", i, len(needed))
    reporter.log("upload", f"Upload mới {uploaded} ảnh, dùng lại {reused} ảnh đã có trên site")
    return media


def _resolve_categories(wc: WooCommerceClient, categories: list, reporter: Reporter) -> list:
    if not categories:
        return []
    all_cats = {c["id"]: c for c in wc.list_categories()}
    by_name = {c["name"].strip().lower(): c for c in all_cats.values()}
    ids = []
    for cat in categories:
        found = all_cats.get(cat.get("id")) if cat.get("id") else by_name.get(cat.get("name", "").strip().lower())
        if not found:
            reporter.log("publish", f"Không tìm thấy category '{cat.get('name') or cat.get('id')}' trên site, bỏ qua", "warn")
            continue
        chain, parent = [], found.get("parent")
        while parent and parent in all_cats and parent not in chain:
            chain.insert(0, parent)
            parent = all_cats[parent].get("parent")
        for cid in chain + [found["id"]]:
            if cid not in ids:
                ids.append(cid)
    return [{"id": cid} for cid in ids]


def build_description(draft: dict, media: dict) -> str:
    html = draft["description"]
    if draft.get("insert_images"):
        urls = [media[ref]["url"] for ref in draft["images"] if media.get(ref, {}).get("url")]
        html = interleave_images(html, urls, draft["title"])
    if draft["kind"] == "simple" and draft.get("box_items"):
        html += "\n" + build_list_html(draft["box_items"], "Trong hộp có gì:")
    if draft.get("include_specs"):
        specs = build_specs_html(draft.get("spec_sections", []))
        if specs:
            html += "\n" + specs
    return html


def _price_fields(obj: dict) -> dict:
    return {
        "regular_price": str(obj["regular_price"]),
        "sale_price": str(obj["sale_price"]) if obj.get("sale_price") else "",
    }


def _attribute_options(product: dict) -> Optional[list]:
    for a in product.get("attributes", []):
        if a.get("name") == VARIATION_ATTRIBUTE_NAME:
            return list(a.get("options", []))
    return None


def publish(draft: dict, reporter: Optional[Reporter] = None, index: Optional[MediaIndex] = None) -> dict:
    """Create the product, or - when its SKU already exists - either keep its
    content and only add missing variations (on_exists="skip") or overwrite
    content, prices and variations (on_exists="update")."""
    reporter = reporter or Reporter()
    missing = settings.missing_keys()
    if missing:
        raise DraftError(f"Chưa cấu hình: {', '.join(missing)}. Vào Cài đặt để điền.")
    draft = normalize_draft(draft)
    index = index or MediaIndex()
    wc, wp = _wc(), _wp()
    key_dir = settings.CACHE_DIR / draft["key"]
    state = load_json(key_dir / "state.json") or {}
    variable = draft["kind"] == "variable"

    # 1. What exists on the site already?
    reporter.check_cancelled()
    existing = None
    pid = state.get("product_id") or state.get("wc_product_id") or state.get("wc_parent_id")
    if pid:
        existing = wc.get_product(int(pid))
        if existing and existing.get("sku") != draft["sku"]:
            existing = None
    existing = existing or wc.find_product_by_sku(draft["sku"])
    if existing and existing.get("status") == "trash":
        raise WooCommerceAPIError(
            f"Sản phẩm #{existing['id']} (SKU {draft['sku']}) đang nằm trong thùng rác. "
            "Khôi phục hoặc xoá vĩnh viễn nó trên WordPress rồi đăng lại."
        )
    if existing and existing.get("type") != draft["kind"]:
        raise WooCommerceAPIError(
            f"SKU {draft['sku']} đang được dùng cho sản phẩm #{existing['id']} loại '{existing.get('type')}'. "
            "Đổi SKU trong phần Nâng cao rồi đăng lại."
        )
    update = bool(existing) and draft["on_exists"] == "update"
    write_content = not existing or update
    if existing:
        mode = "cập nhật toàn bộ" if update else "giữ nguyên nội dung, chỉ bổ sung phần còn thiếu"
        reporter.log("publish", f"SKU {draft['sku']} đã có trên site (#{existing['id']}) → {mode}")

    current_vars, by_sku, by_label = [], {}, {}
    if variable and existing:
        current_vars = wc.list_variations(existing["id"])
        by_sku = {v.get("sku"): v for v in current_vars if v.get("sku")}
        for v in current_vars:
            for a in v.get("attributes", []):
                if a.get("name") == VARIATION_ATTRIBUTE_NAME:
                    by_label[str(a.get("option", "")).lower()] = v

    def existing_variation(v: dict) -> Optional[dict]:
        return by_sku.get(v["sku"]) or by_label.get(v["label"].lower())

    # Variation SKUs that are about to be created must not belong to another
    # product (e.g. a simple product imported earlier from the same link) -
    # WooCommerce would reject them only after the parent already exists.
    for v in draft["variants"]:
        if existing_variation(v):
            continue
        clash = wc.find_product_by_sku(v["sku"])
        if clash and (not existing or clash["id"] != existing["id"]):
            raise WooCommerceAPIError(
                f"SKU '{v['sku']}' của phiên bản '{v['label']}' đang được dùng bởi sản phẩm #{clash['id']} "
                f"(\"{clash.get('name', '')}\"). Đổi SKU của phiên bản trong bước duyệt, hoặc xoá/đổi SKU sản phẩm cũ."
            )

    # 2. Upload only the images that will actually be referenced.
    needed, titles = [], {}
    if write_content:
        for ref in draft["images"]:
            needed.append(ref)
            titles[ref] = draft["title"]
    for v in draft["variants"]:
        if update or not existing_variation(v):
            if v["image"] not in titles:
                needed.append(v["image"])
                titles[v["image"]] = f"{draft['title']} - {v['label']}"
    if needed:
        reporter.stage("upload", "running")
        media = _upload_images(needed, titles, wp, index, reporter)
        reporter.stage("upload", "done", f"{len(needed)} ảnh")
    else:
        media = {}
        reporter.stage("upload", "skipped", "không cần ảnh mới")

    # 3. Product. Last cancellation point: once the first store write has
    # happened the job runs to the end, so it never leaves a half-built product.
    reporter.check_cancelled()
    reporter.stage("publish", "running")

    def content_payload() -> dict:
        payload = {
            "name": draft["title"],
            "type": draft["kind"],
            "status": draft["status"],
            "sku": draft["sku"],
            "description": build_description(draft, media),
            "short_description": draft["short_description"],
            "images": [{"id": media[ref]["id"], "position": i} for i, ref in enumerate(draft["images"])],
            "tags": [{"name": t} for t in draft["tags"]],
            "meta_data": [{"key": SOURCE_META_KEY, "value": " ".join(draft["source_urls"])}],
        }
        categories = _resolve_categories(wc, draft["categories"], reporter)
        if categories:
            payload["categories"] = categories
        brand = wc.find_brand_by_name(draft["brand"]) if draft.get("brand") else None
        if brand:
            payload["brands"] = [{"id": brand["id"]}]
        return payload

    def remember(product_id: int, variations: dict) -> None:
        state.update(
            {
                "product_id": product_id,
                "kind": draft["kind"],
                "sku": draft["sku"],
                "edit_link": edit_link(product_id),
                "variations": {**state.get("variations", {}), **variations},
            }
        )
        save_json(key_dir / "state.json", state)

    final_status = None
    if not variable:
        if existing and not update:
            product, action = existing, "skipped"
        elif update:
            product, action = wc.update_product(existing["id"], {**content_payload(), **_price_fields(draft)}), "updated"
        else:
            product, action = wc.create_product({**content_payload(), **_price_fields(draft)}), "created"
        remember(product["id"], {})
        verb = {"created": "Đã tạo", "updated": "Đã cập nhật", "skipped": "Giữ nguyên"}[action]
        reporter.log("publish", f"{verb} sản phẩm #{product['id']}")
    else:
        labels = [v["label"] for v in draft["variants"]]
        if existing:
            current = _attribute_options(existing)
            merged = list(dict.fromkeys((current or []) + labels))
            attributes = [a for a in existing.get("attributes", []) if a.get("name") != VARIATION_ATTRIBUTE_NAME]
            attributes.append({"name": VARIATION_ATTRIBUTE_NAME, "options": merged, "visible": True, "variation": True})
            if update:
                product = wc.update_product(
                    existing["id"],
                    {
                        **content_payload(),
                        "attributes": attributes,
                        "default_attributes": [{"name": VARIATION_ATTRIBUTE_NAME, "option": cheapest_label(draft["variants"])}],
                    },
                )
                action = "updated"
                reporter.log("publish", f"Đã cập nhật sản phẩm #{product['id']}")
            elif current != merged:
                product = wc.update_product(existing["id"], {"attributes": attributes})
                action = "extended"
                reporter.log("publish", f"Đã thêm phiên bản mới vào thuộc tính '{VARIATION_ATTRIBUTE_NAME}' của #{product['id']}")
            else:
                product, action = existing, "skipped"
        else:
            # Created as a draft and switched to the requested status only
            # after every variation exists, so shoppers never see an empty product.
            payload = content_payload()
            final_status = payload["status"]
            payload["status"] = "draft"
            payload["attributes"] = [{"name": VARIATION_ATTRIBUTE_NAME, "options": labels, "visible": True, "variation": True}]
            payload["default_attributes"] = [{"name": VARIATION_ATTRIBUTE_NAME, "option": cheapest_label(draft["variants"])}]
            product, action = wc.create_product(payload), "created"
            reporter.log("publish", f"Đã tạo sản phẩm cha #{product['id']} (tạm ở trạng thái nháp)")
        remember(product["id"], {})

    variations_result = []
    for i, v in enumerate(draft["variants"], start=1):
        found = existing_variation(v)
        if found and not update:
            var, var_action = found, "skipped"
            regular = _to_int(found.get("regular_price"), 0) or None
            sale = _to_int(found.get("sale_price"), 0) or None
        else:
            payload = {
                "sku": v["sku"],
                **_price_fields(v),
                "description": build_list_html(v["box_items"], "Trong hộp có gì:"),
                "image": {"id": media[v["image"]]["id"]},
                "attributes": [{"name": VARIATION_ATTRIBUTE_NAME, "option": v["label"]}],
                "meta_data": [{"key": SOURCE_META_KEY, "value": v["source_url"]}],
            }
            if found:
                var, var_action = wc.update_variation(product["id"], found["id"], payload), "updated"
            else:
                var, var_action = wc.create_variation(product["id"], payload), "created"
                if action == "skipped":
                    action = "extended"
            regular, sale = v["regular_price"], v["sale_price"]
        remember(product["id"], {v["sku"]: var["id"]})
        verb = {"created": "Đã tạo", "updated": "Đã cập nhật", "skipped": "Giữ nguyên"}[var_action]
        reporter.log("publish", f"{verb} phiên bản '{v['label']}' (#{var['id']})" + (f" · {vnd(regular)}" if regular else ""))
        variations_result.append(
            {"label": v["label"], "sku": v["sku"], "id": var["id"], "action": var_action,
             "regular_price": regular, "sale_price": sale}
        )
        reporter.progress("publish", i, len(draft["variants"]))

    if final_status and final_status != "draft":
        product = wc.update_product(product["id"], {"status": final_status})
        reporter.log("publish", f"Đã chuyển sản phẩm sang trạng thái '{final_status}'")

    thumb = media.get(draft["images"][0], {}).get("url", "")
    if not thumb and product.get("images"):
        thumb = product["images"][0].get("src", "")
    if variable:
        regular_prices = [x["regular_price"] for x in variations_result if x["regular_price"]]
        sale_price = None
    elif action == "skipped":
        regular_prices = [_to_int(product.get("regular_price"), 0)] if product.get("regular_price") else []
        sale_price = _to_int(product.get("sale_price"), 0) or None
    else:
        regular_prices, sale_price = [draft["regular_price"]], draft["sale_price"]
    result = {
        "action": action,
        "product_id": product["id"],
        "name": product.get("name") or draft["title"],
        "kind": draft["kind"],
        "sku": draft["sku"],
        "status": product.get("status", draft["status"]),
        "permalink": product.get("permalink", ""),
        "edit_link": edit_link(product["id"]),
        "thumb_url": thumb,
        "price_min": min(regular_prices) if regular_prices else None,
        "price_max": max(regular_prices) if regular_prices else None,
        "sale_price": sale_price,
        "variations": variations_result,
        "source_urls": draft["source_urls"],
    }
    state.update({"permalink": result["permalink"], "published_at": int(time.time())})
    save_json(key_dir / "state.json", state)
    reporter.stage("publish", "done")
    return result
