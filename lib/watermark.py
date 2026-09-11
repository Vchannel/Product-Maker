"""Remove the flycampro.vn logo from downloaded product photos.

Every product photo on flycampro.vn carries a "FLYCAM PRO.VN" logo in the
top-left corner on a plain white background. Rather than cropping (which
would shift/resize the whole image and risks clipping real product content
on layouts where items sit close to that corner), this auto-detects the
logo's bounding box and paints over it with the sampled background color -
same canvas size, no layout shift.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

SEARCH_FRAC_W = 0.22
SEARCH_FRAC_H = 0.28
BG_TOLERANCE = 24  # per-channel; a pixel counts as "logo" if it differs more than this from bg
PADDING = 6


def _sample_background_color(arr: np.ndarray) -> np.ndarray:
    """Sample from the top-right corner, which is never covered by the
    top-left logo or by centered/right-leaning product content."""
    h, w, _ = arr.shape
    patch = arr[2 : min(10, h), max(0, w - 10) : w]
    return patch.reshape(-1, 3).mean(axis=0)


def remove_top_left_logo(image_path: str) -> bool:
    """Detect and paint over the top-left logo in place. Returns True if a
    logo-like region was found and removed, False if the image was already
    clean (safe to call repeatedly / on already-cleaned files)."""
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape

    bg = _sample_background_color(arr)

    sh = int(h * SEARCH_FRAC_H)
    sw = int(w * SEARCH_FRAC_W)
    region = arr[0:sh, 0:sw].astype(int)
    diff = np.abs(region - bg.astype(int)).sum(axis=2)
    mask = diff > BG_TOLERANCE * 3

    if not mask.any():
        return False

    ys, xs = np.where(mask)
    y0, y1 = max(0, int(ys.min()) - PADDING), min(sh - 1, int(ys.max()) + PADDING)
    x0, x1 = max(0, int(xs.min()) - PADDING), min(sw - 1, int(xs.max()) + PADDING)

    draw = ImageDraw.Draw(img)
    draw.rectangle([x0, y0, x1, y1], fill=tuple(int(c) for c in bg))
    img.save(image_path)
    return True
