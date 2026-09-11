"""Download full-size product images, keep the originals, and produce
logo-free copies - resumably.

Layout under each product's cache folder:
  images/_original/<slug>-01.png   untouched download
  images/<slug>-01.png             logo removed (what gets uploaded)
  images/_processed.json           per-file record: algorithm version, box, sha1

Images processed by an older watermark algorithm (no record, or an older
version) are re-processed from a fresh original download.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from PIL import Image

from .scraper import USER_AGENT
from .watermark import ALGORITHM_VERSION, remove_logo

VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


class ImageDownloadError(RuntimeError):
    pass


def filename_for(url: str, slug: str, index: int) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in VALID_EXTENSIONS:
        ext = ".jpg"
    return f"{slug}-{index:02d}{ext}"


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, timeout: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=timeout)
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        with Image.open(tmp) as im:  # reject HTML error pages saved as .png
            im.verify()
        tmp.replace(dest)
    except (requests.RequestException, OSError, Image.UnidentifiedImageError) as e:
        tmp.unlink(missing_ok=True)
        raise ImageDownloadError(f"Lỗi tải ảnh {url}: {e}") from e


def download_images(
    image_urls: list,
    dest_dir: Path,
    slug: str,
    timeout: int = 60,
    on_progress: Optional[Callable[[int, int], None]] = None,
    force: bool = False,
) -> list:
    """Return [{url, path, filename, original_path, logo_removed, sha1}] in
    gallery order. Already-processed files are reused."""
    dest_dir = Path(dest_dir)
    originals = dest_dir / "_original"
    originals.mkdir(parents=True, exist_ok=True)
    record_path = dest_dir / "_processed.json"
    try:
        records = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    except (OSError, ValueError):
        records = {}

    results = []
    total = len(image_urls)
    for i, url in enumerate(image_urls, start=1):
        filename = filename_for(url, slug, i)
        clean_path = dest_dir / filename
        orig_path = originals / filename
        rec = records.get(filename) or {}

        up_to_date = (
            not force
            and rec.get("v") == ALGORITHM_VERSION
            and rec.get("url") == url
            and clean_path.exists()
            and orig_path.exists()
        )
        if not up_to_date:
            if force or not orig_path.exists() or rec.get("url") not in (None, url):
                _download(url, orig_path, timeout)
            try:
                result = remove_logo(str(orig_path), str(clean_path))
            except Exception as e:  # noqa: BLE001 - any PIL/numpy failure
                raise ImageDownloadError(f"Lỗi xử lý xoá logo trên ảnh {filename}: {e}") from e
            rec = {
                "v": ALGORITHM_VERSION,
                "url": url,
                "logo_removed": result.removed,
                "box": list(result.box) if result.box else None,
                "sha1": sha1_file(clean_path),
            }
            records[filename] = rec
            record_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

        results.append(
            {
                "url": url,
                "path": str(clean_path),
                "original_path": str(orig_path),
                "filename": filename,
                "logo_removed": bool(rec.get("logo_removed")),
                "sha1": rec.get("sha1") or sha1_file(clean_path),
            }
        )
        if on_progress:
            on_progress(i, total)
    return results


def fetch_image_bytes(url: str, timeout: int = 30) -> bytes:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        raise ImageDownloadError(f"Lỗi tải ảnh {url}: {e}") from e


def to_vision_jpeg(data: bytes, max_side: int = 1568) -> bytes:
    """Downscale for the vision API (keeps requests well under the per-image
    size limit and cuts token cost) and flatten transparency onto white."""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            canvas = Image.new("RGB", im.size, (255, 255, 255))
            canvas.paste(im, mask=im.split()[-1])
            im = canvas
        else:
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
