"""Test fixtures: isolated cache/data dirs, synthetic product images, and
in-memory fakes for WooCommerce, WordPress media and Claude - no test ever
touches the network or the real store."""
from __future__ import annotations

import copy
import itertools
import json
import sys
import zlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import pipeline, settings  # noqa: E402
from lib.images import filename_for  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def make_product_image(path: Path, seed: int = 0, logo: bool = True, product_reaches_corner: bool = False, tint: int = 0) -> None:
    """White 1500x1000 canvas with a dark 'product' and optionally a logo in
    the top-left (same geometry as flycampro.vn photos)."""
    img = Image.new("RGB", (1500, 1000), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([600 + seed * 3, 250, 900, 950], fill=(30 + seed % 40, 30 + tint % 60, 35))
    if product_reaches_corner:
        d.rectangle([300, 120, 520, 400], fill=(40, 40, 45))  # enters the logo search window from the right
    if logo:
        d.polygon([(71, 101), (257, 101), (160, 130)], fill=(20, 20, 20))
        d.ellipse([130, 125, 190, 190], fill=(235, 129, 27))
        d.rectangle([71, 200, 257, 237], fill=(20, 20, 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / ".env")
    for key, value in {
        "WP_SITE_URL": "https://shop.test",
        "WC_CONSUMER_KEY": "ck_test",
        "WC_CONSUMER_SECRET": "cs_test",
        "WP_USERNAME": "admin",
        "WP_APP_PASSWORD": "xxxx xxxx",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_MODEL": "claude-opus-5",
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def seed_product(slug: str, title: str, price: int, n_images: int = 3, specs: bool = True) -> str:
    """Write a scraped product (raw.json + original images) into the cache so
    prepare() runs fully offline. Returns its URL."""
    url = f"https://flycampro.vn/products/{slug}"
    image_urls = [f"https://cdn.test/{slug}/{i}.png" for i in range(1, n_images + 1)]
    raw = {
        "source_url": url,
        "slug": slug,
        "title": title,
        "price_vnd": price,
        "description_text": f"Mô tả gốc của {title}.\nĐoạn hai.",
        "spec_sections": [{"heading": "Tổng quan", "rows": [["Cân nặng", "190 g"], ["Pin", "1545 mAh\n240 phút"]]}] if specs else [],
        "box_contents_text": "",
        "image_urls": image_urls,
        "brand": "DJI",
        "box_image_urls": [],
        "scraper_version": pipeline.SCRAPER_VERSION,
    }
    d = settings.CACHE_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    for i, u in enumerate(image_urls, start=1):
        make_product_image(d / "images" / "_original" / filename_for(u, slug, i), seed=i, tint=zlib.crc32(slug.encode()))
    return url


class FakeAI:
    def __init__(self):
        self.rewrite_calls = 0
        self.box_calls = 0

    def rewrite(self, client, model, title, description_text, spec_sections, box_text=""):
        self.rewrite_calls += 1
        return {
            "title": f"{title} – Bản viết lại",
            "short_description": "<ul><li>Điểm nổi bật 1</li><li>Điểm nổi bật 2</li></ul>",
            "description": "<p>Mở bài.</p><h3>Thiết kế</h3><p>Đoạn một.</p><p>Đoạn hai.</p>",
        }

    def box(self, client, model, image_paths, box_image_urls=(), box_text=""):
        self.box_calls += 1
        # Like flycampro galleries: the 2nd photo shows everything in the box,
        # the 3rd is a plain device-only shot.
        n = len(image_paths)
        return {"items": ["Túi đựng", "Cáp USB-C"], "flatlay_index": 1 if n > 1 else None, "hero_index": 2 if n > 2 else None}


@pytest.fixture
def fake_ai(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(pipeline, "rewrite_content", ai.rewrite)
    monkeypatch.setattr(pipeline, "describe_box_contents", ai.box)
    monkeypatch.setattr(pipeline, "make_client", lambda *a, **k: object())
    return ai


class FakeStore:
    """Just enough of WooCommerce + WP media to exercise publish()."""

    def __init__(self):
        self.ids = itertools.count(1000)
        self.products = {}
        self.variations = {}  # parent_id -> {id: variation}
        self.media = {}
        self.uploads = 0
        self.categories = [
            {"id": 10, "name": "Camera", "parent": 0, "count": 3},
            {"id": 11, "name": "Gimbal camera", "parent": 10, "count": 2},
        ]

    # WooCommerceClient
    def get_product(self, pid):
        p = self.products.get(int(pid))
        return copy.deepcopy(p) if p else None

    def find_product_by_sku(self, sku):
        return next((copy.deepcopy(p) for p in self.products.values() if p["sku"] == sku), None)

    def _all_skus(self):
        skus = {p["sku"] for p in self.products.values()}
        for vs in self.variations.values():
            skus |= {v["sku"] for v in vs.values()}
        return skus

    def create_product(self, payload):
        if payload["sku"] in self._all_skus():
            raise pipeline.WooCommerceAPIError("duplicate sku", 400, "product_invalid_sku")
        pid = next(self.ids)
        self.products[pid] = {"id": pid, "permalink": f"https://shop.test/?p={pid}", "status": payload.get("status", "draft"), **copy.deepcopy(payload)}
        self.variations[pid] = {}
        return copy.deepcopy(self.products[pid])

    def update_product(self, pid, payload):
        self.products[pid].update(copy.deepcopy(payload))
        return copy.deepcopy(self.products[pid])

    def list_variations(self, pid):
        return [copy.deepcopy(v) for v in self.variations.get(pid, {}).values()]

    def create_variation(self, pid, payload):
        if payload["sku"] in self._all_skus():
            raise pipeline.WooCommerceAPIError("duplicate sku", 400, "product_invalid_sku")
        options = next(a["options"] for a in self.products[pid]["attributes"] if a["name"] == pipeline.VARIATION_ATTRIBUTE_NAME)
        assert payload["attributes"][0]["option"] in options, "variation option missing from parent attribute"
        vid = next(self.ids)
        self.variations[pid][vid] = {"id": vid, **copy.deepcopy(payload)}
        return copy.deepcopy(self.variations[pid][vid])

    def update_variation(self, pid, vid, payload):
        self.variations[pid][vid].update(copy.deepcopy(payload))
        return copy.deepcopy(self.variations[pid][vid])

    def list_categories(self):
        return copy.deepcopy(self.categories)

    def find_brand_by_name(self, name):
        return {"id": 7, "name": "DJI"} if name == "DJI" else None

    def list_products(self, ids):
        return [copy.deepcopy(self.products[i]) for i in ids if i in self.products]

    def check(self):
        return len(self.products)

    # WPMediaClient
    def upload_media(self, path, title="", alt_text=""):
        self.uploads += 1
        mid = next(self.ids)
        self.media[mid] = {"id": mid, "source_url": f"https://shop.test/wp-content/uploads/{Path(path).name}"}
        return dict(self.media[mid])

    def get_media(self, mid):
        return dict(self.media[mid]) if mid in self.media else None

    def whoami(self):
        return {"name": "admin", "capabilities": {"upload_files": True}}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(pipeline, "_wc", lambda: s)
    monkeypatch.setattr(pipeline, "_wp", lambda: s)
    return s
