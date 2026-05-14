
from app.core.attribute_strategies.llm_color import detect_color_with_llm
import re
from typing import Optional, List

# =========================
# BASIC COLOR CONFIG
# =========================

COLOR_SYNONYMS = {
    "grey": "gray",
    "off white": "offwhite",
    "off-white": "offwhite",
    "navy blue": "navy",
    "dark blue": "navy",
    "charcoal": "gray",
    "burgundy": "maroon",
    "wine": "maroon",
    "cream": "offwhite",
    "ivory": "offwhite",
    "khaki": "beige",
}

CANON_COLORS = [
    "black", "white", "offwhite", "gray",
    "red", "maroon", "pink", "orange", "yellow",
    "green", "olive", "teal",
    "blue", "navy", "purple",
    "brown", "tan", "beige",
    "gold", "silver",
]

COLOR_TOKENS = sorted(
    set(CANON_COLORS) | set(COLOR_SYNONYMS.keys()) | set(COLOR_SYNONYMS.values()),
    key=len,
    reverse=True
)

# =========================
# TEXT EXTRACTION
# =========================

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def normalize_color_token(tok: str) -> Optional[str]:
    t = _norm_text(tok)
    t = t.replace("/", " ").replace("&", " ").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()

    if t in COLOR_SYNONYMS:
        t = COLOR_SYNONYMS[t]

    return t if t in CANON_COLORS else None

def find_colors_in_text(text: str) -> List[str]:
    t = _norm_text(text)
    found = []

    for tok in COLOR_TOKENS:
        pattern = r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])"
        if re.search(pattern, t):
            norm = normalize_color_token(tok)
            if norm and norm not in found:
                found.append(norm)

    return found

def extract_text_color(title: str, features: List[str]) -> Optional[str]:
    combined = f"{title} {' '.join(features or [])}"

    colors = find_colors_in_text(combined)

    if colors:
        return colors[0]

    return None


# =========================
# IMAGE FALLBACK (USING YOUR SCRIPT)
# =========================

# IMPORTANT: import your original function here
# Adjust path if needed
from color_map_shoes_clean import infer_color_from_image_url

def detect_color(canonical, verbose: bool = False):

    from app.core.attribute_strategies.llm_color import detect_color_with_llm
    from color_map_shoes_clean import infer_color_from_image_url

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    image_urls = canonical.raw_images or []

    best_result = None
    best_confidence = -1
    best_image_url = None

    # =========================
    # STEP 1: Evaluate all images
    # =========================
    for image_url in image_urls:

        _log(f"  [color] image: {image_url}")

        try:
            result = infer_color_from_image_url(
                image_url,
                cache_dir="./_cache"
            )

            if not result or not result.primary_name:
                continue

            confidence = result.confidence

            _log(f"  [color] vision: {result.primary_name!r} (conf={confidence:.3f})")

            # pick best image based on confidence
            if confidence > best_confidence:
                best_confidence = confidence
                best_result = result
                best_image_url = image_url

        except Exception as e:
            _log(f"  [color] vision error: {e}")
            continue

    # =========================
    # NO VALID IMAGE
    # =========================
    if not best_result:
        return {
            "value": None,
            "needs_review": True,
            "reason": "no_valid_image",
            "image_url": None,
            "category": canonical.predicted_taxonomy.get("path") if canonical.predicted_taxonomy else None,
            "product_id": canonical.source_product_id
        }

    # =========================
    # STEP 2: Apply logic on BEST image
    # =========================
    primary = best_result.primary_name
    confidence = best_result.confidence
    secondary = best_result.secondary_names or []
    taxonomy_path = canonical.predicted_taxonomy.get("path") if canonical.predicted_taxonomy else ""

    _log(f"  [color] best image: {best_image_url}")
    _log(f"  [color] best vision color: {primary!r} (conf={confidence:.3f})")

    # Vision says "patterned" (e.g. multi-color shoe) — still try LLM before review.
    is_mobile = taxonomy_path and "smartphones" in taxonomy_path.lower()
    is_patterned = getattr(best_result, "is_patterned", False)

    # LLM when vision is uncertain OR when patterned (non-phone) — not only conf < 0.78.
    needs_llm = confidence < 0.78 or (is_patterned and not is_mobile)

    if needs_llm:
        if confidence < 0.78:
            _log("  [color] conf < 0.78 → LLM fallback")
        else:
            _log("  [color] pattern / multi-color → LLM fallback")

        llm_result = detect_color_with_llm(
            canonical,
            best_image_url,
            primary,
            secondary,
            confidence,
        )

        if llm_result:
            return llm_result

        if confidence < 0.78:
            fail_reason = "llm_failed"
        else:
            fail_reason = "pattern_llm_failed"

        return {
            "value": None,
            "needs_review": True,
            "reason": fail_reason,
            "image_url": best_image_url,
            "category": taxonomy_path,
            "product_id": canonical.source_product_id,
        }

    # =========================
    # FINAL ACCEPT (confident, not patterned / or mobile)
    # =========================
    return {
        "name": primary,
        "hex": best_result.primary_hex,
    }