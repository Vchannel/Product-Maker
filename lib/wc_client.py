"""Clients for the WordPress REST API (wp/v2) and WooCommerce REST API (wc/v3).

These are two separate credential systems on the same site:
  - wc/v3/*  auth: WooCommerce Consumer Key/Secret (WC_CONSUMER_KEY/SECRET)
  - wp/v2/*  auth: a WordPress user + Application Password (WP_USERNAME/WP_APP_PASSWORD)
WooCommerce keys are NOT valid credentials for wp/v2 endpoints (e.g. media
upload), so both must be configured. See README.md for how to generate each.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

import requests


class WooCommerceAPIError(RuntimeError):
    pass


class WPMediaClient:
    def __init__(self, site_url: str, username: str, app_password: str, timeout: int = 60):
        self.base = site_url.rstrip("/") + "/wp-json/wp/v2"
        self.auth = (username, app_password)
        self.timeout = timeout

    def upload_media(self, file_path: str, title: str = "", alt_text: str = "") -> dict:
        path = Path(file_path)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            data = path.read_bytes()
            resp = requests.post(
                f"{self.base}/media",
                headers={
                    "Content-Disposition": f'attachment; filename="{path.name}"',
                    "Content-Type": mime,
                },
                data=data,
                auth=self.auth,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            detail = f" | Response: {e.response.text[:500]}" if e.response is not None else ""
            raise WooCommerceAPIError(f"Lỗi upload ảnh {path.name} lên WP media: {e}{detail}") from e

        media = resp.json()
        if title or alt_text:
            patch = {}
            if title:
                patch["title"] = title
            if alt_text:
                patch["alt_text"] = alt_text
            try:
                r2 = requests.post(
                    f"{self.base}/media/{media['id']}", json=patch, auth=self.auth, timeout=self.timeout
                )
                r2.raise_for_status()
            except requests.RequestException:
                pass  # metadata patch is best-effort; không chặn luồng chính
        return media


class WooCommerceClient:
    def __init__(self, site_url: str, consumer_key: str, consumer_secret: str, timeout: int = 60):
        self.base = site_url.rstrip("/") + "/wp-json/wc/v3"
        self.auth = (consumer_key, consumer_secret)
        self.timeout = timeout

    def find_brand_by_name(self, name: str) -> Optional[dict]:
        """Look up a term in the WooCommerce native Brands taxonomy
        (wc/v3/products/brands) by name. Returns None both when no match is
        found and when the site has no Brands feature installed (404) -
        brand assignment is a nice-to-have, never worth failing an import over."""
        try:
            resp = requests.get(
                f"{self.base}/products/brands", params={"search": name}, auth=self.auth, timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except requests.RequestException:
            return None
        results = resp.json()
        for b in results:
            if b.get("name", "").lower() == name.lower():
                return b
        return results[0] if results else None

    def find_category_by_name(self, name: str) -> Optional[dict]:
        try:
            resp = requests.get(
                f"{self.base}/products/categories", params={"search": name}, auth=self.auth, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise WooCommerceAPIError(f"Lỗi tìm category '{name}': {e}") from e
        results = resp.json()
        for c in results:
            if c.get("name", "").lower() == name.lower():
                return c
        return results[0] if results else None

    def get_category(self, category_id: int) -> Optional[dict]:
        try:
            resp = requests.get(f"{self.base}/products/categories/{category_id}", auth=self.auth, timeout=self.timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except requests.RequestException as e:
            raise WooCommerceAPIError(f"Lỗi lấy category id={category_id}: {e}") from e
        return resp.json()

    def find_product_by_sku(self, sku: str) -> Optional[dict]:
        try:
            resp = requests.get(
                f"{self.base}/products", params={"sku": sku}, auth=self.auth, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise WooCommerceAPIError(f"Lỗi tìm sản phẩm theo SKU '{sku}': {e}") from e
        results = resp.json()
        return results[0] if results else None

    def create_product(self, payload: dict) -> dict:
        try:
            resp = requests.post(
                f"{self.base}/products", json=payload, auth=self.auth, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            detail = f" | Response: {e.response.text[:500]}" if e.response is not None else ""
            raise WooCommerceAPIError(f"Lỗi tạo sản phẩm trên WooCommerce: {e}{detail}") from e
        return resp.json()

    def find_variation_by_sku(self, parent_id: int, sku: str) -> Optional[dict]:
        try:
            resp = requests.get(
                f"{self.base}/products/{parent_id}/variations",
                params={"sku": sku},
                auth=self.auth,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise WooCommerceAPIError(f"Lỗi tìm variation theo SKU '{sku}': {e}") from e
        results = resp.json()
        return results[0] if results else None

    def create_variation(self, parent_id: int, payload: dict) -> dict:
        try:
            resp = requests.post(
                f"{self.base}/products/{parent_id}/variations",
                json=payload,
                auth=self.auth,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            detail = f" | Response: {e.response.text[:500]}" if e.response is not None else ""
            raise WooCommerceAPIError(f"Lỗi tạo variation trên WooCommerce: {e}{detail}") from e
        return resp.json()
