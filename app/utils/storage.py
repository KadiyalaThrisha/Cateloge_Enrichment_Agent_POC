import json
import os


def save_products(products, file_path="data/output_products.json"):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)


def load_products(file_path="data/output_products.json"):
    if not os.path.exists(file_path):
        return []

    with open(file_path, encoding="utf-8") as f:
        return json.load(f)