#!/usr/bin/env python3
"""Nhập sản phẩm từ flycampro.vn lên WooCommerce (vchannelstore.com) - bản dòng lệnh.

Cách dùng:
    python import_product.py <link> [<link> ...] [--discount 45000] [--status draft]
                             [--category "Gimbal camera"] [--update] [--force-scrape] [--force-rewrite]

Dòng lệnh chạy thẳng từ đầu tới cuối, không có bước duyệt. Muốn xem/sửa nội
dung trước khi đăng, dùng web app: python webapp.py
"""
from __future__ import annotations

import argparse
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from lib import pipeline, settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import sản phẩm từ flycampro.vn lên WooCommerce")
    parser.add_argument("urls", nargs="+", help="1 link = sản phẩm đơn; 2+ link cùng dòng máy = 1 sản phẩm nhiều phiên bản")
    parser.add_argument("--discount", type=int, default=None, help="Số tiền (VND) trừ vào giá gốc để ra giá khuyến mãi")
    parser.add_argument("--status", choices=["draft", "pending", "publish"], default=None)
    parser.add_argument("--category", default="", help="Tên category có sẵn trên site, cách nhau bởi dấu phẩy")
    parser.add_argument("--update", action="store_true", help="Nếu sản phẩm đã có: ghi đè nội dung/giá (mặc định: chỉ thêm phần còn thiếu)")
    parser.add_argument("--no-box", action="store_true", help="Bỏ qua bước AI đọc phụ kiện trong hộp")
    parser.add_argument("--force-scrape", action="store_true", help="Bỏ qua cache, tải lại trang gốc")
    parser.add_argument("--force-rewrite", action="store_true", help="Bỏ qua cache, gọi lại AI viết nội dung")
    args = parser.parse_args()

    settings.load_env()
    missing = settings.missing_keys()
    if missing:
        print(f"Thiếu cấu hình trong .env: {', '.join(missing)} (hoặc điền trong trang Cài đặt của web app).")
        return 1

    options = pipeline.PrepareOptions.from_dict(
        {
            "discount_vnd": args.discount,
            "status": args.status,
            "categories": [c for c in args.category.split(",") if c.strip()],
            "force_scrape": args.force_scrape,
            "force_rewrite": args.force_rewrite,
            "read_box": not args.no_box,
            "on_exists": "update" if args.update else "skip",
        }
    )
    try:
        draft = pipeline.prepare(args.urls, options)
        result = pipeline.publish(draft)
    except (pipeline.DraftError, *pipeline.PIPELINE_ERRORS) as e:
        print(f"\nLỖI: {e}")
        print("Chạy lại đúng lệnh này để tiếp tục từ bước đã lưu cache.")
        return 1
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        return 130

    print(f"\nHoàn tất ({result['action']}) - sản phẩm #{result['product_id']}")
    print(f"  Sửa trên WordPress: {result['edit_link']}")
    if result.get("permalink"):
        print(f"  Xem trên cửa hàng:  {result['permalink']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
