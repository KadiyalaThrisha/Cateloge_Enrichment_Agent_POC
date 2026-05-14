"""LLM fallback when rule-based variant axes (multi-value within a group) are empty."""

from __future__ import annotations

import json
import re
from typing import List, Optional

from app.core.canonical_model import CanonicalProduct
from app.core.llm.groq_client import call_groq_llm


def _parse_axes_json(text: str) -> Optional[List[str]]:
    if not text:
        return None
    raw = text.strip()
    if "```" in raw:
        for chunk in raw.split("```"):
            chunk = chunk.strip()
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("["):
                raw = chunk
                break
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    out: List[str] = []
    for item in data:
        if isinstance(item, str):
            s = item.strip()
            if s and s not in out:
                out.append(s)
        if len(out) >= 10:
            break
    return out or None


def infer_variant_axes_with_llm(members: List[CanonicalProduct]) -> Optional[List[str]]:
    """
    Infer catalog variant axis names (e.g. Color, Storage, Size) for a product group.

    Used when rule-based axes are empty: typically single-SKU groups or uniform
    variant_attributes across siblings so no discriminating axis was found.

    Returns None when the model yields nothing usable.
    """
    if not members:
        return None

    first = members[0]
    taxonomy_path = ((first.predicted_taxonomy or {}).get("path")) or "Unknown"

    lines: List[str] = []
    for m in members[:15]:
        pid = m.source_product_id or "-"
        title = (m.raw_title or "").strip()
        if len(title) > 260:
            title = title[:259] + "…"
        vattrs = m.variant_attributes or {}
        lines.append(f"- id={pid} title={title!r} variant_attributes={vattrs}")

    block = "\n".join(lines)

    prompt = f"""
You are an e-commerce catalog assistant. A "variant axis" is a dimension that
differentiates SKUs in the same product family (examples: Color, Storage, RAM,
Size, Band Width, Carrier, Style).

You are given one or more rows that belong to the SAME grouping family.
Taxonomy path: {taxonomy_path}

Rows:
{block}

Task:
- Infer which variant axes are relevant for this family (even if only one row is listed).
- Prefer axes that appear as keys in variant_attributes when present; otherwise infer
  from titles and category (e.g. phones: Color, Storage; apparel: Color, Size).
- Use short Title Case labels (e.g. "Color", "Storage", "Size"). Max 6 axes.
- Output ONLY a JSON array of strings, nothing else. Example: ["Color","Storage"]

Rules:
- Do not include axes that are clearly identical across all rows unless the catalog
  would still list them as variant dimensions for shoppers.
- If you cannot infer any sensible axes, output exactly: []
"""

    try:
        response = call_groq_llm(prompt, max_completion_tokens=220)
    except Exception:
        return None

    if not response:
        return None

    axes = _parse_axes_json(response)
    if not axes:
        return None

    cleaned: List[str] = []
    for a in axes:
        s = re.sub(r"\s+", " ", str(a).strip())
        if not s:
            continue
        if s not in cleaned:
            cleaned.append(s)
    return cleaned or None
