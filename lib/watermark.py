"""Remove the flycampro.vn logo from downloaded product photos.

Product photos on flycampro.vn carry a "FLYCAM PRO.VN" logo near the top-left
corner on a plain white background. The logo region is painted over with the
sampled background colour (same canvas, no crop, no layout shift).

Detection is deliberately conservative - a photo is left untouched unless the
top-left area really looks like an isolated logo on a flat background:
  * the background sample must be near-uniform;
  * non-background pixels are grouped into horizontal/vertical clusters, and
    clusters that run into the search window's edge (i.e. the product itself
    reaching into the corner) are ignored;
  * the remaining box must have a plausible logo size.
The first version painted one rectangle over *every* non-white pixel in the
window, which also erased parts of cameras that reached into that corner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

ALGORITHM_VERSION = 2

SEARCH_FRAC_W = 0.24
SEARCH_FRAC_H = 0.30
BG_TOLERANCE = 24  # per channel
BG_MAX_STD = 6.0
GAP_FRAC = 0.012  # min empty gap (fraction of width) that separates clusters
PADDING = 6
MIN_LOGO_FRAC = 0.03
MAX_LOGO_W_FRAC = 0.20
MAX_LOGO_H_FRAC = 0.24


@dataclass
class LogoResult:
    removed: bool
    box: Optional[tuple] = None  # (x0, y0, x1, y1)
    reason: str = ""


def _runs(active: np.ndarray, min_gap: int) -> list:
    """Split a 1-D boolean array into [start, end] runs, merging runs whose
    gap is smaller than min_gap."""
    idx = np.flatnonzero(active)
    if idx.size == 0:
        return []
    runs = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        i = int(i)
        if i - runs[-1][1] <= min_gap:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return runs


def detect_logo(img: Image.Image) -> LogoResult:
    arr = np.asarray(img.convert("RGB"))
    h, w, _ = arr.shape
    if w < 200 or h < 200:
        return LogoResult(False, reason="ảnh quá nhỏ")

    patch = arr[2 : min(12, h), max(0, w - 12) : w].reshape(-1, 3).astype(float)
    bg = patch.mean(axis=0)
    if patch.std(axis=0).max() > BG_MAX_STD:
        return LogoResult(False, reason="nền không đồng nhất")

    sh, sw = int(h * SEARCH_FRAC_H), int(w * SEARCH_FRAC_W)
    region = arr[:sh, :sw].astype(int)
    mask = np.abs(region - bg.astype(int)).max(axis=2) > BG_TOLERANCE
    if not mask.any():
        return LogoResult(False, reason="không có logo")

    gap = max(8, int(w * GAP_FRAC))
    for x0, x1 in _runs(mask.any(axis=0), gap):
        if x1 >= sw - 2:
            continue  # cluster touches the right edge of the window: product content
        sub = mask[:, x0 : x1 + 1]
        y_runs = [r for r in _runs(sub.any(axis=1), gap) if r[1] < sh - 2]
        if not y_runs:
            continue
        # The logo is the biggest vertical cluster inside this column band.
        y0, y1 = max(y_runs, key=lambda r: sub[r[0] : r[1] + 1].sum())
        # Re-tighten x to that vertical cluster only.
        cols = np.flatnonzero(mask[y0 : y1 + 1, x0 : x1 + 1].any(axis=0))
        bx0, bx1 = x0 + int(cols[0]), x0 + int(cols[-1])
        bw, bh = bx1 - bx0 + 1, y1 - y0 + 1
        if bw < w * MIN_LOGO_FRAC or bh < h * MIN_LOGO_FRAC:
            continue
        if bw > w * MAX_LOGO_W_FRAC or bh > h * MAX_LOGO_H_FRAC:
            continue
        box = (
            max(0, bx0 - PADDING),
            max(0, y0 - PADDING),
            min(sw - 1, bx1 + PADDING),
            min(sh - 1, y1 + PADDING),
        )
        return LogoResult(True, box=box)
    return LogoResult(False, reason="không tìm thấy vùng giống logo")


def remove_logo(src_path: str, dest_path: str) -> LogoResult:
    """Write a logo-free copy of src_path to dest_path (always writes, so the
    destination exists even when nothing was removed)."""
    with Image.open(src_path) as im:
        im.load()
        fmt = im.format
        result = detect_logo(im)
        out = im
        if result.removed:
            if im.mode not in ("RGB", "RGBA"):
                out = im.convert("RGBA" if "transparency" in im.info or im.mode in ("LA", "P") else "RGB")
            bg = np.asarray(out.convert("RGB"))[2:12, -12:].reshape(-1, 3).mean(axis=0)
            fill = tuple(int(c) for c in bg)
            if out.mode == "RGBA":
                fill = fill + (255,)
            ImageDraw.Draw(out).rectangle(result.box, fill=fill)
        save_kwargs = {}
        if (fmt or "").upper() == "JPEG":
            save_kwargs = {"quality": 95, "subsampling": 0}
            if out.mode != "RGB":
                out = out.convert("RGB")
        out.save(dest_path, format=fmt or None, **save_kwargs)
    return result
