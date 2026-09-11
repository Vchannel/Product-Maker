"""Scrape product data from flycampro.vn product pages (Haravan theme).

Selectors were determined by inspecting a live product page's actual
(unrendered) HTML response - see README for the reference URL.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    pass


@dataclass
class SpecSection:
    heading: str
    rows: list  # list[tuple[str, str]]


@dataclass
class ProductData:
    source_url: str
    slug: str
    title: str
    price_vnd: int
    description_text: str
    spec_sections: list  # list[SpecSection]
    box_contents_text: str
    image_urls: list  # list[str] - full-size image URLs
    brand: str = ""

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "slug": self.slug,
            "title": self.title,
            "price_vnd": self.price_vnd,
            "description_text": self.description_text,
            "spec_sections": [
                {"heading": s.heading, "rows": [list(r) for r in s.rows]}
                for s in self.spec_sections
            ],
            "box_contents_text": self.box_contents_text,
            "image_urls": self.image_urls,
            "brand": self.brand,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductData":
        sections = [
            SpecSection(s["heading"], [tuple(r) for r in s["rows"]])
            for s in d["spec_sections"]
        ]
        return cls(
            source_url=d["source_url"],
            slug=d["slug"],
            title=d["title"],
            price_vnd=d["price_vnd"],
            description_text=d["description_text"],
            spec_sections=sections,
            box_contents_text=d.get("box_contents_text", ""),
            image_urls=d["image_urls"],
            brand=d.get("brand", ""),
        )


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    if not slug:
        raise ScrapeError(f"Không xác định được slug sản phẩm từ URL: {url}")
    return slug


def _normalize_image_url(url: str, base_url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base_url, url)


def _extract_price(soup: BeautifulSoup) -> int:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product" and isinstance(item.get("offers"), dict):
                price = item["offers"].get("price")
                if price is not None:
                    try:
                        return int(float(price))
                    except (TypeError, ValueError):
                        continue
    # Fallback: parse visible price text if JSON-LD is missing/malformed.
    # flycampro.vn does not embed JSON-LD in the raw HTTP response (only in the
    # browser-rendered DOM via client-side JS), so this is the path actually
    # used in practice. Match only the digit run immediately before the VND
    # symbol - the price block also contains a USD price and phone numbers,
    # and grabbing every digit in the block would produce a garbage number.
    price_el = soup.select_one(".price.clearfix, .price-product-detail")
    if price_el:
        match = re.search(r"([\d][\d.,]*)\s*(?:đ|₫|VND)", price_el.get_text(), re.IGNORECASE)
        if match:
            digits = re.sub(r"[^\d]", "", match.group(1))
            if digits:
                return int(digits)
    raise ScrapeError("Không tìm thấy giá sản phẩm (JSON-LD lẫn text đều fail).")


def _extract_title(soup: BeautifulSoup) -> str:
    el = soup.select_one('h1[itemprop="name"]') or soup.select_one("h1")
    if not el:
        raise ScrapeError("Không tìm thấy tiêu đề sản phẩm (thẻ h1).")
    text = el.get_text(strip=True)
    if not text:
        raise ScrapeError("Tiêu đề sản phẩm rỗng.")
    return text


def _html_block_to_text(el) -> str:
    """Convert an .rte HTML block to clean, structure-preserving plain text."""
    if el is None:
        return ""
    lines = []
    for node in el.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        lines.append(f"- {text}" if node.name == "li" else text)
    if lines:
        return "\n".join(lines)
    return el.get_text(" ", strip=True)


def _extract_description(soup: BeautifulSoup) -> str:
    el = soup.select_one("#idTab1 .rte") or soup.select_one("#idTab1")
    text = _html_block_to_text(el)
    if not text:
        raise ScrapeError("Không tìm thấy mô tả sản phẩm (#idTab1).")
    return text


def _extract_box_contents(soup: BeautifulSoup) -> str:
    el = soup.select_one("#idTab3 .rte") or soup.select_one("#idTab3")
    return _html_block_to_text(el)


def _extract_specs(soup: BeautifulSoup) -> list:
    """Parse the specs tab (#idTab2): a flat sequence of heading elements
    (p/h2/h3/h4) each immediately followed by a sibling <table> of the
    same section's key/value rows."""
    container = soup.select_one("#idTab2 .rte")
    if container is None:
        return []

    sections: list = []
    current_heading = "Thông số kỹ thuật"
    current_rows: list = []

    def flush():
        if current_rows:
            sections.append(SpecSection(current_heading, list(current_rows)))

    for child in container.find_all(["p", "h2", "h3", "h4", "table"], recursive=False):
        if child.name == "table":
            for tr in child.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text(" ", strip=True)
                    value = cells[1].get_text(" ", strip=True)
                    if label or value:
                        current_rows.append((label, value))
        else:
            heading_text = child.get_text(" ", strip=True)
            if not heading_text:
                continue
            flush()
            current_heading = heading_text
            current_rows = []
    flush()
    return sections


def _extract_images(soup: BeautifulSoup, base_url: str) -> list:
    urls: list = []
    seen: set = set()
    slider = soup.select_one(".big_section.flexslider")
    if slider is None:
        raise ScrapeError("Không tìm thấy khối ảnh sản phẩm (.big_section.flexslider).")
    for li in slider.select("ul.big_thumbs.slides > li.b_thumb"):
        if "b_video" in (li.get("class") or []):
            continue  # skip embedded YouTube video slide
        a = li.find("a", href=True)
        if not a:
            continue
        url = _normalize_image_url(a["href"], base_url)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        raise ScrapeError("Không tìm thấy URL ảnh full-size nào trong gallery sản phẩm.")
    return urls


def _extract_brand(soup: BeautifulSoup) -> str:
    # JSON-LD is present only in the browser-rendered DOM, never in the raw
    # HTTP response this scraper actually sees (same root cause as the price
    # fallback above), so this loop realistically never matches. flycampro.vn
    # is a single-brand DJI distributor, so default to that when it doesn't.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Product":
                brand = item.get("brand")
                if isinstance(brand, dict) and brand.get("name"):
                    return brand["name"]
                if isinstance(brand, str) and brand:
                    return brand
    return "DJI"


def scrape_product(url: str, timeout: int = 30) -> ProductData:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ScrapeError(f"Lỗi tải trang {url}: {e}") from e

    soup = BeautifulSoup(resp.text, "lxml")

    return ProductData(
        source_url=url,
        slug=slug_from_url(url),
        title=_extract_title(soup),
        price_vnd=_extract_price(soup),
        description_text=_extract_description(soup),
        spec_sections=_extract_specs(soup),
        box_contents_text=_extract_box_contents(soup),
        image_urls=_extract_images(soup, url),
        brand=_extract_brand(soup),
    )
