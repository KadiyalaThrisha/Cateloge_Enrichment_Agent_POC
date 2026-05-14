


#!/usr/bin/env python3
"""
color_map_shoes_clean.py

Shoes color mapping (text-first, image-fallback) for Amazon-style product JSON/JSONL.

Design goals
- Single field for UI: `Color` + `colorHex` (no fineColor).
- Traceability preserved in `detected_color`.
- Text-first extraction from structured fields, features, and title.
- Image fallback using KMeans palette + robust primary-cluster selection + perceptual swatch matching (Lab).

Notes specific to shoes
- Shoes are often photographed without a person; no skin filtering by default.
- Background is often white; we filter near-white (and optionally near-black) pixels.
- Primary color is selected by a score (not always the biggest cluster) to avoid outsole/shadow dominance.

Output fields per product
- detected_color: { primary: {name, hex, source, confidence}, secondary: [...], is_patterned: bool }
- Color: canonical color name (string or None)
- colorHex: hex string (or None)

Usage
python color_map_shoes_clean.py --input "D:\\...\\Shoes_Only.jsonl" --output "D:\\...\\Shoes_Only_with_color.jsonl" --cache-dir "D:\\...\\_cache\\shoe_color"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Optional deps for image fallback
try:
    import requests
except Exception:
    requests = None

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import numpy as np
except Exception:
    np = None

try:
    from sklearn.cluster import KMeans
except Exception:
    KMeans = None

# Progress bar
try:
    from tqdm import tqdm
except Exception:
    tqdm = None


# ---------------------------
# Canonical colors + hex
# ---------------------------

# Keep synonyms mapped only to labels that exist in CANON_COLORS.
COLOR_SYNONYMS = {
    "grey": "gray",
    "off white": "offwhite",
    "off-white": "offwhite",
    "navy blue": "navy",
    "dark blue": "navy",
    "midnight blue": "navy",
    "charcoal": "gray",
    "burgundy": "maroon",
    "wine": "maroon",
    "cream": "offwhite",
    "ivory": "offwhite",
    "khaki": "beige",
    "sand": "beige",
    "taupe": "beige",
    "camel": "tan",
    "tan": "tan",
}

CANON_COLORS = [
    "black", "white", "offwhite", "gray",
    "red", "maroon", "pink", "orange", "yellow",
    "green", "olive", "teal",
    "blue", "navy", "purple",
    "brown", "tan", "beige",
    "gold", "silver",
    "multicolor",
]

CANON_HEX = {
    "black": "#111111", "white": "#ffffff", "offwhite": "#f5f5f0", "gray": "#808080",
    "red": "#d32f2f", "maroon": "#800000", "pink": "#e91e63", "orange": "#fb8c00",
    "yellow": "#fdd835", "green": "#43a047", "olive": "#808000", "teal": "#008080",
    "blue": "#1e88e5", "navy": "#0b1f3a", "purple": "#8e24aa",
    "brown": "#6d4c41", "tan": "#c19a6b", "beige": "#d7c4a3",
    "gold": "#d4af37", "silver": "#c0c0c0",
    "multicolor": "#9e9e9e",
}

COLOR_TOKENS = sorted(
    set(CANON_COLORS) | set(COLOR_SYNONYMS.keys()) | set(COLOR_SYNONYMS.values()),
    key=len,
    reverse=True
)

NON_SPECIFIC_COLOR_PHRASES = [
    "various colors", "various colour", "available in",
    "solid color", "solid colour", "like colors", "like colours",
    "color may vary", "colour may vary",
    "slight variation in the actual color", "slight variation in the actual colour",
]

PATTERN_HINTS = [
    "printed", "pattern", "patterned", "camouflage", "camo",
    "snake", "snakeskin", "leopard", "zebra", "cheetah",
    "stripe", "striped", "check", "checked",
    "glitter", "sparkle", "metallic",
]


# ---------------------------
# Utilities
# ---------------------------

def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x

def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = [max(0, min(255, int(x))) for x in rgb]
    return f"#{r:02x}{g:02x}{b:02x}"

def is_non_specific_color_context(text: str) -> bool:
    t = _norm_text(text)
    return any(p in t for p in NON_SPECIFIC_COLOR_PHRASES)

def title_has_pattern_hint(title: str) -> bool:
    t = _norm_text(title)
    return any(h in t for h in PATTERN_HINTS)

def safe_get(d: Dict[str, Any], path: List[str]) -> Optional[Any]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

def normalize_color_token(tok: str) -> Optional[str]:
    t = _norm_text(tok)
    t = t.replace("/", " ").replace("&", " ").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if t in COLOR_SYNONYMS:
        t = COLOR_SYNONYMS[t]
    return t if t in CANON_COLORS else None

def find_colors_in_text(text: str) -> List[str]:
    t = _norm_text(text)
    if not t or is_non_specific_color_context(t):
        return []
    found: List[str] = []
    for tok in COLOR_TOKENS:
        pattern = r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])"
        if re.search(pattern, t):
            norm = normalize_color_token(tok)
            if norm and norm != "multicolor" and norm not in found:
                found.append(norm)
    return found

def pick_main_image_url(product: Dict[str, Any]) -> Optional[str]:
    imgs = product.get("images") or []
    if not isinstance(imgs, list) or not imgs:
        return None
    main = next((im for im in imgs if isinstance(im, dict) and im.get("variant") == "MAIN"), None)
    if not isinstance(main, dict):
        main = imgs[0] if isinstance(imgs[0], dict) else None
    if not isinstance(main, dict):
        return None
    return main.get("hi_res") or main.get("large") or main.get("thumb")


# ---------------------------
# Result
# ---------------------------

@dataclass
class ColorResult:
    primary_name: Optional[str] = None
    primary_hex: Optional[str] = None
    secondary_names: List[str] = None
    source: str = "unknown"
    confidence: float = 0.0
    is_patterned: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": (
                {
                    "name": self.primary_name,
                    "hex": self.primary_hex,
                    "source": self.source,
                    "confidence": round(float(self.confidence), 3),
                } if self.primary_name else None
            ),
            "secondary": self.secondary_names or [],
            "is_patterned": bool(self.is_patterned),
        }


# ---------------------------
# Text extraction
# ---------------------------

def extract_color_from_structured(product: Dict[str, Any]) -> Optional[str]:
    candidates = [
        product.get("color"),
        product.get("colour"),
        safe_get(product, ["details", "Color"]),
        safe_get(product, ["details", "Colour"]),
        safe_get(product, ["attributes", "color"]),
        safe_get(product, ["attributes", "colour"]),
        safe_get(product, ["specs", "Color"]),
        safe_get(product, ["specs", "Colour"]),
        safe_get(product, ["productDetails", "Color"]),
        safe_get(product, ["productDetails", "Colour"]),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip() and not is_non_specific_color_context(c):
            found = find_colors_in_text(c)
            if found:
                return found[0]
    return None

def extract_color_from_features(product: Dict[str, Any]) -> Optional[str]:
    feats = product.get("features") or []
    if not isinstance(feats, list):
        return None
    rx = re.compile(r"\b(colou?r)\s*[:\-]\s*([A-Za-z][A-Za-z \-/&]+)\b", re.IGNORECASE)
    for line in feats:
        if not isinstance(line, str):
            continue
        if is_non_specific_color_context(line):
            continue
        m = rx.search(line)
        if m:
            val = _norm_text(m.group(2))
            colors = find_colors_in_text(val)
            if colors:
                return colors[0]
    return None

def extract_colors_from_title(title: str) -> Tuple[Optional[str], List[str], float]:
    t = (title or "").strip()
    if not t:
        return None, [], 0.0
    low = _norm_text(t)
    if is_non_specific_color_context(low):
        return None, [], 0.0

    m = re.search(r"\(([^)]*)\)", t)
    if m:
        inside = m.group(1)
        first = inside.split(",")[0].strip()
        colors = find_colors_in_text(first)
        if colors:
            return colors[0], [], 0.92

    colors_all = find_colors_in_text(t)
    if colors_all:
        primary = colors_all[0]
        secondary = colors_all[1:]
        conf = 0.85 if len(colors_all) == 1 else 0.78
        return primary, secondary, conf

    return None, [], 0.0

def extract_text_color(product: Dict[str, Any]) -> ColorResult:
    title = product.get("title") or ""
    feats = product.get("features") or []
    is_pat = title_has_pattern_hint(title) or any(title_has_pattern_hint(f) for f in feats if isinstance(f, str))

    c_struct = extract_color_from_structured(product)
    if c_struct:
        return ColorResult(c_struct, CANON_HEX.get(c_struct), [], "structured", 0.96, is_pat)

    c_feat = extract_color_from_features(product)
    if c_feat:
        return ColorResult(c_feat, CANON_HEX.get(c_feat), [], "features", 0.94, is_pat)

    primary, secondary, conf = extract_colors_from_title(title)
    if primary:
        is_pat2 = is_pat or (len(secondary) > 0)
        return ColorResult(primary, CANON_HEX.get(primary), secondary, "title", conf, is_pat2)

    return ColorResult(None, None, [], "none", 0.0, is_pat)


# ---------------------------
# Image inference: Swatch matching (Lab)
# ---------------------------

def _require_image_deps() -> None:
    missing = []
    if requests is None: missing.append("requests")
    if Image is None: missing.append("Pillow")
    if np is None: missing.append("numpy")
    if KMeans is None: missing.append("scikit-learn")
    if missing:
        raise RuntimeError("Missing dependencies for image inference: " + ", ".join(missing))

# Stable, UI-safe palette (single label only).
SWATCHES: List[Tuple[str, Tuple[int, int, int]]] = [
    ("black",  (17, 17, 17)),
    ("white",  (255, 255, 255)),
    ("offwhite",(245, 243, 232)),
    ("gray",   (128, 128, 128)),

    ("red",    (210, 47, 47)),
    ("maroon", (128, 0, 0)),
    ("pink",   (233, 30, 99)),

    ("orange", (251, 140, 0)),
    ("yellow", (253, 216, 53)),

    ("green",  (67, 160, 71)),
    ("olive",  (128, 128, 0)),
    ("teal",   (0, 128, 128)),

    ("blue",   (30, 136, 229)),
    ("navy",   (11, 31, 58)),
    ("purple", (142, 36, 170)),

    ("brown",  (109, 76, 65)),
    ("tan",    (193, 154, 107)),
    ("beige",  (215, 196, 163)),

    ("gold",   (212, 175, 55)),
    ("silver", (192, 192, 192)),
]

def _srgb_to_linear(u: float) -> float:
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4

def _rgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    r, g, b = [x / 255.0 for x in rgb]
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)

    # linear RGB -> XYZ (D65)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505

    # XYZ -> Lab
    xr, yr, zr = 0.95047, 1.00000, 1.08883
    x, y, z = x / xr, y / yr, z / zr

    def f(t: float) -> float:
        return t ** (1/3) if t > 0.008856 else (7.787 * t + 16/116)

    fx, fy, fz = f(x), f(y), f(z)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    bb = 200 * (fy - fz)
    return (L, a, bb)

def _lab_dist(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

_SWATCH_LABS = [(name, _rgb_to_lab(rgb)) for name, rgb in SWATCHES]

def _rgb_hsv_luma(rgb: Tuple[int, int, int]) -> Tuple[float, float, float, float]:
    r, g, b = [x / 255.0 for x in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    diff = mx - mn
    s = 0.0 if mx < 1e-6 else diff / mx

    if diff < 1e-6:
        h_deg = 0.0
    elif mx == r:
        h_deg = (60 * ((g - b) / diff) + 360) % 360
    elif mx == g:
        h_deg = (60 * ((b - r) / diff) + 120) % 360
    else:
        h_deg = (60 * ((r - g) / diff) + 240) % 360

    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return h_deg, s, mx, luma

def match_color_name(rgb: Tuple[int, int, int]) -> str:
    """
    One-label output for UI.
    Uses a small swatch palette in Lab space + a safety guard for magenta/pink.
    """
    h_deg, s, v, luma = _rgb_hsv_luma(rgb)

    # Safety guard: magenta/pink zone
    if 290 <= h_deg <= 345 and s > 0.22 and luma > 0.10:
        return "pink"

    lab = _rgb_to_lab(rgb)
    best_name = "multicolor"
    best_d = 1e18
    for name, slab in _SWATCH_LABS:
        d = _lab_dist(lab, slab)
        if d < best_d:
            best_d = d
            best_name = name
    return best_name


# ---------------------------
# Image inference pipeline
# ---------------------------

def download_image(url: str, cache_dir: str, timeout: int = 20) -> Optional[str]:
    if requests is None:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    import hashlib
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    fpath = os.path.join(cache_dir, f"{h}.jpg")
    if os.path.exists(fpath) and os.path.getsize(fpath) > 10_000:
        return fpath
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.amazon.com/",
            },
        )
        if r.status_code != 200:
            return None
        ct = (r.headers.get("Content-Type") or "").lower()
        if "image" not in ct:
            return None
        with open(fpath, "wb") as f:
            f.write(r.content)
        return fpath if os.path.getsize(fpath) > 10_000 else None
    except Exception:
        return None

def center_crop(img: "Image.Image", frac: float = 0.78, y_bias: float = 0.10) -> "Image.Image":
    """Shoes: slightly larger crop and biased downward to focus on the shoe body."""
    w, h = img.size
    cw, ch = int(w * frac), int(h * frac)
    cx = w // 2
    cy = int(h * (0.5 + y_bias))
    x0 = max(0, cx - cw // 2)
    y0 = max(0, cy - ch // 2)
    x1 = min(w, x0 + cw)
    y1 = min(h, y0 + ch)
    return img.crop((x0, y0, x1, y1))

def rgb_to_hsv_np(rgb01: "np.ndarray") -> "np.ndarray":
    r, g, b = rgb01[:, 0], rgb01[:, 1], rgb01[:, 2]
    mx = np.max(rgb01, axis=1)
    mn = np.min(rgb01, axis=1)
    diff = mx - mn

    h = np.zeros_like(mx)
    mask = diff > 1e-6
    idx = mask & (mx == r)
    h[idx] = ((g[idx] - b[idx]) / diff[idx]) % 6
    idx = mask & (mx == g)
    h[idx] = ((b[idx] - r[idx]) / diff[idx]) + 2
    idx = mask & (mx == b)
    h[idx] = ((r[idx] - g[idx]) / diff[idx]) + 4
    h = (h / 6.0) % 1.0

    s = np.zeros_like(mx)
    s[mx > 1e-6] = diff[mx > 1e-6] / mx[mx > 1e-6]
    v = mx
    return np.stack([h, s, v], axis=1)

def is_near_white(rgb01: "np.ndarray") -> "np.ndarray":
    hsv = rgb_to_hsv_np(rgb01)
    s = hsv[:, 1]
    v = hsv[:, 2]
    return (v > 0.93) & (s < 0.16)

def is_near_black(rgb01: "np.ndarray") -> "np.ndarray":
    luma = 0.2126*rgb01[:, 0] + 0.7152*rgb01[:, 1] + 0.0722*rgb01[:, 2]
    return luma < 0.05

def kmeans_palette(rgb_pixels: "np.ndarray", k: int = 5, seed: int = 13) -> Tuple["np.ndarray", "np.ndarray"]:
    X = rgb_pixels.astype(np.float32) / 255.0
    hsv = rgb_to_hsv_np(X)
    luma = (0.2126 * X[:, 0] + 0.7152 * X[:, 1] + 0.0722 * X[:, 2]).reshape(-1, 1)
    feats = np.concatenate([hsv, luma], axis=1)

    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = km.fit_predict(feats)

    cent = np.zeros((k, 3), dtype=np.float32)
    wts = np.zeros((k,), dtype=np.float32)
    for i in range(k):
        idx = labels == i
        if idx.sum() == 0:
            continue
        wts[i] = idx.mean()
        cent[i] = X[idx].mean(axis=0)

    order = np.argsort(-wts)
    cent = cent[order]
    wts = wts[order]
    cent_uint8 = np.clip(cent * 255.0, 0, 255).astype(np.uint8)
    return cent_uint8, wts

def infer_color_from_image_url(url: str, cache_dir: str) -> ColorResult:
    _require_image_deps()
    if not url:
        return ColorResult()

    fpath = download_image(url, cache_dir=cache_dir)
    if not fpath:
        return ColorResult()

    try:
        img = Image.open(fpath).convert("RGB")
    except Exception:
        return ColorResult()

    img = center_crop(img, frac=0.9, y_bias=0.0).resize((256, 256))
   
    # ---------------------------
    # BASE PIXELS (CLEAN)
    # ---------------------------
    arr = np.array(img, dtype=np.uint8).reshape(-1, 3)
    rgb01 = arr.astype(np.float32) / 255.0

    # remove white + black background
    mask = is_near_white(rgb01) | is_near_black(rgb01)
    kept = arr[~mask]

    # fallback
    if kept.shape[0] < 3000:
        kept = arr[~is_near_white(rgb01)]

    # ---------------------------
    # REMOVE GLOW / REFLECTION NOISE
    # ---------------------------
    filtered_pixels = []

    for pixel in kept:
        r, g, b = pixel

        # FIXED overflow
        if (int(r) + int(g) + int(b)) / 3 > 220:
            continue

        # remove extreme glow
        if max(r, g, b) - min(r, g, b) > 200:
            continue

        filtered_pixels.append(pixel)

    if len(filtered_pixels) > 2000:
        kept = np.array(filtered_pixels)

    rgb01 = arr.astype(np.float32) / 255.0

    mask = is_near_white(rgb01) | is_near_black(rgb01)
    kept = arr[~mask]

    # fallback: if we removed too much, keep only non-white
    if kept.shape[0] < 3000:
        kept = arr[~is_near_white(rgb01)]


    # ---------------------------
    # REMOVE SCREEN / GLOW NOISE (NEW)
    # ---------------------------
    filtered_pixels = []

    for pixel in kept:
        r, g, b = pixel

        # remove very bright pixels (screen / reflections)
        if (int(r) + int(g) + int(b)) / 3 > 220:
            continue

        # remove extreme color spikes (screen glow)
        if max(r, g, b) - min(r, g, b) > 200:
            continue

        filtered_pixels.append(pixel)

    # apply filter only if safe
    if len(filtered_pixels) > 2000:
        kept = np.array(filtered_pixels)

    if kept.shape[0] < 2500:
        return ColorResult()

    centroids, weights = kmeans_palette(kept, k=5)
    # ---------------------------
    # FILTER WEAK CLUSTERS (Step 3)
    # ---------------------------
    filtered_indices = [
        i for i in range(len(weights))
        if float(weights[i]) > 0.08
    ]

    # fallback if everything removed
    if not filtered_indices:
        filtered_indices = list(range(len(weights)))

    # Choose primary cluster by score (avoid outsole/shadow/background dominance)
    
    def score_centroid(c, w):
        rgb = tuple(int(x) for x in c.tolist())
        h_deg, s, v, luma = _rgb_hsv_luma(rgb)

        # Penalize extreme brightness/darkness (background/shadow)
        if luma > 0.93:
            return w * 0.2
        if luma < 0.08:
            return w * 0.3

        # Boost real colors (higher saturation)
        saturation_boost = 0.5 + (1.5 * s)

        # Slight penalty for neutral colors (gray/silver-like)
        neutral_penalty = 0.7 if s < 0.15 else 1.0

        # Final score
        return w * saturation_boost * neutral_penalty
    
    # def score_centroid(c, w):
    #     rgb = tuple(int(x) for x in c.tolist())
    #     h_deg, s, v, luma = _rgb_hsv_luma(rgb)
    #     dark_penalty = 0.25 if luma < 0.18 else 1.0
    #     bright_penalty = 0.35 if luma > 0.92 else 1.0
    #     return (w * (0.35 + 1.1*s) * (0.35 + (1 - abs(luma - 0.55))) * dark_penalty * bright_penalty)

    # best_i = max(range(len(weights)), key=lambda i: score_centroid(centroids[i], float(weights[i])))
    best_i = max(
    filtered_indices,
    key=lambda i: score_centroid(centroids[i], float(weights[i]))
)

    primary_rgb = tuple(int(x) for x in centroids[best_i].tolist())
    primary_hex = rgb_to_hex(primary_rgb)
    primary_name = match_color_name(primary_rgb)
    # ---------------------------
    # STEP 4: Neutral Color Correction
    # ---------------------------
    h_deg, s, v, luma = _rgb_hsv_luma(primary_rgb)

    # If detected as gray/silver but actually has color signal
    if primary_name in ["silver", "gray"]:
        if s > 0.2:  # has color signal
            if 10 < h_deg < 50:
                primary_name = "brown"
            elif 50 <= h_deg < 70:
                primary_name = "beige"

    # Pattern detection: multiple strong, distinct clusters
    is_pat = False
    secondary: List[str] = []

    def dist(a, b) -> float:
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        return float(np.linalg.norm(a - b))

    strong = [(centroids[i], float(weights[i])) for i in range(len(weights)) if float(weights[i]) >= 0.12]
    if len(strong) >= 2:
        if dist(strong[0][0], strong[1][0]) >= 70:
            is_pat = True
            for c, w in strong[1:3]:
                nm = match_color_name(tuple(int(x) for x in c.tolist()))
                if nm != primary_name and nm != "multicolor" and nm not in secondary:
                    secondary.append(nm)

    kept_ratio = kept.shape[0] / arr.shape[0]
    dominance = float(weights[best_i]) if len(weights) else 0.0
    conf = clamp01(0.35 + 0.55 * dominance + 0.25 * kept_ratio)
    if primary_name == "multicolor":
        conf *= 0.6

    if primary_name and not primary_hex:
        primary_hex = CANON_HEX.get(primary_name)

    return ColorResult(primary_name, primary_hex, secondary, "image", conf, is_pat)


# ---------------------------
# Promote fields
# ---------------------------

def promote_color_fields(p: Dict[str, Any]) -> None:
    dc = p.get("detected_color") or {}
    primary = dc.get("primary") or {}
    name = primary.get("name")
    hx = primary.get("hex")

    if isinstance(name, str):
        name = name.strip().lower()
    else:
        name = None

    if (not hx) and name:
        hx = CANON_HEX.get(name)

    p["Color"] = name
    p["colorHex"] = hx


# ---------------------------
# IO
# ---------------------------

def iter_products(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        head = f.read(2048)
        f.seek(0)
        if head.lstrip().startswith("["):
            data = json.load(f)
            if isinstance(data, list):
                for obj in data:
                    if isinstance(obj, dict):
                        yield obj
            return
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
            except Exception:
                continue

def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)

def write_products(path: str, products: Iterable[Dict[str, Any]], as_json: bool) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if as_json:
        arr = list(products)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(arr, f, ensure_ascii=False, indent=2, default=_json_default)
    else:
        with open(path, "w", encoding="utf-8") as f:
            for obj in products:
                f.write(json.dumps(obj, ensure_ascii=False, default=_json_default) + "\\n")

def is_json_output(path: str) -> bool:
    return path.lower().endswith(".json") and not path.lower().endswith(".jsonl")


# ---------------------------
# Main
# ---------------------------

# ---------------------------
# Main
# ---------------------------

# ---------------------------
# Main (UPDATED FOR TAXONOMY JSON)
# ---------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Input taxonomy JSON file")
    ap.add_argument("--output", required=True, help="Output enriched taxonomy JSON file")
    ap.add_argument("--cache-dir", default=r".\\_cache_shoe_color", help="Cache directory for downloaded images")
    ap.add_argument("--no-image-fallback", action="store_true", help="Disable image-based inference fallback")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing detected_color if present")
    args = ap.parse_args()

    total = text_found = image_found = skipped_existing = failures = 0

    # ✅ Load full taxonomy JSON
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    taxonomy_paths = list(data.keys())

    iterator = tqdm(taxonomy_paths, desc="Processing Taxonomy Paths") if tqdm else taxonomy_paths

    for path in iterator:

        products = data[path].get("products", [])

        for p in products:
            total += 1

            if (not args.overwrite) and isinstance(p.get("detected_color"), dict) and (p["detected_color"].get("primary") is not None):
                skipped_existing += 1
                promote_color_fields(p)
                continue

            # ---------- TEXT EXTRACTION ----------
            cr = extract_text_color(p)

            if cr.primary_name:
                text_found += 1
                p["detected_color"] = cr.to_dict()
                promote_color_fields(p)
                continue

            # ---------- IMAGE FALLBACK ----------
            if args.no_image_fallback:
                p["detected_color"] = cr.to_dict()
                promote_color_fields(p)
                continue

            url = pick_main_image_url(p)

            if not url:
                failures += 1
                p["detected_color"] = cr.to_dict()
                promote_color_fields(p)
                continue

            try:
                img_res = infer_color_from_image_url(url, cache_dir=args.cache_dir)
                if img_res.primary_name:
                    image_found += 1
                    img_res.is_patterned = bool(img_res.is_patterned or cr.is_patterned)
                    p["detected_color"] = img_res.to_dict()
                else:
                    failures += 1
                    p["detected_color"] = cr.to_dict()
            except Exception:
                failures += 1
                p["detected_color"] = cr.to_dict()

            promote_color_fields(p)

    # ✅ Write back SAME STRUCTURE
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)

    print(json.dumps({
        "input": args.input,
        "output": args.output,
        "total_products_processed": total,
        "text_color_found": text_found,
        "image_color_found": image_found,
        "skipped_existing": skipped_existing,
        "failures": failures,
        "image_fallback_enabled": (not args.no_image_fallback)
    }, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())



if __name__ == "__main__":
    sys.exit(main())
