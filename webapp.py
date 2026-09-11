#!/usr/bin/env python3
"""Product Maker - web app nhập sản phẩm flycampro.vn → WooCommerce.

Cách dùng:
    python webapp.py              # mở http://127.0.0.1:8686 và tự bật trình duyệt
    python webapp.py --port 9000  # đổi cổng
    python webapp.py --no-browser

Web app chỉ chạy trên máy này (127.0.0.1) vì nó giữ API key và mật khẩu của cửa hàng.
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PORT = 8686  # macOS reserves 5000/7000 for AirPlay Receiver


def find_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in [preferred, *range(preferred + 1, preferred + 30)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"Không tìm được cổng trống từ {preferred} tới {preferred + 29}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Product Maker web app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Không tự mở trình duyệt")
    parser.add_argument("--debug", action="store_true", help="Chạy Flask dev server (tự reload khi sửa code)")
    args = parser.parse_args()

    from web import create_app

    host = "127.0.0.1"
    port = find_port(args.port, host)
    url = f"http://{host}:{port}"
    app = create_app()

    print("")
    print("  ┌──────────────────────────────────────────────┐")
    print("  │  Product Maker đang chạy                     │")
    print(f"  │  Mở trình duyệt tại: {url:<24}│")
    print("  │  Giữ cửa sổ này mở. Nhấn Ctrl+C để tắt.      │")
    print("  └──────────────────────────────────────────────┘")
    print("")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    if args.debug:
        app.run(host=host, port=port, debug=True, use_reloader=False, threaded=True)
        return
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port, threaded=True)
    else:
        serve(app, host=host, port=port, threads=8, ident="ProductMaker")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã tắt Product Maker.")
