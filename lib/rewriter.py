"""Rewrite scraped product copy into original, sales-ready Vietnamese text via
Claude, and read "what's in the box" from product photos.

Claude returns structured JSON (enforced with output_config.format) and the
HTML is assembled here from escaped text - the model never writes markup.
The specs table is never sent for rewriting; its numbers stay as scraped.
"""
from __future__ import annotations

import base64
import json
from html import escape
from pathlib import Path

import anthropic

from .images import ImageDownloadError, fetch_image_bytes, to_vision_jpeg

# Models that accept the server-side refusal fallback parameter.
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_VISION_IMAGES = 8

SYSTEM_PROMPT = """Bạn là copywriter thương mại điện tử tiếng Việt cho vchannelstore.com - cửa hàng bán \
thiết bị DJI chính hãng.

Nhiệm vụ: viết lại HOÀN TOÀN tiêu đề và mô tả sản phẩm từ nội dung gốc được cung cấp (đổi cấu trúc câu và \
cách diễn đạt, không sao chép nguyên văn), văn phong tự nhiên, chuyên nghiệp, thuyết phục, hợp với khách mua \
online tại Việt Nam.

Nguyên tắc về độ chính xác - quan trọng vì đây là trang bán hàng thật:
- Chỉ dùng thông tin có trong nội dung gốc hoặc danh sách thông số. Không thêm tính năng, con số, khuyến mãi, \
cam kết bảo hành hay quà tặng nào không có trong đó.
- Mọi con số nhắc tới phải khớp chính xác với danh sách thông số.
- Giữ nguyên tên riêng và tên model (DJI, Osmo Pocket 4, ActiveTrack...).
- Không nhắc tới tên cửa hàng/nhà phân phối nguồn (flycampro) trong nội dung.

Cấu trúc kết quả:
- title: tên sản phẩm kèm 1-3 điểm nổi bật ngắn gọn, tối đa khoảng 90 ký tự.
- highlights: 3-6 câu ngắn nêu điểm nổi bật nhất, mỗi câu một ý.
- sections: các phần của bài mô tả. Phần đầu là đoạn mở bài với heading rỗng; các phần sau có heading ngắn \
(không viết hoa toàn bộ). Tổng độ dài tương đương bản gốc. Không đưa bảng thông số kỹ thuật vào đây."""

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "paragraphs"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "highlights", "sections"],
    "additionalProperties": False,
}

BOX_SYSTEM_PROMPT = """Bạn xem ảnh của một trang sản phẩm DJI để xác định phụ kiện đi kèm trong hộp.

Nguồn thông tin, theo thứ tự ưu tiên:
1. Ảnh chụp danh sách "Trong hộp có gì" (dạng chữ) - nếu có, chép lại đúng danh sách đó, dịch sang tiếng Việt \
nếu là tiếng Anh, giữ nguyên số lượng ghi trên ảnh.
2. Ảnh flat-lay: toàn bộ phụ kiện xếp riêng từng món trên nền trắng.

Cách liệt kê:
- Với ảnh flat-lay, chỉ liệt kê phụ kiện xung quanh (túi, dây đeo, chân đế, cáp, mic, tay cầm, nắp...), không \
liệt kê thân máy chính và không đoán số hiệu model - số hiệu đọc qua ảnh nhỏ rất dễ sai.
- Khi không đọc rõ chi tiết, mô tả chung chung ("Cáp USB-C" thay vì đoán độ dài; "Túi đựng" thay vì đoán chất liệu). \
Không ghi các phương án kiểu "A hoặc B".
- Nếu không có ảnh nào thuộc hai loại trên, trả về found = false và danh sách rỗng."""

BOX_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["found", "items"],
    "additionalProperties": False,
}


class RewriteError(RuntimeError):
    pass


def make_client(api_key: str = "") -> "anthropic.Anthropic":
    return anthropic.Anthropic(api_key=api_key or None, max_retries=3, timeout=300.0)


def _call_structured(client, model: str, system: str, content, schema: dict, max_tokens: int, what: str) -> dict:
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    use_fallback = model in FALLBACK_MODELS
    try:
        try:
            if use_fallback:
                response = client.messages.create(
                    **kwargs,
                    extra_headers={"anthropic-beta": FALLBACK_BETA},
                    extra_body={"fallbacks": "default"},
                )
            else:
                response = client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            if not use_fallback or "fallback" not in str(e.message).lower():
                raise
            response = client.messages.create(**kwargs)  # account without the fallback beta
    except anthropic.AuthenticationError as e:
        raise RewriteError("Anthropic API key không hợp lệ - kiểm tra lại trong Cài đặt.") from e
    except anthropic.NotFoundError as e:
        raise RewriteError(f"Model '{model}' không tồn tại hoặc tài khoản không có quyền dùng.") from e
    except anthropic.RateLimitError as e:
        raise RewriteError("Anthropic API đang giới hạn tốc độ (rate limit) - thử lại sau ít phút.") from e
    except anthropic.APIStatusError as e:
        raise RewriteError(f"Lỗi Anthropic API khi {what} ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RewriteError(f"Không kết nối được tới Anthropic API khi {what}: {e}") from e

    if response.stop_reason == "refusal":
        raise RewriteError(f"Claude từ chối xử lý yêu cầu {what}.")
    if response.stop_reason == "max_tokens":
        raise RewriteError(f"Nội dung trả về bị cắt giữa chừng khi {what} (vượt max_tokens).")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RewriteError(f"Claude trả về JSON không hợp lệ khi {what}: {text[:300]}") from e


def _build_user_prompt(title: str, description_text: str, spec_sections: list, box_contents_text: str) -> str:
    parts = [
        f"<tieu_de_goc>\n{title}\n</tieu_de_goc>",
        f"<mo_ta_goc>\n{description_text}\n</mo_ta_goc>",
        "<thong_so>\n" + json.dumps(spec_sections, ensure_ascii=False) + "\n</thong_so>",
    ]
    if (box_contents_text or "").strip():
        parts.append(f"<trong_hop>\n{box_contents_text.strip()}\n</trong_hop>")
    parts.append("Viết lại nội dung cho sản phẩm trên.")
    return "\n\n".join(parts)


def structured_to_html(data: dict) -> dict:
    title = " ".join(str(data.get("title", "")).split())
    highlights = [str(h).strip() for h in data.get("highlights", []) if str(h).strip()]
    blocks = []
    for section in data.get("sections", []):
        heading = str(section.get("heading", "")).strip()
        paragraphs = [str(p).strip() for p in section.get("paragraphs", []) if str(p).strip()]
        if heading and paragraphs:
            blocks.append(f"<h3>{escape(heading)}</h3>")
        blocks.extend(f"<p>{escape(p)}</p>" for p in paragraphs)
    result = {
        "title": title,
        "short_description": "<ul>" + "".join(f"<li>{escape(h)}</li>" for h in highlights) + "</ul>" if highlights else "",
        "description": "\n".join(blocks),
    }
    for key in ("title", "short_description", "description"):
        if not result[key]:
            raise RewriteError(f"Claude trả về thiếu nội dung '{key}'.")
    return result


def rewrite_content(
    client: "anthropic.Anthropic",
    model: str,
    title: str,
    description_text: str,
    spec_sections: list,
    box_contents_text: str = "",
) -> dict:
    """Return {"title", "short_description", "description"} as HTML strings."""
    data = _call_structured(
        client,
        model,
        SYSTEM_PROMPT,
        _build_user_prompt(title, description_text, spec_sections, box_contents_text),
        REWRITE_SCHEMA,
        max_tokens=16000,
        what="viết lại nội dung",
    )
    return structured_to_html(data)


def _image_block(data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(to_vision_jpeg(data)).decode("ascii"),
        },
    }


def describe_box_contents(
    client: "anthropic.Anthropic",
    model: str,
    image_paths: list,
    box_image_urls: list = (),
    box_contents_text: str = "",
) -> list:
    """Return the list of in-box accessories ([] when nothing reliable found).

    box_image_urls are images from the page's "Trong hộp có gì" tab (often a
    screenshot of the official list) and are shown to Claude first."""
    content = []
    n = 0
    for url in list(box_image_urls)[:3]:
        try:
            data = fetch_image_bytes(url)
        except ImageDownloadError:
            continue
        n += 1
        content.append({"type": "text", "text": f"Ảnh {n} (tab 'Trong hộp có gì'):"})
        content.append(_image_block(data))
    for path in image_paths:
        if n >= MAX_VISION_IMAGES:
            break
        n += 1
        content.append({"type": "text", "text": f"Ảnh {n} (gallery sản phẩm):"})
        content.append(_image_block(Path(path).read_bytes()))
    if n == 0:
        return []
    if (box_contents_text or "").strip():
        content.append({"type": "text", "text": f"Văn bản 'Trong hộp có gì' trên trang:\n{box_contents_text.strip()}"})
    content.append({"type": "text", "text": "Liệt kê phụ kiện trong hộp."})

    data = _call_structured(
        client, model, BOX_SYSTEM_PROMPT, content, BOX_SCHEMA, max_tokens=4000, what="đọc phụ kiện trong hộp"
    )
    if not data.get("found"):
        return []
    return [str(i).strip() for i in data.get("items", []) if str(i).strip()]


def check_api(api_key: str, model: str) -> str:
    """Cheap connectivity check - retrieves the model, no tokens spent."""
    client = anthropic.Anthropic(api_key=api_key or None, max_retries=0, timeout=20.0)
    try:
        info = client.models.retrieve(model)
    except anthropic.AuthenticationError as e:
        raise RewriteError("API key không hợp lệ.") from e
    except anthropic.NotFoundError as e:
        raise RewriteError(f"Không tìm thấy model '{model}'.") from e
    except anthropic.APIStatusError as e:
        raise RewriteError(f"Lỗi ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RewriteError(f"Không kết nối được: {e}") from e
    return getattr(info, "display_name", None) or model
