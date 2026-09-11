from pathlib import Path

import numpy as np
from PIL import Image

from lib import pipeline
from lib.html_utils import build_specs_html, interleave_images, sanitize_html
from lib.scraper import ScrapeError, parse_product_html, validate_url
from lib.watermark import detect_logo, remove_logo

from conftest import FIXTURES, make_product_image


# ---------------------------------------------------------------- scraper
def test_scraper_flat_table_layout():
    html = (FIXTURES / "flycampro_table_specs.html").read_text(encoding="utf-8")
    p = parse_product_html(html, "https://flycampro.vn/products/dji-pocket-4-creator-combo")
    assert p.title == "DJI Pocket 4 Creator Combo"
    assert p.price_vnd == 14740000
    assert len(p.image_urls) == 7
    assert [s.heading for s in p.spec_sections] == ["Tổng quan", "Gimbal", "Camera", "Pin", "Connection"]
    assert sum(len(s.rows) for s in p.spec_sections) == 43


def test_scraper_div_wrapped_tables_with_heading_rows():
    # The old parser returned zero spec sections for this layout.
    html = (FIXTURES / "flycampro_div_specs.html").read_text(encoding="utf-8")
    p = parse_product_html(html, "https://flycampro.vn/products/dji-osmo-pocket-4p")
    assert [s.heading for s in p.spec_sections] == ["General", "Gimbal", "Camera", "Battery", "Connection"]
    assert sum(len(s.rows) for s in p.spec_sections) == 43
    gimbal = dict(p.spec_sections[1].rows)
    assert gimbal["Controllable Range"] == "Pan: -235° to 58°\nTilt: -125° to 58°\nRoll: -45° to 45°"
    assert p.box_image_urls and p.box_image_urls[0].startswith("https://cdn.hstatic.net/")


def test_validate_url():
    assert validate_url("https://flycampro.vn/products/dji-pocket-4/") == "dji-pocket-4"
    for bad in ["https://example.com/products/x", "https://flycampro.vn/collections/dji", "not a url"]:
        try:
            validate_url(bad)
        except ScrapeError:
            continue
        raise AssertionError(f"accepted {bad}")


# ---------------------------------------------------------------- watermark
def test_logo_removed_without_touching_product(tmp_path):
    src, out = tmp_path / "a.png", tmp_path / "b.png"
    make_product_image(src, logo=True, product_reaches_corner=True)
    result = remove_logo(str(src), str(out))
    assert result.removed
    x0, y0, x1, y1 = result.box
    assert x1 < 300, "box must stop before the product that reaches into the corner"
    after = np.asarray(Image.open(out).convert("RGB"))
    assert (after[101:238, 71:258] == 255).all(), "logo pixels painted white"
    before = np.asarray(Image.open(src).convert("RGB"))
    assert (after[120:401, 300:521] == before[120:401, 300:521]).all(), "product untouched"


def test_clean_image_untouched(tmp_path):
    src = tmp_path / "a.png"
    make_product_image(src, logo=False)
    assert not detect_logo(Image.open(src)).removed


def test_busy_background_skipped(tmp_path):
    src = tmp_path / "photo.jpg"
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (800, 1200, 3), dtype=np.uint8)).save(src)
    assert detect_logo(Image.open(src)).reason == "nền không đồng nhất"


# ---------------------------------------------------------------- helpers
def test_split_common_prefix():
    assert pipeline.split_common_prefix(["DJI Pocket 4 Creator Combo", "DJI Pocket 4 Standard Combo"]) == (
        "DJI Pocket 4",
        ["Creator Combo", "Standard Combo"],
    )
    assert pipeline.split_common_prefix(["DJI Pocket 4", "DJI Pocket 4 Creator Combo"]) == (
        "DJI Pocket 4",
        ["DJI Pocket 4", "Creator Combo"],
    )


def test_slugify_vietnamese():
    assert pipeline.slugify("Máy quay Đẹp 4P") == "may-quay-dep-4p"
    assert pipeline.slugify("DJI Pocket 4") == "dji-pocket-4"  # same as the old ASCII slugify


def test_sanitize_html_strips_active_content():
    dirty = '<p onclick="x()">Hi <script>alert(1)</script><img src="javascript:1" onerror="x"><a href="javascript:y">l</a></p><div>d</div><iframe src="//e"></iframe>'
    clean = sanitize_html(dirty)
    for bad in ("script", "onclick", "onerror", "javascript", "iframe", "<div"):
        assert bad not in clean
    assert "<p>d</p>" in clean


def test_specs_html_escapes_and_keeps_line_breaks():
    html = build_specs_html([{"heading": "A<b>", "rows": [["x < y", 'a"b\nc']]}])
    assert "A&lt;b&gt;" in html and "x &lt; y" in html and "a&quot;b<br>c" in html


def test_interleave_images_quotes_alt():
    html = interleave_images("<p>1</p><p>2</p><p>3</p>", ["https://x/a.png"], 'He said "hi"')
    assert 'alt="He said &quot;hi&quot;"' in html
    assert html.startswith("<p>1</p>")
