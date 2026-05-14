def collect_reviews(products):
    review_items = []

    for product in products:
        provenance = product.get("provenance", {})
        review_block = provenance.get("review", {})

        for attr, details in review_block.items():
            if details.get("needs_review"):

                review_items.append({
                    "product_id": details.get("product_id"),
                    "attribute": attr,
                    "reason": details.get("reason"),
                    "image_url": details.get("image_url"),
                    "category": details.get("category")
                })

    return review_items