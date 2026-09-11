"""Scrape product data from flycampro.vn product pages (Haravan theme).

Selectors were determined by inspecting live product pages' raw (not
browser-rendered) HTML. Two spec-tab layouts exist in the wild and both are
handled: flat <p>heading</p><table> pairs, and <div>-wrapped tables whose
section heading sits in the table's own first row.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SUPPORTED_HOSTS = {"flycampro.vn", "www.flycampro.vn"}


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
    image_urls: list  # list[str] - full-size gallery image URLs
    brand: str = ""
    box_image_urls: list = field(default_factory=list)  # images inside the "Trong hộp có gì" tab

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "slug": self.slug,
            "title": self.title,
            "price_vnd": self.price_vnd,
            "description_text": self.description_text,
            "spec_sections": [
                {"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in self.spec_sections
            ],
            "box_contents_text": self.box_contents_text,
            "image_urls": self.image_urls,
            "brand": self.brand,
            "box_image_urls": self.box_image_urls,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProductData":
        sections = [SpecSection(s["heading"], [tuple(r) for r in s["rows"]]) for s in d["spec_sections"]]
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
            box_image_urls=d.get("box_image_urls", []),
        )

    def spec_sections_dicts(self) -> list:
        return [{"heading": s.heading, "rows": [list(r) for r in s.rows]} for s in self.spec_sections]


def slug_from_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ScrapeError(f"URL không hợp lệ: {url}")
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not slug:
        raise ScrapeError(f"Không xác định được slug sản phẩm từ URL: {url}")
    return slug


def validate_url(url: str) -> str:
    """Return the slug, raising ScrapeError for URLs this scraper can't handle."""
    slug = slug_from_url(url)
    parsed = urlparse(url.strip())
    if parsed.netloc.lower() not in SUPPORTED_HOSTS:
        raise ScrapeError(f"Chỉ hỗ trợ link từ flycampro.vn: {url}")
    if "/products/" not in parsed.path:
        raise ScrapeError(f"Link phải là trang sản phẩm (dạng flycampro.vn/products/...): {url}")
    return slug


def _normalize_image_url(url: str, base_url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base_url, url)


def _iter_ld_json(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Product":
                yield item


def _extract_price(soup: BeautifulSoup) -> int:
    for item in _iter_ld_json(soup):
        offers = item.get("offers")
        if isinstance(offers, dict) and offers.get("price") is not None:
            try:
                return int(float(offers["price"]))
            except (TypeError, ValueError):
                pass
    # flycampro.vn only has JSON-LD in the browser-rendered DOM, so this text
    # fallback is the path used in practice. Match only the digit run right
    # before the VND symbol - the block also has a USD price and phone numbers.
    price_el = soup.select_one(".price.clearfix, .price-product-detail")
    if price_el:
        match = re.search(r"([\d][\d.,]*)\s*(?:đ|₫|VND)", price_el.get_text(), re.IGNORECASE)
        if match:
            digits = re.sub(r"[^\d]", "", match.group(1))
            if digits:
                return int(digits)
    raise ScrapeError("Không tìm thấy giá sản phẩm (có thể sản phẩm đang hết hàng hoặc 'Liên hệ').")


def _extract_title(soup: BeautifulSoup) -> str:
    el = soup.select_one('h1[itemprop="name"]') or soup.select_one("h1")
    text = el.get_text(" ", strip=True) if el else ""
    if not text:
        raise ScrapeError("Không tìm thấy tiêu đề sản phẩm (thẻ h1).")
    return re.sub(r"\s+", " ", text)


def _html_block_to_text(el) -> str:
    """Convert an .rte HTML block to clean, structure-preserving plain text."""
    if el is None:
        return ""
    lines = []
    for node in el.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        # Avoid emitting the same text twice: a <p> inside an <li> is covered by
        # the <li>, and an <li> wrapping a nested list is covered by its items.
        if node.name != "li" and node.find_parent("li") is not None:
            continue
        if node.name == "li" and node.find("li") is not None:
            continue
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
    text = _html_block_to_text(el)
    if text.strip().upper().rstrip("?").strip() == "TRONG HỘP CÓ GÌ":
        return ""
    return text


def _extract_box_images(soup: BeautifulSoup, base_url: str) -> list:
    el = soup.select_one("#idTab3")
    if el is None:
        return []
    urls = []
    for img in el.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src and not src.startswith("data:"):
            url = _normalize_image_url(src, base_url)
            if url not in urls:
                urls.append(url)
    return urls


def _cell_text(cell: Tag) -> str:
    """Cell text with <br> kept as line breaks and whitespace collapsed."""
    for br in cell.find_all("br"):
        br.replace_with(NavigableString("\n"))
    text = cell.get_text(" ")
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _extract_specs(soup: BeautifulSoup) -> list:
    """Walk the specs tab (#idTab2) in document order. A section heading is
    either a text element outside any table, or a table row whose first cell
    has text and whose remaining cells are empty (a header row)."""
    container = soup.select_one("#idTab2 .rte") or soup.select_one("#idTab2")
    if container is None:
        return []

    sections: list = []
    state = {"heading": "Thông số kỹ thuật", "rows": []}

    def flush():
        if state["rows"]:
            sections.append(SpecSection(state["heading"], list(state["rows"])))
        state["rows"] = []

    def set_heading(text: str):
        flush()
        state["heading"] = text

    for node in container.find_all(["p", "h2", "h3", "h4", "h5", "table"]):
        if node.name == "table":
            for tr in node.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                texts = [_cell_text(c) for c in cells]
                if not any(texts):
                    continue
                if len(texts) == 1 or (texts[0] and not any(texts[1:])):
                    set_heading(texts[0])
                    continue
                state["rows"].append((texts[0], texts[1]))
        elif node.find_parent("table") is None:
            text = node.get_text(" ", strip=True)
            if text and len(text) <= 80:
                set_heading(text)
    flush()
    return sections


def _extract_images(soup: BeautifulSoup, base_url: str) -> list:
    urls: list = []
    slider = soup.select_one(".big_section.flexslider")
    if slider is None:
        raise ScrapeError("Không tìm thấy khối ảnh sản phẩm (.big_section.flexslider).")
    for li in slider.select("ul.big_thumbs.slides > li.b_thumb"):
        if "b_video" in (li.get("class") or []):
            continue  # embedded YouTube slide
        a = li.find("a", href=True)
        if not a:
            continue
        url = _normalize_image_url(a["href"], base_url)
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ScrapeError("Không tìm thấy ảnh full-size nào trong gallery sản phẩm.")
    return urls


def _extract_brand(soup: BeautifulSoup) -> str:
    for item in _iter_ld_json(soup):
        brand = item.get("brand")
        if isinstance(brand, dict) and brand.get("name"):
            return brand["name"]
        if isinstance(brand, str) and brand:
            return brand
    # flycampro.vn is a single-brand DJI distributor.
    return "DJI"


def parse_product_html(html: str, url: str) -> ProductData:
    soup = BeautifulSoup(html, "lxml")
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
        box_image_urls=_extract_box_images(soup, url),
    )


def scrape_product(url: str, timeout: int = 30) -> ProductData:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise ScrapeError(f"Trang sản phẩm không tồn tại (404): {url}") from e
        raise ScrapeError(f"Lỗi tải trang {url}: {e}") from e
    except requests.RequestException as e:
        raise ScrapeError(f"Lỗi tải trang {url}: {e}") from e
    return parse_product_html(resp.text, url)
