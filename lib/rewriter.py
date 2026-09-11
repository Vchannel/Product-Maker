"""Rewrite scraped product copy into original, sales-ready Vietnamese text via Claude.

Only free-text (title/description) is sent to the model. The specs table is
handled separately in Python and is never rewritten by the model - its
numbers must stay exactly as scraped.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path

import anthropic

BOX_CONTENTS_SYSTEM_PROMPT = """Bạn xem các ảnh sản phẩm được đánh số theo thứ tự trên trang flycampro.vn.
Một trong số đó THƯỜNG (không phải luôn luôn) là ảnh flat-lay chụp toàn bộ phụ kiện đi kèm trong hộp,
xếp riêng từng món trên nền trắng.

Nhiệm vụ: tìm đúng ảnh đó (nếu có) và liệt kê CHÍNH XÁC những PHỤ KIỆN đi kèm nhìn thấy được trong
ảnh đó, dưới dạng danh sách tiếng Việt.

QUY TẮC BẮT BUỘC:
- KHÔNG liệt kê thiết bị camera/gimbal chính (thân máy chính) - đó là sản phẩm chính, không phải
  phụ kiện, và bạn KHÔNG được đoán tên/số hiệu model của nó (rất dễ đọc nhầm số hiệu qua ảnh nhỏ).
  Chỉ liệt kê những món phụ kiện xung quanh: túi đựng, dây đeo, chân đế, cáp, mic, tay cầm, ốp, v.v.
- Không suy đoán số lượng hay thông số chi tiết nếu không đọc rõ được chữ trên vật thể - khi đó mô
  tả chung chung (vd: "Dây cáp USB-C" thay vì đoán độ dài dây, "Túi đựng" thay vì đoán chất liệu).
- Nếu không có ảnh nào rõ ràng là ảnh flat-lay phụ kiện, trả về danh sách rỗng.

Trả lời DUY NHẤT bằng JSON object hợp lệ, không kèm markdown code fence:
{"found": true/false, "items": ["Vật thể 1", "Vật thể 2", ...]}"""

SYSTEM_PROMPT = """Bạn là copywriter thương mại điện tử tiếng Việt cho vchannelstore.com, \
chuyên bán lại thiết bị DJI chính hãng nhập từ nhà phân phối flycampro.vn.

Nhiệm vụ: viết lại HOÀN TOÀN (không sao chép nguyên văn, đổi cấu trúc câu và cách diễn đạt) \
tiêu đề và mô tả sản phẩm dựa trên nội dung gốc được cung cấp, giữ văn phong tự nhiên, \
chuyên nghiệp, thuyết phục, phù hợp bán hàng online tại Việt Nam.

QUY TẮC BẮT BUỘC:
- Không bịa thêm bất kỳ thông số, tính năng, hay con số nào không có trong nội dung gốc hoặc \
danh sách thông số kỹ thuật được cung cấp.
- Nếu nhắc tới thông số kỹ thuật trong bài viết, số liệu phải khớp chính xác với danh sách thông số.
- Không thêm khuyến mãi, cam kết bảo hành, hay bất kỳ thông tin nào không có trong nội dung gốc.
- Giữ nguyên các tên riêng (DJI, tên dòng sản phẩm...).

Trả lời DUY NHẤT bằng một JSON object hợp lệ, không kèm markdown code fence, không giải thích \
gì thêm, đúng format sau:
{
  "title": "Tiêu đề sản phẩm viết lại",
  "short_description": "<ul><li>...</li>...</ul>",
  "description": "<p>...</p><p>...</p>..."
}
"short_description" là 3-6 bullet điểm nổi bật nhất, viết dạng thẻ <ul><li>.
"description" là mô tả chi tiết dạng các đoạn <p>, có thể thêm <h3> cho tiêu đề phụ nếu hợp lý, \
độ dài tương đương bản gốc, KHÔNG bao gồm bảng thông số kỹ thuật (phần đó được thêm riêng)."""


class RewriteError(RuntimeError):
    pass


def _build_user_prompt(title: str, description_text: str, spec_sections: list, box_contents_text: str) -> str:
    specs_json = json.dumps(spec_sections, ensure_ascii=False)
    parts = [
        f"TIÊU ĐỀ GỐC:\n{title}\n",
        f"MÔ TẢ GỐC:\n{description_text}\n",
        "DANH SÁCH THÔNG SỐ KỸ THUẬT (chỉ để tham chiếu / kiểm tra số liệu, "
        f"không đưa nguyên bảng vào description):\n{specs_json}\n",
    ]
    box_text = (box_contents_text or "").strip()
    if box_text and box_text.upper() != "TRONG HỘP CÓ GÌ?":
        parts.append(f"TRONG HỘP CÓ GÌ:\n{box_text}\n")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RewriteError(
            f"Claude không trả về JSON hợp lệ: {e}\nRaw response (rút gọn): {text[:1000]}"
        ) from e


def rewrite_content(
    client: "anthropic.Anthropic",
    model: str,
    title: str,
    description_text: str,
    spec_sections: list,
    box_contents_text: str = "",
) -> dict:
    user_prompt = _build_user_prompt(title, description_text, spec_sections, box_contents_text)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIStatusError as e:
        raise RewriteError(f"Lỗi gọi Anthropic API ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RewriteError(f"Lỗi kết nối tới Anthropic API: {e}") from e

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise RewriteError("Claude không trả về nội dung text nào.")

    data = _parse_json_response(text)
    for key in ("title", "short_description", "description"):
        if key not in data or not str(data[key]).strip():
            raise RewriteError(f"Thiếu field '{key}' trong JSON trả về từ Claude.")
    return data


def describe_box_contents(client: "anthropic.Anthropic", model: str, image_paths: list) -> str:
    """Ask Claude to find the accessories flat-lay among the given images (if
    any) and list what's visibly in it, as an HTML bullet list. Returns ""
    when no such image is found - callers should fall back gracefully."""
    content = []
    for i, path in enumerate(image_paths, start=1):
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        data = base64.standard_b64encode(p.read_bytes()).decode("utf-8")
        content.append({"type": "text", "text": f"Ảnh {i}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
    content.append({"type": "text", "text": "Tìm ảnh flat-lay phụ kiện (nếu có) và liệt kê nội dung."})

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=BOX_CONTENTS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIStatusError as e:
        raise RewriteError(f"Lỗi gọi Anthropic API (vision, {e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RewriteError(f"Lỗi kết nối tới Anthropic API (vision): {e}") from e

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise RewriteError("Claude không trả về nội dung text nào (vision).")

    data = _parse_json_response(text)
    if not data.get("found") or not data.get("items"):
        return ""
    items = [str(item).strip() for item in data["items"] if str(item).strip()]
    if not items:
        return ""
    lis = "".join(f"<li>{item}</li>" for item in items)
    return f"<p><strong>Trong hộp có gì:</strong></p><ul>{lis}</ul>"
