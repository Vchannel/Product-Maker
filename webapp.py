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
import os
import socket
import sys
import threading
import webbrowser

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PORT = 8686  # macOS reserves 5000/7000 for AirPlay Receiver


def running_instance(port: int, host: str = "127.0.0.1") -> bool:
    """True when a Product Maker is already answering on this port. Two copies
    would share one database with two job workers, and the browser would keep
    talking to whichever copy owns the familiar port."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/overview", timeout=2) as resp:
            return "stats" in json.load(resp)
    except Exception:  # noqa: BLE001 - anything else on the port is "not us"
        return False


_INSTANCE_LOCK = None


def acquire_instance_lock(data_dir) -> bool:
    """Hold an exclusive lock on data/app.lock for the life of the process, so
    a second copy can't start even when the port probe can't tell (on Windows
    SO_REUSEADDR lets two servers bind the same port)."""
    global _INSTANCE_LOCK
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "app.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _INSTANCE_LOCK = handle
    return True


def find_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in [preferred, *range(preferred + 1, preferred + 30)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Same option the web server uses: without it, connections from a
            # just-closed instance (TIME_WAIT) make a free port look busy and the
            # app silently moves to another port.
            if os.name == "nt":
                # Windows: SO_REUSEADDR would let us "bind" a port another server owns.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

    host = "127.0.0.1"
    if running_instance(args.port, host):
        url = f"http://{host}:{args.port}"
        print(f"\n  Product Maker đã đang chạy tại {url} - mở lại cửa sổ đó thay vì chạy thêm bản thứ hai.")
        print("  (Muốn khởi động lại: đóng cửa sổ dòng lệnh đang chạy app, rồi chạy lại.)\n")
        if not args.no_browser:
            webbrowser.open(url)
        return

    from lib import settings
    from web import create_app

    if not acquire_instance_lock(settings.DATA_DIR):
        url = f"http://{host}:{args.port}"
        print(f"\n  Product Maker đã đang chạy (một cửa sổ khác đang giữ app). Mở {url}")
        print("  Muốn khởi động lại: đóng cửa sổ dòng lệnh đang chạy app, rồi chạy lại.\n")
        if not args.no_browser:
            webbrowser.open(url)
        return

    port = find_port(args.port, host)
    url = f"http://{host}:{port}"
    app = create_app()

    print("", flush=True)
    print("  ┌──────────────────────────────────────────────┐")
    print("  │  Product Maker đang chạy                     │")
    print(f"  │  Mở trình duyệt tại: {url:<24}│")
    print("  │  Giữ cửa sổ này mở. Nhấn Ctrl+C để tắt.      │")
    print("  └──────────────────────────────────────────────┘", flush=True)
    print("", flush=True)

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
