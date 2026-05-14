from __future__ import annotations

import re
from typing import Optional

from app.core.canonical_model import CanonicalProduct
from app.core.llm.groq_client import call_groq_llm


def infer_family_name_with_llm(canonical: CanonicalProduct) -> Optional[str]:
    """
    Infer a canonical family name for grouping fallback.

    Returns None when no trustworthy answer is produced.
    """
    title = (canonical.raw_title or "").strip()
    if not title:
        return None

    taxonomy_path = ((canonical.predicted_taxonomy or {}).get("path")) or "Unknown"
    identity = canonical.identity_attributes or {}
    variant = canonical.variant_attributes or {}

    prompt = f"""
You are an e-commerce catalog grouping assistant.
Return the BASE FAMILY NAME for catalog grouping (one line, no quotes).

Input:
- Title: {title}
- Taxonomy: {taxonomy_path}
- Identity attributes: {identity}
- Variant attributes: {variant}

Rules:
- Preserve brand and full product line identity, including model numbers in the name
  (e.g. "iPhone 14 Plus", "iPhone 8", "Galaxy S8" — do not strip digits that are part of the model).
- Remove only clear variant facets: storage/RAM (e.g. 128GB, 6GB RAM), color when obvious,
  carrier/lock (e.g. AT&T, T-Mobile, Unlocked), renewed/refurbished boilerplate when it is not
  needed to tell products apart, pack counts, screen size in inches if redundant with model.
- For accessories (cases, cables, trays, repair parts), keep a concise family: brand + what it is
  + primary compatible device line if stated (do not leave empty gaps or stray punctuation).
- Never return a name with doubled commas, empty parentheses, or trailing junk punctuation;
  normalize spaces.
- Do not output explanations.
- Output ONLY the family name text on one line.
"""

    try:
        response = call_groq_llm(prompt)
    except Exception:
        return None

    if not response:
        return None

    # Keep only a clean single line family label.
    line = response.strip().splitlines()[0].strip().strip("\"'")
    line = re.sub(r"\s+", " ", line)

    if not line:
        return None

    # Filter obviously invalid responses.
    lowered = line.lower()
    if lowered in {"unknown", "n/a", "none", "no family"}:
        return None

    return line

