"""Category-aware attribute extraction."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import webcolors

from app.config.attribute_config import ATTRIBUTE_CONFIG
from app.config.category_attribute_config import CATEGORY_ATTRIBUTE_CONFIG
from app.core.attribute_strategies.color_strategy import (
    detect_color,
    extract_text_color,
    normalize_color_token,
)
from app.core.canonical_model import CanonicalProduct


class AttributeService:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    @staticmethod
    def get_category_key(taxonomy_path: Optional[str]) -> str:
        if not taxonomy_path:
            return "default"

        path_lower = taxonomy_path.lower()

        for category, config in CATEGORY_ATTRIBUTE_CONFIG.items():
            keywords = config.get("keywords", [])
            for keyword in keywords:
                if keyword in path_lower:
                    return category

        return "default"

    def extract(self, canonical: CanonicalProduct) -> CanonicalProduct:
        taxonomy = canonical.predicted_taxonomy
        path = taxonomy.get("path") if taxonomy else None

        category_key = self.get_category_key(path)

        if self.verbose:
            print(f"  [attrs] taxonomy: {path}")
            print(f"  [attrs] category_key: {category_key}")

        combined_text_parts: List[str] = []

        if canonical.raw_title:
            combined_text_parts.append(canonical.raw_title)
        if canonical.raw_description:
            combined_text_parts.append(canonical.raw_description)
        if canonical.raw_features:
            combined_text_parts.extend(canonical.raw_features)
        if canonical.raw_categories:
            combined_text_parts.extend(canonical.raw_categories)
        if getattr(canonical, "raw_highlights", None):
            combined_text_parts.extend(canonical.raw_highlights)
        if getattr(canonical, "raw_specs", None):
            combined_text_parts.extend(canonical.raw_specs)

        combined_text = " ".join(combined_text_parts).lower()

        attributes: Dict[str, Any] = {}

        allowed_attributes = CATEGORY_ATTRIBUTE_CONFIG.get(category_key, {}).get("attributes", [])
        if self.verbose:
            print(f"  [attrs] allowed: {allowed_attributes}")

        for attr in allowed_attributes:
            config = ATTRIBUTE_CONFIG.get(attr, {})
            strategy = config.get("strategy")

            if strategy == "color_strategy":
                # Title/features mention of color is the highest-priority signal.
                title_color = extract_text_color(
                    canonical.raw_title or "",
                    canonical.raw_features or [],
                )
                if title_color:
                    normalized = normalize_color_token(title_color) or title_color
                    hex_value = None
                    try:
                        hex_value = webcolors.name_to_hex(normalized.lower())
                    except ValueError:
                        hex_value = None
                    attributes[attr] = {
                        "name": normalized.capitalize(),
                        "hex": hex_value,
                    }
                    continue

                color_result = detect_color(canonical, verbose=self.verbose)

                if isinstance(color_result, dict) and color_result.get("needs_review"):
                    if not canonical.provenance:
                        canonical.provenance = {}
                    if "review" not in canonical.provenance:
                        canonical.provenance["review"] = {}
                    canonical.provenance["review"]["color"] = color_result

                elif isinstance(color_result, dict) and "name" in color_result:
                    attributes[attr] = {
                        "name": color_result["name"].capitalize(),
                        "hex": color_result.get("hex"),
                    }

            elif strategy == "direct":
                if canonical.raw_brand:
                    attributes[attr] = canonical.raw_brand

            elif strategy == "text":
                values = config.get("values", [])
                for value in values:
                    if re.search(rf"\b{value}\b", combined_text):
                        attributes[attr] = value.capitalize()
                        break

            elif strategy == "regex":
                if attr == "size":
                    match = re.search(
                        r"\bsize\s*([xsmlXL0-9]+)", combined_text, re.IGNORECASE
                    )
                    if match:
                        attributes[attr] = match.group(1).upper()
                elif attr == "ram":
                    ram_match = re.search(
                        r"\b(\d+)\s*gb\s*ram\b|\bram\s*(\d+)\s*gb\b",
                        combined_text,
                        re.IGNORECASE,
                    )
                    if ram_match:
                        gb = ram_match.group(1) or ram_match.group(2)
                        attributes[attr] = f"{gb}GB"
                elif attr == "storage":
                    storage_match = re.search(
                        r"\b(\d+)\s*(gb|tb)\s*(?:storage|ssd|hdd)\b|\b(?:storage|ssd|hdd)\s*(\d+)\s*(gb|tb)\b",
                        combined_text,
                        re.IGNORECASE,
                    )
                    if storage_match:
                        size = storage_match.group(1) or storage_match.group(3)
                        unit = storage_match.group(2) or storage_match.group(4)
                        attributes[attr] = f"{size}{unit.upper()}"

        if path:
            path_parts = path.split(" > ")
            if path_parts:
                attributes["product_type"] = path_parts[-1]

        category_config = CATEGORY_ATTRIBUTE_CONFIG.get(category_key, {})
        identity_keys = category_config.get("identity", [])
        variant_keys = category_config.get("variant", [])

        identity_attributes: Dict[str, Any] = {}
        variant_attributes: Dict[str, Any] = {}

        for key, value in attributes.items():
            if key in identity_keys:
                identity_attributes[key] = value
            elif key in variant_keys:
                variant_attributes[key] = value

        canonical.attributes = attributes
        canonical.identity_attributes = identity_attributes
        canonical.variant_attributes = variant_attributes

        if self.verbose:
            review_block = (canonical.provenance or {}).get("review") or {}
            review_keys = sorted(review_block.keys())
            print(
                "  [attrs] result "
                f"id={canonical.source_product_id} "
                f"attrs={sorted(list(attributes.keys()))} "
                f"identity={sorted(list(identity_attributes.keys()))} "
                f"variant={sorted(list(variant_attributes.keys()))} "
                f"needs_review={review_keys}"
            )

        return canonical
