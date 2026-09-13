"""Clients for the WordPress REST API (wp/v2) and WooCommerce REST API (wc/v3).

These are two separate credential systems on the same site:
  - wc/v3/*  auth: WooCommerce Consumer Key/Secret (WC_CONSUMER_KEY/SECRET)
  - wp/v2/*  auth: a WordPress user + Application Password (WP_USERNAME/WP_APP_PASSWORD)
WooCommerce keys are NOT valid credentials for wp/v2 endpoints (e.g. media
upload), so both must be configured.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

import requests

USER_AGENT = "ProductMaker/2.0 (+vchannelstore importer)"


class WooCommerceAPIError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def _describe_error(resp: Optional[requests.Response]) -> tuple:
    """(human message, status, code) from a WordPress/WooCommerce error body."""
    if resp is None:
        return "", None, ""
    code, message = "", ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            code = str(body.get("code", ""))
            message = str(body.get("message", ""))
    except ValueError:
        message = resp.text[:300]
    hints = {
        401: "Sai thông tin xác thực (key/secret hoặc Application Password).",
        403: "Tài khoản không đủ quyền cho thao tác này.",
        404: "Không tìm thấy endpoint - kiểm tra địa chỉ website và permalink của WordPress.",
    }
    if code in ("product_invalid_sku", "woocommerce_rest_product_not_created") and "sku" in message.lower():
        message += " (SKU đã được dùng bởi sản phẩm/biến thể khác, kể cả sản phẩm trong thùng rác.)"
    human = message or hints.get(resp.status_code, "")
    if resp.status_code in hints and message and resp.status_code != 404:
        human = f"{hints[resp.status_code]} {message}"
    return human, resp.status_code, code


class _Base:
    def __init__(self, base: str, auth: tuple, timeout: int):
        self.base = base
        self.auth = auth
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _request(self, method: str, path: str, what: str, ok_404: bool = False, **kwargs):
        url = f"{self.base}{path}"
        try:
            resp = self.session.request(method, url, auth=self.auth, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise WooCommerceAPIError(f"Không kết nối được tới website khi {what}: {e}") from e
        if ok_404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            human, status, code = _describe_error(resp)
            raise WooCommerceAPIError(f"Lỗi khi {what} (HTTP {status}): {human}", status, code)
        return resp


class WPMediaClient(_Base):
    def __init__(self, site_url: str, username: str, app_password: str, timeout: int = 120):
        super().__init__(site_url.rstrip("/") + "/wp-json/wp/v2", (username, app_password), timeout)

    def upload_media(self, file_path: str, title: str = "", alt_text: str = "") -> dict:
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        resp = self._request(
            "POST",
            "/media",
            f"upload ảnh {path.name}",
            headers={"Content-Disposition": f'attachment; filename="{path.name}"', "Content-Type": mime},
            data=path.read_bytes(),
        )
        media = resp.json()
        patch = {k: v for k, v in (("title", title), ("alt_text", alt_text)) if v}
        if patch:
            try:
                self._request("POST", f"/media/{media['id']}", "cập nhật tiêu đề ảnh", json=patch)
            except WooCommerceAPIError:
                pass  # metadata is best-effort
        return media

    def get_media(self, media_id: int) -> Optional[dict]:
        resp = self._request(
            "GET", f"/media/{media_id}", "kiểm tra ảnh", ok_404=True, params={"_fields": "id,source_url"}
        )
        return resp.json() if resp is not None else None

    def whoami(self) -> dict:
        resp = self._request("GET", "/users/me", "kiểm tra tài khoản WordPress", params={"context": "edit"})
        return resp.json()


class WooCommerceClient(_Base):
    def __init__(self, site_url: str, consumer_key: str, consumer_secret: str, timeout: int = 60):
        super().__init__(site_url.rstrip("/") + "/wp-json/wc/v3", (consumer_key, consumer_secret), timeout)

    # --- taxonomy -----------------------------------------------------------
    def list_categories(self) -> list:
        out, page = [], 1
        while True:
            resp = self._request(
                "GET",
                "/products/categories",
                "tải danh sách category",
                params={"per_page": 100, "page": page, "orderby": "name", "hide_empty": "false"},
            )
            batch = resp.json()
            out.extend(batch)
            if len(batch) < 100 or page >= 20:
                return out
            page += 1

    def find_category_by_name(self, name: str) -> Optional[dict]:
        """Exact (case-insensitive) match on name or slug - never a fuzzy
        'first search result', which silently assigned wrong categories."""
        resp = self._request("GET", "/products/categories", f"tìm category '{name}'", params={"search": name, "per_page": 100})
        wanted = name.strip().lower()
        for c in resp.json():
            if c.get("name", "").strip().lower() == wanted or c.get("slug", "").lower() == wanted:
                return c
        return None

    def find_brand_by_name(self, name: str) -> Optional[dict]:
        """Exact match in WooCommerce's native Brands taxonomy. None when there
        is no match or the site has no Brands feature (brand is best-effort)."""
        try:
            resp = self._request("GET", "/products/brands", "tìm brand", ok_404=True, params={"search": name})
        except WooCommerceAPIError:
            return None
        if resp is None:
            return None
        for b in resp.json():
            if b.get("name", "").strip().lower() == name.strip().lower():
                return b
        return None

    # --- products -----------------------------------------------------------
    def find_product_by_sku(self, sku: str) -> Optional[dict]:
        resp = self._request("GET", "/products", f"tìm sản phẩm theo SKU '{sku}'", params={"sku": sku, "status": "any"})
        results = resp.json()
        return results[0] if results else None

    def get_product(self, product_id: int) -> Optional[dict]:
        resp = self._request("GET", f"/products/{product_id}", f"lấy sản phẩm #{product_id}", ok_404=True)
        return resp.json() if resp is not None else None

    def list_products(self, ids: list) -> list:
        out = []
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            resp = self._request(
                "GET",
                "/products",
                "đồng bộ sản phẩm",
                params={"include": ",".join(str(i) for i in chunk), "per_page": 100, "status": "any"},
            )
            out.extend(resp.json())
        return out

    def create_product(self, payload: dict) -> dict:
        return self._request("POST", "/products", "tạo sản phẩm", json=payload).json()

    def update_product(self, product_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/products/{product_id}", f"cập nhật sản phẩm #{product_id}", json=payload).json()

    # --- variations ---------------------------------------------------------
    def list_variations(self, parent_id: int) -> list:
        out, page = [], 1
        while True:
            resp = self._request(
                "GET",
                f"/products/{parent_id}/variations",
                "tải danh sách biến thể",
                params={"per_page": 100, "page": page},
            )
            batch = resp.json()
            out.extend(batch)
            if len(batch) < 100 or page >= 10:
                return out
            page += 1

    def create_variation(self, parent_id: int, payload: dict) -> dict:
        return self._request("POST", f"/products/{parent_id}/variations", "tạo biến thể", json=payload).json()

    def update_variation(self, parent_id: int, variation_id: int, payload: dict) -> dict:
        return self._request(
            "PUT", f"/products/{parent_id}/variations/{variation_id}", "cập nhật biến thể", json=payload
        ).json()

    def check(self) -> int:
        """Connectivity/permission check. Returns the store's product count."""
        resp = self._request("GET", "/products", "kiểm tra WooCommerce", params={"per_page": 1, "status": "any"})
        try:
            return int(resp.headers.get("X-WP-Total", "0"))
        except ValueError:
            return 0
