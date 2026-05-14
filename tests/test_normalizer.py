"""Regression tests for raw feed canonicalization."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.core.normalizer import normalize_raw_data

_DATA = Path(__file__).resolve().parent.parent / "data"


class TestNormalizeRawData(unittest.TestCase):
    def test_amazon_style_fixture(self) -> None:
        # Self-contained so it does not depend on whatever is in sample_input.json.
        raw = {
            "asin": "APPAREL_WOMEN_001",
            "title": "Women's Cotton Kurti Printed Casual Wear Size M",
            "description": "Comfortable cotton kurti suitable for daily wear.",
            "features": ["100% Cotton", "Regular fit"],
            "images": [{"hi_res": "https://example.com/img.jpg"}],
            "price": 799.0,
            "brand": "Generic",
            "categories": ["Clothing", "Women", "Ethnic Wear", "Kurtis"],
        }
        out = normalize_raw_data(raw)

        self.assertEqual(out["source_product_id"], "APPAREL_WOMEN_001")
        self.assertEqual(out["source_system"], "amazon_raw")
        self.assertIn("Kurti", out["raw_title"])
        self.assertEqual(out["normalized_title"], out["raw_title"])
        self.assertTrue(out["raw_description"])
        self.assertEqual(len(out["raw_features"]), 2)
        self.assertEqual(out["raw_price"], 799.0)
        self.assertEqual(len(out["raw_images"]), 1)
        self.assertEqual(out["raw_brand"], "Generic")
        self.assertEqual(len(out["raw_categories"]), 4)

    def test_merchant_style_from_sample_merchant(self) -> None:
        path = _DATA / "sample_merchant.json"
        raw = json.loads(path.read_text(encoding="utf-8"))[0]
        out = normalize_raw_data(raw)

        self.assertEqual(out["source_product_id"], "MERCH-SHOE-01")
        self.assertEqual(out["source_system"], "merchant_feed")
        self.assertEqual(out["raw_title"], "Running Shoe Pro Mesh")
        self.assertAlmostEqual(out["raw_price"], 79.99)
        self.assertEqual(out["raw_brand"], "RunCo")
        self.assertEqual(len(out["raw_images"]), 1)
        self.assertEqual(out["raw_categories"], ["Footwear", "Sports", "Running"])
        self.assertEqual(len(out["raw_features"]), 2)

    def test_us_price_with_thousands_separator(self) -> None:
        out = normalize_raw_data(
            {
                "sku": "x",
                "product_name": "y",
                "price": "1,234.56",
            }
        )
        self.assertAlmostEqual(out["raw_price"], 1234.56)

    def test_eu_price_both_separators(self) -> None:
        out = normalize_raw_data(
            {
                "sku": "x",
                "product_name": "y",
                "price": "1.234,56",
            }
        )
        self.assertAlmostEqual(out["raw_price"], 1234.56)

    def test_explicit_amazon_alias_maps_to_amazon_raw(self) -> None:
        out = normalize_raw_data(
            {
                "asin": "B00TEST",
                "title": "Thing",
                "source_system": "amazon",
            }
        )
        self.assertEqual(out["source_system"], "amazon_raw")

    def test_description_as_list_joined(self) -> None:
        out = normalize_raw_data(
            {
                "sku": "z",
                "name": "n",
                "description": ["Part one.", "Part two."],
            }
        )
        self.assertEqual(out["raw_description"], "Part one. Part two.")

    def test_requires_dict(self) -> None:
        with self.assertRaises(TypeError):
            normalize_raw_data("not a dict")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
