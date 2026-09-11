"""Download full-size product images to a local folder, resumably."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests

from .scraper import USER_AGENT
from .watermark import remove_top_left_logo

VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


class ImageDownloadError(RuntimeError):
    pass


def _filename_for(url: str, slug: str, index: int) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in VALID_EXTENSIONS:
        ext = ".jpg"
    return f"{slug}-{index:02d}{ext}"


def download_images(image_urls: list, dest_dir: Path, slug: str, timeout: int = 60) -> list:
    """Download each URL to dest_dir. Already-downloaded files are skipped,
    so re-running after a partial failure only fetches what's missing."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, url in enumerate(image_urls, start=1):
        filename = _filename_for(url, slug, i)
        local_path = dest_dir / filename

        if not (local_path.exists() and local_path.stat().st_size > 0):
            tmp_path = local_path.with_name(local_path.name + ".part")
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=timeout)
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                tmp_path.replace(local_path)
            except (requests.RequestException, OSError) as e:
                tmp_path.unlink(missing_ok=True)
                raise ImageDownloadError(f"Lỗi tải ảnh {url}: {e}") from e

        try:
            remove_top_left_logo(str(local_path))
        except Exception as e:
            raise ImageDownloadError(f"Lỗi xử lý xóa logo trên ảnh {filename}: {e}") from e

        results.append({"url": url, "path": str(local_path), "filename": filename})
    return results
