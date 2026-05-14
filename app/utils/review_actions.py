def apply_review_update(product, attribute, value):
    """
    Apply user-reviewed value to product
    """

    # 1. Update attribute
    if not product.get("attributes"):
        product["attributes"] = {}

    product["attributes"][attribute] = value.capitalize()

    # 2. Remove review flag
    if "provenance" in product and "review" in product["provenance"]:
        if attribute in product["provenance"]["review"]:
            del product["provenance"]["review"][attribute]

    return product