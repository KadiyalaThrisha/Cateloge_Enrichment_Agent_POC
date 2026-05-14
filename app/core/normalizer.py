"""
Raw feed → dict shaped for CanonicalProduct.

Supports multiple merchant/ERP/API shapes via field aliases; infers source_system
when not provided. Preserves backward compatibility with Amazon-style JSON.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape")
    except Exception:
        return text


def fix_mojibake(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _first_nonempty_str(*candidates: Any) -> str:
    for v in candidates:
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _pick_title(data: Dict[str, Any]) -> str:
    return _first_nonempty_str(
        data.get("title"),
        data.get("product_title"),
        data.get("name"),
        data.get("product_name"),
        data.get("item_name"),
        data.get("display_name"),
    )


def _pick_description(data: Dict[str, Any]) -> str:
    keys = (
        "description",
        "long_description",
        "product_description",
        "body_html",
        "summary",
        "short_description",
    )
    for key in keys:
        raw = data.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            parts = [str(x).strip() for x in raw if x is not None and str(x).strip()]
            if parts:
                return " ".join(parts)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _pick_features(data: Dict[str, Any]) -> List[str]:
    keys = (
        "features",
        "bullet_points",
        "highlights",
        "key_features",
        "product_highlights",
    )
    for key in keys:
        raw = data.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            s = raw.strip()
            return [s] if s else []
        if isinstance(raw, list):
            out: List[str] = []
            for item in raw:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                elif isinstance(item, dict):
                    t = item.get("text") or item.get("value") or item.get("name")
                    if isinstance(t, str) and t.strip():
                        out.append(t.strip())
            if out:
                return out
    return []


def _pick_categories(data: Dict[str, Any]) -> List[str]:
    keys = ("categories", "category_path", "product_category", "breadcrumbs")
    for key in keys:
        raw = data.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            # "A > B > C" or single path
            if ">" in raw:
                parts = [p.strip() for p in raw.split(">") if p.strip()]
                if parts:
                    return parts
            s = raw.strip()
            return [s] if s else []
        if isinstance(raw, list):
            out = [str(x).strip() for x in raw if x is not None and str(x).strip()]
            if out:
                return out
    return []


def _image_url_from_item(img: Any) -> Optional[str]:
    if isinstance(img, str) and img.strip():
        return img.strip()
    if isinstance(img, dict):
        for k in ("hi_res", "large", "medium", "thumb", "url", "src", "link", "href"):
            val = img.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _pick_images(data: Dict[str, Any]) -> List[str]:
    keys = ("images", "image_urls", "media", "product_images")
    urls: List[str] = []
    for key in keys:
        raw = data.get(key)
        if not raw:
            continue
        if isinstance(raw, str) and raw.strip():
            urls.append(raw.strip())
        elif isinstance(raw, list):
            for item in raw:
                u = _image_url_from_item(item)
                if u:
                    urls.append(u)
    seen: set = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _parse_price_string(raw: str) -> Optional[float]:
    """Parse common US/EU price strings into float."""
    s = re.sub(r"[$€£\s]", "", (raw or "").strip())
    if not s:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            # e.g. 1.234,56 → decimal comma
            s = s.replace(".", "").replace(",", ".")
        else:
            # e.g. 1,234.56 → thousands comma
            s = s.replace(",", "")
    elif has_comma and not has_dot:
        parts = s.rsplit(",", 1)
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 2:
            # e.g. 19,99 → decimal comma
            s = parts[0].replace(",", "") + "." + parts[1]
        else:
            s = s.replace(",", "")
    # lone dot or digits-only: use as-is (strip stray commas)
    else:
        s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return None


def _pick_price(data: Dict[str, Any]) -> Optional[float]:
    keys = ("price", "list_price", "sale_price", "unit_price", "amount", "msrp")
    for key in keys:
        raw = data.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            parsed = _parse_price_string(raw)
            if parsed is not None:
                return parsed
    return None


def _pick_brand(data: Dict[str, Any]) -> Optional[str]:
    s = _first_nonempty_str(
        data.get("brand"),
        data.get("manufacturer"),
        data.get("store"),
        data.get("vendor"),
        data.get("seller_name"),
    )
    return s or None


def _pick_source_product_id(data: Dict[str, Any]) -> Optional[str]:
    keys = (
        "source_product_id",
        "product_id",
        "id",
        "sku",
        "merchant_sku",
        "supplier_sku",
        "parent_asin",
        "asin",
        "item_id",
        "erp_item_id",
    )
    for key in keys:
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _infer_source_system(data: Dict[str, Any]) -> str:
    explicit = data.get("source_system") or data.get("source")
    if isinstance(explicit, str) and explicit.strip():
        key = explicit.strip().lower().replace(" ", "_")
        aliases = {
            "amazon": "amazon_raw",
            "amazon_marketplace": "amazon_raw",
            "merchant": "merchant_feed",
            "erp": "erp_export",
        }
        return aliases.get(key, key)

    if data.get("asin") or data.get("parent_asin"):
        return "amazon_raw"
    if data.get("erp_item_id") or data.get("material_number"):
        return "erp_export"
    if data.get("merchant_sku") or data.get("supplier_sku") or data.get("sku"):
        return "merchant_feed"

    return "unknown"


def normalize_raw_data(data: dict) -> dict:
    if not isinstance(data, dict):
        raise TypeError("normalize_raw_data expects a dict")

    title_raw = fix_mojibake(clean_text(_pick_title(data))) or ""
    if isinstance(title_raw, str):
        title_raw = title_raw.strip()
    else:
        title_raw = str(title_raw).strip()

    desc_raw = fix_mojibake(clean_text(_pick_description(data)))
    if desc_raw is None:
        desc_raw = ""
    elif not isinstance(desc_raw, str):
        desc_raw = str(desc_raw)
    desc_raw = desc_raw.strip()

    return {
        "raw_title": title_raw,
        "normalized_title": _collapse_ws(title_raw) or None,
        "raw_description": desc_raw,
        "raw_features": _pick_features(data),
        "raw_price": _pick_price(data),
        "raw_images": _pick_images(data),
        "raw_brand": _pick_brand(data),
        "raw_categories": _pick_categories(data),
        "source_product_id": _pick_source_product_id(data),
        "source_system": _infer_source_system(data),
    }
