"""Remove the flycampro.vn logo from downloaded product photos.

Product photos on flycampro.vn carry a "FLYCAM PRO.VN" logo near the top-left
corner. Two detectors, tried in order:

1. Template match (lib/assets/flycampro_logo.png). The logo is always the same
   artwork, but it is stamped at different sizes (≈187 px wide on recent
   1500×1000 photos, noticeably larger on older 1200×800 ones and on some
   accessory photos), so every photo is searched over a range of logo sizes.
   Only the logo's own pixels are compared, so accessories touching or
   overlapping the logo don't disturb the match, and only the logo footprint
   is rebuilt from neighbouring pixels - a product touching the logo keeps its
   shape instead of getting a white notch.

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

ALGORITHM_VERSION = 4

TEMPLATE_PATH = Path(__file__).parent / "assets" / "flycampro_logo.png"
# Logo sizes searched, as a factor of the template's own pixel size (the
# template was cut from a 1500×1000 photo where the logo is ~187 px wide).
SCALES = tuple(float(s) for s in np.geomspace(0.5, 1.7, 17))
# Mean abs RGB difference over the logo's solid pixels: real logos score
# ~0-30 (native, rescaled, JPEG); photos without the logo score far higher.
MATCH_MAX_ERROR = 45.0
SEARCH_FRAC = 0.42  # the logo's top-left corner lies within this share of the photo

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
    error: Optional[float] = None
    scale: Optional[float] = None


# --------------------------------------------------------------------------
# 1. template match
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _base_template() -> Optional[Image.Image]:
    if not TEMPLATE_PATH.exists():
        return None
    return Image.open(TEMPLATE_PATH).convert("RGB")


@lru_cache(maxsize=128)
def _template(scale_key: int):
    """(rgb float array, halo mask, solid mask) at scale_key / 1000."""
    base = _base_template()
    if base is None:
        return None
    scale = scale_key / 1000
    tpl = base
    if abs(scale - 1) > 0.005:
        size = (max(8, round(base.width * scale)), max(8, round(base.height * scale)))
        tpl = base.resize(size, Image.LANCZOS)
    t = np.asarray(tpl).astype(np.float32)
    ink = (255 - t).max(axis=2)
    return t, ink > 12, ink > 90


def _masked_error(region: np.ndarray, t: np.ndarray, solid: np.ndarray, step: int, rows, cols) -> tuple:
    """Best (error, y, x) over rows × cols: mean abs difference on the
    template's solid pixels, one numpy call per row of x offsets."""
    ys, xs = np.nonzero(solid[::step, ::step])
    ys, xs = ys * step, xs * step
    tv = t[ys, xs][:, None, :]  # (pixels, 1, 3)
    cols = np.asarray(list(cols))
    col_idx = xs[:, None] + cols[None, :]
    best = (np.inf, 0, 0)
    for y in rows:
        err = np.abs(region[y + ys[:, None], col_idx] - tv).mean(axis=(0, 2))
        i = int(err.argmin())
        if err[i] < best[0]:
            best = (float(err[i]), y, int(cols[i]))
    return best


class _FFTRegion:
    """FFTs of one search region, reused for every template size:
    sum M(I-T)^2 = corr(I^2, M) - 2 corr(I, M*T) + sum M*T^2."""

    def __init__(self, region: np.ndarray):
        self.shape = region.shape[:2]
        img = region.astype(np.float64)
        self.f_img = [np.fft.rfft2(img[..., c]) for c in range(3)]
        self.f_sq = [np.fft.rfft2(img[..., c] ** 2) for c in range(3)]

    def ssd_map(self, t: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
        H, W = self.shape
        h, w = mask.shape
        if h > H or w > W:
            return None
        m = mask.astype(np.float64)
        f_m = np.fft.rfft2(m[::-1, ::-1], s=self.shape)
        total = None
        const = 0.0
        for c in range(3):
            tm = t[..., c].astype(np.float64) * m
            f_tm = np.fft.rfft2(tm[::-1, ::-1], s=self.shape)
            part = self.f_sq[c] * f_m - 2 * self.f_img[c] * f_tm
            total = part if total is None else total + part
            const += float((tm * t[..., c]).sum())
        spatial = np.fft.irfft2(total, s=self.shape)[h - 1 : H, w - 1 : W]
        return (spatial + const) / max(1.0, m.sum() * 3)


def _match_template(arr: np.ndarray) -> Optional[tuple]:
    """Return (box, fill_mask, error, scale) for the best logo placement, or None."""
    if _base_template() is None:
        return None
    h, w, _ = arr.shape
    largest = _template(round(SCALES[-1] * 1000))[2].shape
    sh = min(h, int(h * SEARCH_FRAC) + largest[0])
    sw = min(w, int(w * SEARCH_FRAC) + largest[1])
    region = arr[:sh, :sw].astype(np.float32)

    # Coarse: every size, every offset, on a half-resolution copy (FFT).
    half = region[: sh // 2 * 2, : sw // 2 * 2]
    half = (half[0::2, 0::2] + half[1::2, 0::2] + half[0::2, 1::2] + half[1::2, 1::2]) / 4
    fft_half = _FFTRegion(half)
    coarse = []
    for scale in SCALES:
        tpl = _template(round(scale * 500))  # half-size template for the half-size region
        if tpl is None:
            continue
        t_half, _halo, solid_half = tpl
        if solid_half.sum() < 20:
            continue
        ssd = fft_half.ssd_map(t_half, solid_half)
        if ssd is None:
            continue
        cy, cx = (int(v) for v in np.unravel_index(int(ssd.argmin()), ssd.shape))
        coarse.append((float(ssd[cy, cx]), scale, cy * 2, cx * 2))
    if not coarse:
        return None
    coarse.sort()

    # Fine: full resolution, mean-abs error, around the best few coarse hits.
    best = None
    for _ssd, scale, cy, cx in coarse[:3]:
        t, halo, solid = _template(round(scale * 1000))
        th, tw = solid.shape
        if th > sh or tw > sw:
            continue
        reach = 4
        err, y, x = _masked_error(
            region,
            t,
            solid,
            1,
            range(max(0, cy - reach), min(sh - th, cy + reach) + 1),
            range(max(0, cx - reach), min(sw - tw, cx + reach) + 1),
        )
        if best is None or err < best[0]:
            best = (err, y, x, scale, halo, th, tw)
    if best is None or best[0] > MATCH_MAX_ERROR:
        return None
    err, y, x, scale, halo, th, tw = best
    # Rebuild the logo's anti-aliased footprint grown by a couple of pixels:
    # the logo sits at sub-pixel offsets, so the exact footprint would leave a
    # faint grey outline.
    fill = _dilate(halo, 2 if scale < 1.2 else 3)
    return (x, y, x + tw - 1, y + th - 1), fill, err, scale


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


def _inpaint(img: np.ndarray, hole: np.ndarray, max_iter: int = 300) -> np.ndarray:
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
        box, fill, err, scale = match
        return LogoResult(True, box=box, method="template", mask=fill, error=err, scale=scale)
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
                mh, mw = result.mask.shape
                hole[y0 - by0 : y0 - by0 + mh, x0 - bx0 : x0 - bx0 + mw] = result.mask[: by1 - (y0), : bx1 - (x0)][
                    : hole.shape[0] - (y0 - by0), : hole.shape[1] - (x0 - bx0)
                ]
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
