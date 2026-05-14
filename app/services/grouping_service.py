"""Family / variant grouping within a batch."""

from __future__ import annotations

import os
import re
import uuid
from collections import defaultdict
from typing import Any, Dict, List

from app.core.canonical_model import CanonicalProduct
from app.core.grouping_strategies.llm_grouping import infer_family_name_with_llm
from app.core.grouping_strategies.variant_axes_llm import infer_variant_axes_with_llm


class GroupingService:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self.llm_fallback_threshold = self._read_fallback_threshold()
        self._family_name_source = self._read_family_name_source()
        self._merge_by_family_key_without_category = (
            self._read_merge_by_family_key_without_category()
        )

    @staticmethod
    def _read_family_name_source() -> str:
        """
        GROUPING_FAMILY_NAME_SOURCE:
          - llm_only (default): family_name from LLM only; if LLM fails, use a light title fallback
            (no regex stripping — avoids mangled "Apple iPhone Plus, ," names).
          - hybrid: previous behavior — regex-cleaned title first, LLM when confidence is low.
        """
        raw = os.getenv("GROUPING_FAMILY_NAME_SOURCE", "llm_only").strip().lower()
        if raw in ("hybrid", "rule", "rules"):
            return "hybrid"
        return "llm_only"

    @staticmethod
    def _read_merge_by_family_key_without_category() -> bool:
        """
        When taxonomy has no category_id, still merge rows that share the same
        order-invariant family key (see _stable_family_key).

        GROUPING_MERGE_BY_FAMILY_NAME_KEY: default true. Set false to keep one SKU per group
        (ungrouped::<id>) when category_id is missing — avoids rare false merges.
        """
        raw = os.getenv("GROUPING_MERGE_BY_FAMILY_NAME_KEY", "true").strip().lower()
        return raw not in ("0", "false", "no", "off")

    @staticmethod
    def _stable_family_key(family_name: str) -> str:
        """
        Word-order invariant key so e.g. "Apple iPhone 13 Pro" and "iPhone 13 Pro Apple" collide.
        Same token multiset -> same group signature component.
        """
        raw = (family_name or "").strip().lower()
        if not raw:
            return "_empty_"
        raw = re.sub(r"[^\w\s]+", " ", raw, flags=re.UNICODE)
        parts = [p for p in raw.split() if p]
        if not parts:
            return "_empty_"
        return " ".join(sorted(parts))

    def run_batch(self, canonicals: List[CanonicalProduct]) -> List[CanonicalProduct]:
        if not canonicals:
            return canonicals

        if self.verbose:
            print(
                f"  [grouping] start batch size={len(canonicals)} "
                f"family_name_source={self._family_name_source!r} "
                f"merge_by_family_key_no_cat={self._merge_by_family_key_without_category} "
                f"llm_threshold={self.llm_fallback_threshold:.2f} "
                f"variant_axes_llm={self._variant_axes_llm_enabled()}"
            )

        for c in canonicals:
            if self._family_name_source == "llm_only":
                family_name, method, provenance_fb = self._family_name_llm_primary(c)
            else:
                family_name, method, provenance_fb = self._family_name_hybrid(c)

            confidence = self._compute_grouping_confidence(c, family_name)

            self._ensure_quality(c)
            c.quality["grouping_confidence"] = round(confidence, 3)
            c.quality["grouping_method"] = method

            if provenance_fb is not None:
                self._ensure_provenance(c)
                c.provenance["grouping_fallback"] = provenance_fb
            elif c.provenance and "grouping_fallback" in c.provenance:
                c.provenance.pop("grouping_fallback", None)

            if self.verbose:
                print(
                    f"  [grouping] product={c.source_product_id} "
                    f"family={family_name!r} method={method} conf={confidence:.3f}"
                )

            family_signature = self._derive_family_signature(c, family_name)
            c.family_name = family_name
            c.family_signature = family_signature

        grouped: Dict[str, List[CanonicalProduct]] = defaultdict(list)
        for c in canonicals:
            grouped[c.family_signature].append(c)

        for signature, members in grouped.items():
            group_id = f"grp_{uuid.uuid5(uuid.NAMESPACE_DNS, signature).hex[:12]}"
            variant_axes = self._derive_variant_axes(members)
            axes_source = "rule_discriminating_values" if variant_axes else "none"

            if not variant_axes and self._variant_axes_llm_enabled():
                llm_axes = infer_variant_axes_with_llm(members)
                if llm_axes:
                    variant_axes = llm_axes
                    axes_source = "llm_inference"
                    if self.verbose:
                        print(
                            f"  [grouping] variant_axes_llm family={members[0].family_name!r} "
                            f"members={len(members)} axes={variant_axes}"
                        )
                elif self.verbose:
                    print(
                        f"  [grouping] variant_axes_llm skipped/failed "
                        f"family={members[0].family_name!r} members={len(members)}"
                    )

            for member in members:
                member.group_id = group_id
                member.variant_axes = variant_axes
                member.variants = [
                    {
                        "source_product_id": m.source_product_id,
                        "variant_attributes": m.variant_attributes or {},
                    }
                    for m in members
                ]
                # Group-level confidence is the minimum member confidence in the final group.
                self._ensure_quality(member)
                group_conf = min(
                    float((m.quality or {}).get("grouping_confidence", 0.0)) for m in members
                )
                member.quality["grouping_group_confidence"] = round(group_conf, 3)
                member.quality["variant_axes_source"] = axes_source

            if axes_source == "llm_inference" and variant_axes:
                for member in members:
                    self._ensure_provenance(member)
                    member.provenance["variant_axes_llm"] = {
                        "axes": list(variant_axes),
                        "group_member_count": len(members),
                    }
            else:
                for member in members:
                    self._ensure_provenance(member)
                    if "variant_axes_llm" in member.provenance:
                        member.provenance.pop("variant_axes_llm", None)

            if self.verbose:
                print(
                    f"  [grouping] family={members[0].family_name!r} "
                    f"members={len(members)} axes={variant_axes}"
                )

        if self.verbose:
            llm_named = sum(
                1
                for c in canonicals
                if (c.quality or {}).get("grouping_method")
                in ("llm_only", "llm_fallback", "llm_unavailable_title_fallback")
            )
            print(
                f"  [grouping] done groups={len(grouped)} "
                f"llm_family_methods={llm_named}/{len(canonicals)}"
            )

        return canonicals

    @staticmethod
    def _read_fallback_threshold() -> float:
        raw = os.getenv("GROUPING_LLM_FALLBACK_THRESHOLD", "0.75").strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.75
        return min(max(value, 0.0), 1.0)

    @staticmethod
    def _variant_axes_llm_enabled() -> bool:
        raw = os.getenv("VARIANT_AXES_LLM_ENABLED", "true").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _simple_title_fallback(self, canonical: CanonicalProduct) -> str:
        """No regex stripping — avoids mangling model numbers (e.g. iPhone 14) in titles."""
        raw = (canonical.normalized_title or canonical.raw_title or "").strip()
        if not raw:
            return "unknown_family"
        return " ".join(raw.split())

    def _family_name_llm_primary(
        self, canonical: CanonicalProduct
    ) -> tuple[str, str, Dict[str, Any] | None]:
        llm_family = infer_family_name_with_llm(canonical)
        if llm_family:
            return llm_family, "llm_only", None
        fb_name = self._simple_title_fallback(canonical)
        return (
            fb_name,
            "llm_unavailable_title_fallback",
            {
                "trigger": "llm_family_name_unavailable",
                "detail": "Groq/LLM returned no usable line; used normalized title as family name.",
            },
        )

    def _family_name_hybrid(
        self, canonical: CanonicalProduct
    ) -> tuple[str, str, Dict[str, Any] | None]:
        family_name = self._derive_family_name(canonical)
        confidence = self._compute_grouping_confidence(canonical, family_name)
        if confidence < self.llm_fallback_threshold:
            llm_family = infer_family_name_with_llm(canonical)
            if llm_family:
                return (
                    llm_family,
                    "llm_fallback",
                    {
                        "trigger": "grouping_confidence_below_threshold",
                        "threshold": self.llm_fallback_threshold,
                        "grouping_confidence": round(confidence, 3),
                    },
                )
        return family_name, "rule_based", None

    @staticmethod
    def _ensure_quality(canonical: CanonicalProduct) -> None:
        if not canonical.quality:
            canonical.quality = {}

    @staticmethod
    def _ensure_provenance(canonical: CanonicalProduct) -> None:
        if not canonical.provenance:
            canonical.provenance = {}

    def _compute_grouping_confidence(self, canonical: CanonicalProduct, family_name: str) -> float:
        """
        Rule-based confidence for grouping reliability.
        This is intentionally independent from taxonomy confidence fallback behavior.
        """
        score = 1.0
        taxonomy = canonical.predicted_taxonomy or {}
        category_id = taxonomy.get("category_id")

        # Missing category_id means no safe cross-item merge allowed.
        if not category_id:
            score -= 0.35

        if not family_name or family_name == "unknown_family":
            score -= 0.25

        # If title looks highly variant-heavy after cleanup, confidence should be lower.
        raw_title = (canonical.raw_title or "").strip()
        cleaned_len = len((family_name or "").strip())
        raw_len = len(raw_title)
        if raw_len > 0:
            identity_ratio = cleaned_len / raw_len
            if identity_ratio < 0.45:
                score -= 0.2

        # No extracted variant attributes means weaker grouping signal.
        variant_attrs = canonical.variant_attributes or {}
        if not variant_attrs:
            score -= 0.1

        return min(max(score, 0.0), 1.0)

    def _derive_family_name(self, canonical: CanonicalProduct) -> str:
        raw_title = (canonical.raw_title or "").strip()
        if not raw_title:
            return "unknown_family"
        cleaned = self._remove_variant_signals_from_title(raw_title, canonical)
        return " ".join(cleaned.split()) if cleaned.strip() else "unknown_family"

    def _derive_family_signature(self, canonical: CanonicalProduct, family_name: str) -> str:
        taxonomy = canonical.predicted_taxonomy or {}
        category_id = taxonomy.get("category_id")
        fam_key = self._stable_family_key(family_name)

        if category_id:
            return f"{category_id}||fam={fam_key}"

        if self._merge_by_family_key_without_category and fam_key != "_empty_":
            return f"no_category||fam={fam_key}"

        isolated_key = canonical.source_product_id or canonical.raw_title or "unknown"
        return f"ungrouped::{isolated_key}".lower()

    @staticmethod
    def _derive_variant_axes(members: List[CanonicalProduct]) -> List[str]:
        values_by_axis: Dict[str, set] = defaultdict(set)

        for m in members:
            for key, value in (m.variant_attributes or {}).items():
                values_by_axis[key].add(GroupingService._normalize_variant_value(value))

        axes = [axis for axis, vals in values_by_axis.items() if len(vals) > 1]
        return sorted(axes)

    @staticmethod
    def _normalize_variant_value(value: Any) -> str:
        if isinstance(value, dict):
            if "name" in value and value["name"] is not None:
                return str(value["name"]).strip().lower()
            return str(sorted(value.items())).lower()
        return str(value).strip().lower()

    def _remove_variant_signals_from_title(self, title: str, canonical: CanonicalProduct) -> str:
        """
        Build family identity from title by removing common variant tokens:
        color, RAM, storage, and size-like markers.
        """
        text = f" {title} "

        # Remove variant values already extracted for this record.
        # This keeps family identity separate from variant attributes.
        variant_values: List[str] = []
        for v in (canonical.variant_attributes or {}).values():
            if isinstance(v, dict) and v.get("name"):
                variant_values.append(str(v["name"]))
            elif isinstance(v, str):
                variant_values.append(v)

        for raw in variant_values:
            token = raw.strip()
            if not token:
                continue
            text = re.sub(rf"\b{re.escape(token)}\b", " ", text, flags=re.IGNORECASE)

        # Generic variant token patterns
        patterns = [
            r"\b(?:[1-9]\d{0,2})\s*(?:gb|tb)\s*(?:ram|storage|ssd|hdd)?\b",
            r"\b(?:ram|storage|ssd|hdd)\s*(?:[1-9]\d{0,2})\s*(?:gb|tb)\b",
            r"\b(?:ram|storage|ssd|hdd)\b",
            r"\bsize\s*[a-z0-9]+\b",
            r"\b(?:xs|s|m|l|xl|xxl|xxxl)\b",
            r"\b\d{1,2}(?:\.\d+)?\s*(?:uk|us|eu)?\b",
        ]
        for pat in patterns:
            text = re.sub(pat, " ", text, flags=re.IGNORECASE)

        return re.sub(r"\s+", " ", text).strip()
