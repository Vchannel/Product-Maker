"""HTML helpers for product content: allowlist sanitizing and safe builders.

Everything that ends up in a WooCommerce description passes through here -
scraped strings, Claude output, and edits typed into the review editor - so a
stray "<" or quote in any of them can't break the page markup.
"""
from __future__ import annotations

from html import escape

from bs4 import BeautifulSoup, NavigableString

ALLOWED_TAGS = {
    "p", "br", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "ul", "ol", "li",
    "a", "blockquote", "table", "thead", "tbody", "tr", "td", "th", "img", "span",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "width", "height", "style"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "table": {"class"},
}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg", "noscript"}


def _safe_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith(("http://", "https://", "/", "#", "mailto:"))


def sanitize_html(html: str) -> str:
    """Keep only simple formatting tags/attributes; unwrap anything else
    (its text is kept) and drop active content entirely."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        name = tag.name.lower()
        if name in DROP_WITH_CONTENT:
            tag.decompose()
            continue
        if name == "div":
            tag.name = "p"
            name = "p"
        if name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = ALLOWED_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
            elif attr in ("href", "src") and not _safe_url(tag.attrs[attr]):
                del tag.attrs[attr]
        if name == "a" and tag.get("target") == "_blank":
            tag["rel"] = "noopener"
    # Drop empty paragraphs left over from editors (<p><br></p>, <p> </p>).
    for p in soup.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()
    return str(soup).strip()


def text_to_html_lines(text: str) -> str:
    """Escape plain text, turning newlines into <br>."""
    return "<br>".join(escape(line) for line in (text or "").split("\n"))


def build_specs_html(spec_sections: list, heading: str = "Thông số kỹ thuật") -> str:
    sections = [s for s in spec_sections or [] if s.get("rows")]
    if not sections:
        return ""
    parts = [f"<h3>{escape(heading)}</h3>"]
    for section in sections:
        if section.get("heading"):
            parts.append(f"<h4>{escape(section['heading'])}</h4>")
        parts.append('<table class="product-specs"><tbody>')
        for label, value in section["rows"]:
            parts.append(f"<tr><td>{text_to_html_lines(label)}</td><td>{text_to_html_lines(value)}</td></tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def build_list_html(items: list, title: str = "") -> str:
    items = [str(i).strip() for i in items or [] if str(i).strip()]
    if not items:
        return ""
    head = f"<p><strong>{escape(title)}</strong></p>" if title else ""
    return head + "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"


def interleave_images(description_html: str, image_urls: list, alt_text: str) -> str:
    """Spread gallery images evenly between the description's top-level
    blocks. The opening block stays text-only; leftovers go at the end."""
    if not image_urls:
        return description_html

    soup = BeautifulSoup(description_html or "", "html.parser")
    blocks = [str(c) for c in soup.contents if not (isinstance(c, NavigableString) and not c.strip())]
    if not blocks:
        blocks = [description_html] if description_html else []

    def img_tag(url: str) -> str:
        return (
            f'<p><img src="{escape(url, quote=True)}" alt="{escape(alt_text, quote=True)}" '
            'style="max-width:100%;height:auto;" /></p>'
        )

    img_iter = iter(image_urls)
    gap = max(1, len(blocks) // len(image_urls)) if blocks else 1
    result = []
    since_last = 0
    for i, block in enumerate(blocks):
        result.append(block)
        if i == 0:
            continue
        since_last += 1
        if since_last >= gap:
            url = next(img_iter, None)
            if url is not None:
                result.append(img_tag(url))
            since_last = 0
    for url in img_iter:
        result.append(img_tag(url))
    return "\n".join(result)


def html_to_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
