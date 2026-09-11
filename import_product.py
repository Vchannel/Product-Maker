#!/usr/bin/env python3
"""Nhập sản phẩm từ flycampro.vn lên WooCommerce (vchannelstore.com).

Cách dùng:
    python import_product.py <link_san_pham>

Xem README.md để biết cách cấu hình .env trước khi chạy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from pathlib import Path

# Force UTF-8 stdout/stderr regardless of how this process was launched -
# Windows defaults console encoding to the legacy ANSI codepage (cp1252),
# which can't represent Vietnamese text and crashes print() with a
# UnicodeEncodeError. Only some launch paths (e.g. a PowerShell session)
# happen to set UTF-8 already; this makes it unconditional.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from lib.images import ImageDownloadError, download_images
from lib.rewriter import RewriteError, describe_box_contents, rewrite_content
from lib.scraper import ProductData, ScrapeError, scrape_product, slug_from_url
from lib.wc_client import WooCommerceAPIError, WooCommerceClient, WPMediaClient

VARIATION_ATTRIBUTE_NAME = "Phiên bản"

DEFAULT_DISCOUNT_VND = 45000
DEFAULT_STATUS = "draft"
CACHE_ROOT = Path("cache")

REQUIRED_ENV = ["WP_SITE_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET", "WP_USERNAME", "WP_APP_PASSWORD"]


_log_local = threading.local()


def set_log_sink(sink) -> None:
    """Route this thread's log() calls into `sink` (any object with .append)
    in addition to stdout. Used by the web app so each import job's log
    lines land in its own per-job buffer instead of the process's stdout."""
    _log_local.sink = sink


def log(step: str, msg: str) -> None:
    line = f"[{step}] {msg}"
    print(line)
    sink = getattr(_log_local, "sink", None)
    if sink is not None:
        sink.append(line)


def load_json(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_specs_html(spec_sections: list) -> str:
    if not spec_sections:
        return ""
    parts = ["<h3>Thông số kỹ thuật</h3>"]
    for section in spec_sections:
        rows = section["rows"]
        if not rows:
            continue
        parts.append(f"<h4>{section['heading']}</h4>")
        parts.append('<table class="flycampro-specs"><tbody>')
        for label, value in rows:
            parts.append(f"<tr><td>{label}</td><td>{value}</td></tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def interleave_images_into_description(description_html: str, image_urls: list, alt_text: str) -> str:
    """Spread product images through the description body instead of leaving
    them only in the separate WooCommerce gallery. Distributes images evenly
    across the description's top-level blocks (<p>/<h3>/...); the opening
    paragraph is left text-only, and any leftover images are appended at the end."""
    if not image_urls:
        return description_html

    soup = BeautifulSoup(description_html, "html.parser")
    blocks = [str(c) for c in soup.contents if getattr(c, "name", None)]
    if not blocks:
        blocks = [description_html]

    def img_tag(url: str) -> str:
        return f'<p><img src="{url}" alt="{alt_text}" style="max-width:100%;height:auto;" /></p>'

    img_iter = iter(image_urls)
    gap = max(1, len(blocks) // len(image_urls))
    result = []
    since_last = 0
    for i, block in enumerate(blocks):
        result.append(block)
        if i == 0:
            continue
        since_last += 1
        if since_last >= gap:
            url = next(img_iter, None)
            if url is None:
                continue
            result.append(img_tag(url))
            since_last = 0
    for url in img_iter:
        result.append(img_tag(url))
    return "\n".join(result)


def derive_tags(original_title: str, brand: str) -> list:
    """Deterministic tags from the scraped (not rewritten) title - brand plus
    the remaining product-line words, with generic combo/package suffixes
    trimmed. No invented text, just a reformatting of what was scraped."""
    tags = []
    if brand:
        tags.append(brand)

    line = original_title.strip()
    if brand and line.lower().startswith(brand.lower()):
        line = line[len(brand) :].strip()

    suffixes = ["Creator Combo", "Fly More Combo", "Combo Sáng Tạo", "Standard Combo", "Combo"]
    for suf in suffixes:
        if line.lower().endswith(suf.lower()):
            line = line[: -len(suf)].strip()
            break

    if line and line.lower() != (brand or "").lower():
        tags.append(line)
    return tags


def resolve_categories(wc: WooCommerceClient, category_names: list) -> list:
    """Look up categories by name (must already exist in WooCommerce - this
    never auto-creates a category, since guessing a new one wrong pollutes
    the site's taxonomy). Automatically includes each match's parent
    category too, matching this store's existing convention of assigning
    both (e.g. "Camera" + "Gimbal camera")."""
    result_ids = []
    for name in category_names:
        cat = wc.find_category_by_name(name.strip())
        if not cat:
            log("WOOCOMMERCE", f"CẢNH BÁO: không tìm thấy category '{name}' trên site, bỏ qua.")
            continue
        if cat["parent"] and cat["parent"] not in result_ids:
            result_ids.append(cat["parent"])
        if cat["id"] not in result_ids:
            result_ids.append(cat["id"])
    return [{"id": cid} for cid in result_ids]


def resolve_brand(wc: WooCommerceClient, brand_name: str) -> list:
    """Look up the brand in WooCommerce's native Brands taxonomy, if the site
    has one. Returns [] (not an error) when there's no such taxonomy or no
    matching term - brand assignment is best-effort."""
    if not brand_name:
        return []
    brand = wc.find_brand_by_name(brand_name)
    if not brand:
        return []
    return [{"id": brand["id"]}]


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def split_common_prefix(titles: list) -> tuple:
    """Split a list of titles into their shared leading words and each
    title's distinguishing suffix - e.g. ["DJI Pocket 4 Creator Combo",
    "DJI Pocket 4 Standard Combo"] -> ("DJI Pocket 4", ["Creator Combo",
    "Standard Combo"]). Falls back to the full title as its own label if a
    title doesn't extend the common prefix (e.g. only one URL given)."""
    word_lists = [t.split() for t in titles]
    common_words = []
    for words in zip(*word_lists):
        if len(set(words)) == 1:
            common_words.append(words[0])
        else:
            break
    common = " ".join(common_words)
    labels = []
    for t in titles:
        label = t[len(common) :].strip() if common and t.startswith(common) else t
        labels.append(label or t)
    return common, labels


def step_scrape(url: str, cache_dir: Path, force: bool) -> ProductData:
    raw_path = cache_dir / "raw.json"
    if not force:
        cached = load_json(raw_path)
        if cached:
            log("SCRAPE", f"Dùng dữ liệu đã cache tại {raw_path}")
            return ProductData.from_dict(cached)
    log("SCRAPE", f"Đang lấy dữ liệu từ {url} ...")
    product = scrape_product(url)
    save_json(raw_path, product.to_dict())
    log("SCRAPE", f"OK - '{product.title}' | giá {product.price_vnd:,}đ | {len(product.image_urls)} ảnh")
    return product


def step_download_images(product: ProductData, cache_dir: Path) -> list:
    images_dir = cache_dir / "images"
    log("IMAGES", f"Đang tải {len(product.image_urls)} ảnh về {images_dir} ...")
    images = download_images(product.image_urls, images_dir, product.slug)
    log("IMAGES", f"OK - {len(images)} ảnh đã sẵn sàng ở local")
    return images


def step_box_description(images: list, cache_dir: Path, model: str, force: bool) -> str:
    """Vision-based 'what's in the box' HTML blurb, used as a variation's own
    description on WooCommerce. Cached like the other Claude call so re-runs
    don't re-spend on it."""
    cache_path = cache_dir / "box_description.json"
    if not force:
        cached = load_json(cache_path)
        if cached is not None:
            log("BOX-CONTENTS", f"Dùng cache tại {cache_path}")
            return cached.get("html", "")

    log("BOX-CONTENTS", "Đang hỏi Claude (vision) để tìm ảnh flat-lay phụ kiện ...")
    import anthropic

    client = anthropic.Anthropic()
    image_paths = [img["path"] for img in images[:3]]
    html = describe_box_contents(client, model, image_paths)
    save_json(cache_path, {"html": html})
    log("BOX-CONTENTS", "OK - tìm thấy danh sách phụ kiện" if html else "Không tìm thấy ảnh flat-lay phù hợp")
    return html


def step_rewrite(product: ProductData, cache_dir: Path, model: str, force: bool) -> dict:
    rewritten_path = cache_dir / "rewritten.json"
    if not force:
        cached = load_json(rewritten_path)
        if cached:
            log("REWRITE", f"Dùng nội dung đã rewrite trong cache tại {rewritten_path}")
            return cached

    log("REWRITE", f"Đang gọi Claude ({model}) để viết lại nội dung ...")
    import anthropic

    client = anthropic.Anthropic()
    spec_sections = [{"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in product.spec_sections]
    rewritten = rewrite_content(
        client, model, product.title, product.description_text, spec_sections, product.box_contents_text
    )
    save_json(rewritten_path, rewritten)
    log("REWRITE", "OK - đã có tiêu đề, mô tả ngắn và mô tả chi tiết mới")
    return rewritten


def step_push_to_wc(
    product: ProductData,
    rewritten: dict,
    images: list,
    cache_dir: Path,
    discount_vnd: int,
    status: str,
    category_names: list = (),
) -> str:
    state_path = cache_dir / "state.json"
    state = load_json(state_path) or {}

    if state.get("wc_product_id"):
        link = state.get("wc_edit_link") or state.get("wc_product_link", "")
        log("WOOCOMMERCE", f"Sản phẩm đã được xử lý trước đó (ID {state['wc_product_id']}). {link}")
        return link

    site_url = os.environ["WP_SITE_URL"]
    wc = WooCommerceClient(site_url, os.environ["WC_CONSUMER_KEY"], os.environ["WC_CONSUMER_SECRET"])
    wp_media = WPMediaClient(site_url, os.environ["WP_USERNAME"], os.environ["WP_APP_PASSWORD"])

    sku = f"fcp-{product.slug}"[:64]

    log("WOOCOMMERCE", f"Kiểm tra sản phẩm trùng SKU '{sku}' ...")
    existing = wc.find_product_by_sku(sku)
    if existing:
        link = existing.get("permalink", "")
        log(
            "WOOCOMMERCE",
            f"Sản phẩm với SKU '{sku}' đã tồn tại (ID {existing['id']}). Bỏ qua, không tạo trùng. {link}",
        )
        state.update({"wc_product_id": existing["id"], "wc_product_link": link, "skipped_existing": True})
        save_json(state_path, state)
        return link

    media_ids = state.get("media_ids", {})
    log("WOOCOMMERCE", f"Đang upload {len(images)} ảnh lên WordPress media library ...")
    for img in images:
        filename = img["filename"]
        if filename in media_ids:
            continue
        media = wp_media.upload_media(img["path"], title=rewritten["title"], alt_text=rewritten["title"])
        media_ids[filename] = {"id": media["id"], "url": media["source_url"]}
        state["media_ids"] = media_ids
        save_json(state_path, state)  # save after each upload so a later failure doesn't re-upload
        log("WOOCOMMERCE", f"  + {filename} -> media ID {media['id']}")

    regular_price = product.price_vnd
    sale_price = regular_price - discount_vnd if 0 < discount_vnd < regular_price else None

    spec_sections = [{"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in product.spec_sections]
    image_urls_in_order = [media_ids[img["filename"]]["url"] for img in images]
    description_html = interleave_images_into_description(
        rewritten["description"], image_urls_in_order, rewritten["title"]
    )
    description_html += "\n" + build_specs_html(spec_sections)

    tags = derive_tags(product.title, product.brand)
    brands_payload = resolve_brand(wc, product.brand)
    categories_payload = resolve_categories(wc, list(category_names))

    payload = {
        "name": rewritten["title"],
        "type": "simple",
        "status": status,
        "sku": sku,
        "regular_price": str(regular_price),
        "description": description_html,
        "short_description": rewritten["short_description"],
        "images": [{"id": media_ids[img["filename"]]["id"], "position": i} for i, img in enumerate(images)],
        "tags": [{"name": t} for t in tags],
        "meta_data": [{"key": "_flycampro_source_url", "value": product.source_url}],
    }
    if sale_price is not None:
        payload["sale_price"] = str(sale_price)
    if brands_payload:
        payload["brands"] = brands_payload
    if categories_payload:
        payload["categories"] = categories_payload

    log("WOOCOMMERCE", f"Đang tạo sản phẩm (status={status}) ...")
    created = wc.create_product(payload)
    link = created.get("permalink", "")
    edit_link = f"{site_url.rstrip('/')}/wp-admin/post.php?post={created['id']}&action=edit"
    state.update({"wc_product_id": created["id"], "wc_product_link": link, "wc_edit_link": edit_link})
    save_json(state_path, state)
    log("WOOCOMMERCE", f"OK - Đã tạo sản phẩm ID {created['id']}")
    return edit_link or link


def run_simple_product_flow(
    url: str, force_scrape: bool, force_rewrite: bool, discount: int, status: str, model: str, category_names: list
) -> str:
    slug = slug_from_url(url)
    cache_dir = CACHE_ROOT / slug
    product = step_scrape(url, cache_dir, force_scrape)
    images = step_download_images(product, cache_dir)
    rewritten = step_rewrite(product, cache_dir, model, force_rewrite)
    return step_push_to_wc(product, rewritten, images, cache_dir, discount, status, category_names)


def run_variable_product_flow(
    urls: list, force_scrape: bool, force_rewrite: bool, discount: int, status: str, model: str, category_names: list
) -> str:
    """Group several flycampro.vn product pages (same device, different combo)
    into ONE WooCommerce variable product: a "Phiên bản" option that swaps
    price, representative image, and a per-option 'what's in the box' blurb."""
    site_url = os.environ["WP_SITE_URL"]
    wc = WooCommerceClient(site_url, os.environ["WC_CONSUMER_KEY"], os.environ["WC_CONSUMER_SECRET"])
    wp_media = WPMediaClient(site_url, os.environ["WP_USERNAME"], os.environ["WP_APP_PASSWORD"])

    products = []
    images_by_slug = {}
    for url in urls:
        slug = slug_from_url(url)
        cache_dir = CACHE_ROOT / slug
        product = step_scrape(url, cache_dir, force_scrape)
        images_by_slug[slug] = step_download_images(product, cache_dir)
        products.append(product)

    common_title, labels = split_common_prefix([p.title for p in products])
    if not common_title:
        common_title = products[0].title
    log("GROUP", f"Tên chung: '{common_title}' | các phiên bản: {', '.join(labels)}")

    parent_slug = slugify(common_title)
    parent_cache_dir = CACHE_ROOT / f"_variable-{parent_slug}"
    base_product = products[0]

    rewritten_parent_path = parent_cache_dir / "rewritten.json"
    rewritten_parent = None if force_rewrite else load_json(rewritten_parent_path)
    if rewritten_parent is None:
        log("REWRITE", f"Đang gọi Claude ({model}) để viết nội dung chung cho sản phẩm cha ...")
        import anthropic

        client = anthropic.Anthropic()
        spec_sections = [
            {"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in base_product.spec_sections
        ]
        rewritten_parent = rewrite_content(
            client, model, common_title, base_product.description_text, spec_sections, ""
        )
        save_json(rewritten_parent_path, rewritten_parent)
        log("REWRITE", "OK - đã có nội dung chung")
    else:
        log("REWRITE", f"Dùng nội dung cha đã cache tại {rewritten_parent_path}")

    box_html_by_slug = {}
    for product in products:
        box_html_by_slug[product.slug] = step_box_description(
            images_by_slug[product.slug], CACHE_ROOT / product.slug, model, force_rewrite
        )

    state_path = parent_cache_dir / "state.json"
    state = load_json(state_path) or {}
    parent_sku = f"fcp-{parent_slug}"[:64]

    parent_id = state.get("wc_parent_id")
    if not parent_id:
        log("WOOCOMMERCE", f"Kiểm tra sản phẩm cha trùng SKU '{parent_sku}' ...")
        existing = wc.find_product_by_sku(parent_sku)
        if existing:
            parent_id = existing["id"]
            state["wc_parent_id"] = parent_id
            state["wc_parent_link"] = existing.get("permalink", "")
            save_json(state_path, state)
            log("WOOCOMMERCE", f"Sản phẩm cha đã tồn tại (ID {parent_id}), dùng lại - không tạo trùng.")
        else:
            media_ids = state.get("parent_media_ids", {})
            base_images = images_by_slug[base_product.slug]
            for img in base_images:
                filename = img["filename"]
                if filename in media_ids:
                    continue
                media = wp_media.upload_media(
                    img["path"], title=rewritten_parent["title"], alt_text=rewritten_parent["title"]
                )
                media_ids[filename] = {"id": media["id"], "url": media["source_url"]}
                state["parent_media_ids"] = media_ids
                save_json(state_path, state)
                log("WOOCOMMERCE", f"  + (gallery cha) {filename} -> media ID {media['id']}")

            spec_sections = [
                {"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in base_product.spec_sections
            ]
            image_urls_in_order = [media_ids[img["filename"]]["url"] for img in base_images]
            description_html = interleave_images_into_description(
                rewritten_parent["description"], image_urls_in_order, rewritten_parent["title"]
            )
            description_html += "\n" + build_specs_html(spec_sections)
            tags = derive_tags(common_title, base_product.brand)
            brands_payload = resolve_brand(wc, base_product.brand)
            categories_payload = resolve_categories(wc, list(category_names))

            payload = {
                "name": rewritten_parent["title"],
                "type": "variable",
                "status": status,
                "sku": parent_sku,
                "description": description_html,
                "short_description": rewritten_parent["short_description"],
                "images": [{"id": media_ids[img["filename"]]["id"], "position": i} for i, img in enumerate(base_images)],
                "tags": [{"name": t} for t in tags],
                "attributes": [
                    {"name": VARIATION_ATTRIBUTE_NAME, "options": labels, "visible": True, "variation": True}
                ],
            }
            if brands_payload:
                payload["brands"] = brands_payload
            if categories_payload:
                payload["categories"] = categories_payload
            log("WOOCOMMERCE", f"Đang tạo sản phẩm cha dạng variable (status={status}) ...")
            created = wc.create_product(payload)
            parent_id = created["id"]
            state["wc_parent_id"] = parent_id
            state["wc_parent_link"] = created.get("permalink", "")
            save_json(state_path, state)
            log("WOOCOMMERCE", f"OK - Đã tạo sản phẩm cha ID {parent_id}")

    variation_ids = state.get("variations", {})
    for product, label in zip(products, labels):
        # Always derive from parent_slug + label, never product.slug alone -
        # a variant's own URL slug can coincide exactly with the parent's
        # (e.g. the "default" combo's flycampro URL has no combo suffix),
        # which would collide with the parent product's own SKU.
        variant_sku = f"fcp-{parent_slug}-{slugify(label)}"[:64]
        if variant_sku in variation_ids:
            log("WOOCOMMERCE", f"Variation '{label}' (SKU {variant_sku}) đã xử lý trước đó, bỏ qua.")
            continue

        existing_var = wc.find_variation_by_sku(parent_id, variant_sku)
        if existing_var:
            variation_ids[variant_sku] = existing_var["id"]
            state["variations"] = variation_ids
            save_json(state_path, state)
            log("WOOCOMMERCE", f"Variation SKU '{variant_sku}' đã tồn tại (ID {existing_var['id']}), bỏ qua.")
            continue

        images = images_by_slug[product.slug]
        media_key = f"media_ids__{product.slug}"
        media_ids = state.get(media_key, {})
        for img in images:
            filename = img["filename"]
            if filename in media_ids:
                continue
            title = f"{rewritten_parent['title']} - {label}"
            media = wp_media.upload_media(img["path"], title=title, alt_text=title)
            media_ids[filename] = {"id": media["id"], "url": media["source_url"]}
            state[media_key] = media_ids
            save_json(state_path, state)
            log("WOOCOMMERCE", f"  + ({label}) {filename} -> media ID {media['id']}")

        regular_price = product.price_vnd
        sale_price = regular_price - discount if 0 < discount < regular_price else None
        hero_media_id = media_ids[images[0]["filename"]]["id"]

        variation_payload = {
            "sku": variant_sku,
            "regular_price": str(regular_price),
            "description": box_html_by_slug[product.slug],
            "image": {"id": hero_media_id},
            "attributes": [{"name": VARIATION_ATTRIBUTE_NAME, "option": label}],
        }
        if sale_price is not None:
            variation_payload["sale_price"] = str(sale_price)

        log("WOOCOMMERCE", f"Đang tạo variation '{label}' (giá {regular_price:,}đ) ...")
        created_var = wc.create_variation(parent_id, variation_payload)
        variation_ids[variant_sku] = created_var["id"]
        state["variations"] = variation_ids
        save_json(state_path, state)
        log("WOOCOMMERCE", f"OK - variation '{label}' ID {created_var['id']}")

    return f"{site_url.rstrip('/')}/wp-admin/post.php?post={parent_id}&action=edit"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import sản phẩm từ flycampro.vn lên WooCommerce")
    parser.add_argument(
        "urls",
        nargs="+",
        help="1 URL sản phẩm trên flycampro.vn, hoặc 2+ URL của các combo cùng 1 sản phẩm "
        "(sẽ gộp thành 1 variable product với option chọn combo)",
    )
    parser.add_argument("--force-scrape", action="store_true", help="Bỏ qua cache, scrape lại từ đầu")
    parser.add_argument("--force-rewrite", action="store_true", help="Bỏ qua cache, gọi lại Claude để rewrite")
    parser.add_argument(
        "--discount",
        type=int,
        default=None,
        help=f"Số tiền (VND) trừ vào giá gốc để ra sale_price (mặc định {DEFAULT_DISCOUNT_VND})",
    )
    parser.add_argument(
        "--status",
        choices=["draft", "publish", "pending"],
        default=None,
        help=f"Trạng thái sản phẩm khi tạo trên WooCommerce (mặc định: {DEFAULT_STATUS})",
    )
    parser.add_argument(
        "--category",
        default="",
        help="Tên (các) category có sẵn trên WooCommerce để gán cho sản phẩm, cách nhau bởi dấu phẩy "
        "(vd: --category \"Gimbal camera\"). Category phải đã tồn tại trên site - script không tự tạo mới. "
        "Category cha của category tìm thấy cũng sẽ tự động được gán kèm.",
    )
    args = parser.parse_args()

    load_dotenv()

    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Thiếu biến môi trường trong .env: {', '.join(missing)}")
        print("Xem README.md để biết cách cấu hình.")
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Thiếu biến môi trường ANTHROPIC_API_KEY trong .env.")
        return 1

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    discount = (
        args.discount if args.discount is not None else int(os.environ.get("PRICE_DISCOUNT_VND", DEFAULT_DISCOUNT_VND))
    )
    status = args.status or os.environ.get("WC_PRODUCT_STATUS", DEFAULT_STATUS)

    try:
        for url in args.urls:
            slug_from_url(url)  # validate every URL up front before doing any work
    except ScrapeError as e:
        print(f"URL không hợp lệ: {e}")
        return 1

    category_names = [c.strip() for c in args.category.split(",") if c.strip()]

    try:
        if len(args.urls) == 1:
            link = run_simple_product_flow(
                args.urls[0], args.force_scrape, args.force_rewrite, discount, status, model, category_names
            )
        else:
            link = run_variable_product_flow(
                args.urls, args.force_scrape, args.force_rewrite, discount, status, model, category_names
            )
    except (ScrapeError, ImageDownloadError, RewriteError, WooCommerceAPIError) as e:
        log("ERROR", str(e))
        print("\nDừng lại do lỗi ở trên. Chạy lại đúng lệnh này để tiếp tục từ bước đã cache thành công.")
        return 1

    print(f"\nHoàn tất! Link sản phẩm: {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
