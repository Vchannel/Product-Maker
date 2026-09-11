"""Remove the flycampro.vn logo from downloaded product photos.

Product photos on flycampro.vn carry a "FLYCAM PRO.VN" logo near the top-left
corner. Two detectors, tried in order:

1. Template match (lib/assets/flycampro_logo.png). The logo is always the same
   artwork, so its exact position is found by comparing only the logo's own
   pixels - accessories touching or overlapping the logo (a selfie stick, a
   lens cover) don't disturb the match. Only pixels that really belong to the
   logo are removed, and they are filled from the surrounding pixels, so a
   product touching the logo keeps its shape instead of getting a white notch.

2. Cluster fallback for logo variants the template doesn't match: paints a
   rectangle over an isolated, logo-sized cluster on a flat background, and
   leaves the photo untouched when product content runs into that corner.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

ALGORITHM_VERSION = 3

TEMPLATE_PATH = Path(__file__).parent / "assets" / "flycampro_logo.png"
TEMPLATE_BASE_WIDTH = 1500  # width of the photos the template was cut from
# Mean abs RGB difference over the logo's solid pixels: ~0-30 on real logos
# (native size or rescaled/JPEG), >100 on photos without the logo.
MATCH_MAX_ERROR = 45.0
SEARCH_FRAC = 0.32

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
    method: str = ""
    mask: Optional[np.ndarray] = None  # template method: pixels to fill, relative to box


# --------------------------------------------------------------------------
# 1. template match
# --------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _template(width: int):
    """(rgb float array, halo mask, solid mask) scaled for an image `width` px wide."""
    if not TEMPLATE_PATH.exists():
        return None
    tpl = Image.open(TEMPLATE_PATH).convert("RGB")
    scale = width / TEMPLATE_BASE_WIDTH
    if abs(scale - 1) > 0.01:
        size = (max(8, round(tpl.width * scale)), max(8, round(tpl.height * scale)))
        tpl = tpl.resize(size, Image.LANCZOS)
    t = np.asarray(tpl).astype(np.float32)
    ink = (255 - t).max(axis=2)
    return t, ink > 12, ink > 90


def _masked_error(region: np.ndarray, t: np.ndarray, solid: np.ndarray, step: int, positions) -> tuple:
    th, tw = solid.shape
    ys, xs = np.nonzero(solid[::step, ::step])
    ys, xs = ys * step, xs * step
    tv = t[ys, xs]
    best = (np.inf, 0, 0)
    for y, x in positions:
        err = np.abs(region[y + ys, x + xs] - tv).mean()
        if err < best[0]:
            best = (err, y, x)
    return best


def _match_template(arr: np.ndarray) -> Optional[tuple]:
    h, w, _ = arr.shape
    tpl = _template(w)
    if tpl is None:
        return None
    t, halo, solid = tpl
    th, tw = solid.shape
    sh, sw = min(h, int(h * SEARCH_FRAC) + th), min(w, int(w * SEARCH_FRAC) + tw)
    if sh < th or sw < tw:
        return None
    region = arr[:sh, :sw].astype(np.float32)
    # Coarse scan on a subsampled grid, then refine around the best hit. The
    # grid follows the logo's scale so thin strokes aren't skipped on small photos.
    scale = w / TEMPLATE_BASE_WIDTH
    step = max(1, round(3 * scale))
    sample = max(1, round(2 * scale))
    coarse = [(y, x) for y in range(0, sh - th + 1, step) for x in range(0, sw - tw + 1, step)]
    _, cy, cx = _masked_error(region, t, solid, sample, coarse)
    fine = [
        (y, x)
        for y in range(max(0, cy - step), min(sh - th, cy + step) + 1)
        for x in range(max(0, cx - step), min(sw - tw, cx + step) + 1)
    ]
    err, y, x = _masked_error(region, t, solid, 1, fine)
    if err > MATCH_MAX_ERROR:
        return None

    # Pixels to rebuild: the logo's full anti-aliased footprint grown by a
    # couple of pixels. The logo is placed with sub-pixel offsets, so an exact
    # template footprint leaves a faint grey outline; the small margin is
    # rebuilt from neighbouring pixels, which keeps touching products intact.
    fill = _dilate(halo, 2)
    return (x, y, x + tw - 1, y + th - 1), fill, err


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy == 0 and dx == 0:
                continue
            out[max(0, dy) : h + min(0, dy), max(0, dx) : w + min(0, dx)] |= mask[
                max(0, -dy) : h + min(0, -dy), max(0, -dx) : w + min(0, -dx)
            ]
    return out


def _inpaint(img: np.ndarray, hole: np.ndarray, max_iter: int = 200) -> np.ndarray:
    """Fill `hole` pixels by repeatedly averaging already-known 8-neighbours,
    so white background stays white and an accessory under the logo is
    continued with its own colour."""
    out = img.astype(np.float32).copy()
    known = ~hole
    h, w = hole.shape
    shifts = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for _ in range(max_iter):
        if known.all():
            break
        total = np.zeros_like(out)
        count = np.zeros((h, w), dtype=np.float32)
        for dy, dx in shifts:
            src_y = slice(max(0, -dy), h - max(0, dy))
            dst_y = slice(max(0, dy), h - max(0, -dy))
            src_x = slice(max(0, -dx), w - max(0, dx))
            dst_x = slice(max(0, dx), w - max(0, -dx))
            k = known[src_y, src_x]
            total[dst_y, dst_x] += out[src_y, src_x] * k[..., None]
            count[dst_y, dst_x] += k
        frontier = (~known) & (count > 0)
        if not frontier.any():
            break
        out[frontier] = total[frontier] / count[frontier][:, None]
        known = known | frontier
    return out


# --------------------------------------------------------------------------
# 2. cluster fallback
# --------------------------------------------------------------------------
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


def _detect_cluster(arr: np.ndarray) -> LogoResult:
    h, w, _ = arr.shape
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
        y0, y1 = max(y_runs, key=lambda r: sub[r[0] : r[1] + 1].sum())
        cols = np.flatnonzero(mask[y0 : y1 + 1, x0 : x1 + 1].any(axis=0))
        bx0, bx1 = x0 + int(cols[0]), x0 + int(cols[-1])
        bw, bh = bx1 - bx0 + 1, y1 - y0 + 1
        if bw < w * MIN_LOGO_FRAC or bh < h * MIN_LOGO_FRAC:
            continue
        if bw > w * MAX_LOGO_W_FRAC or bh > h * MAX_LOGO_H_FRAC:
            continue
        box = (max(0, bx0 - PADDING), max(0, y0 - PADDING), min(sw - 1, bx1 + PADDING), min(sh - 1, y1 + PADDING))
        return LogoResult(True, box=box, method="cluster")
    return LogoResult(False, reason="không tìm thấy vùng giống logo")


# --------------------------------------------------------------------------
def detect_logo(img: Image.Image) -> LogoResult:
    arr = np.asarray(img.convert("RGB"))
    h, w, _ = arr.shape
    if w < 200 or h < 200:
        return LogoResult(False, reason="ảnh quá nhỏ")
    match = _match_template(arr)
    if match:
        box, fill, _err = match
        return LogoResult(True, box=box, method="template", mask=fill)
    return _detect_cluster(arr)


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
            if result.method == "template":
                x0, y0, x1, y1 = result.box
                pad = 6
                h, w = out.height, out.width
                bx0, by0, bx1, by1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + 1 + pad), min(h, y1 + 1 + pad)
                data = np.asarray(out).copy()
                hole = np.zeros((by1 - by0, bx1 - bx0), dtype=bool)
                hole[y0 - by0 : y0 - by0 + result.mask.shape[0], x0 - bx0 : x0 - bx0 + result.mask.shape[1]] = result.mask
                patch = _inpaint(data[by0:by1, bx0:bx1], hole)
                data[by0:by1, bx0:bx1] = np.clip(np.rint(patch), 0, 255).astype(np.uint8)
                out = Image.fromarray(data, mode=out.mode)
            else:
                bands = len(out.getbands())
                bg = np.asarray(out)[2:12, -12:].reshape(-1, bands).mean(axis=0)
                ImageDraw.Draw(out).rectangle(result.box, fill=tuple(int(round(c)) for c in bg))
        save_kwargs = {}
        if (fmt or "").upper() == "JPEG":
            save_kwargs = {"quality": 95, "subsampling": 0}
            if out.mode != "RGB":
                out = out.convert("RGB")
        out.save(dest_path, format=fmt or None, **save_kwargs)
    result.mask = None
    return result
