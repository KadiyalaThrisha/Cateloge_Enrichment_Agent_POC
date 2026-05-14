from app.core.llm.groq_client import call_groq_llm

import webcolors

COLOR_NORMALIZATION_MAP = {
    "lavender": "purple",
    "violet": "purple",
    "purple": "purple",

    "navy": "blue",
    "skyblue": "blue",
    "cyan": "blue",
    "aqua": "blue",
    "blue": "blue",

    "mint": "green",
    "olive": "green",
    "lime": "green",
    "green": "green",

    "maroon": "red",
    "crimson": "red",
    "red": "red",

    "cream": "beige",
    "offwhite": "white",
    "ivory": "white",

    "golden": "gold",
    "silver": "silver",

    "tan": "brown",
    "beige": "beige",
    "brown": "brown",

    "black": "black",
    "white": "white",
    "gray": "gray",
    "grey": "gray",
    "pink": "pink",
    "yellow": "yellow",
    "orange": "orange"
}

def normalize_color(color_name: str):
    return COLOR_NORMALIZATION_MAP.get(color_name.lower(), color_name.lower())


def color_name_to_hex(color_name: str):
    try:
        return webcolors.name_to_hex(color_name.lower())
    except ValueError:
        return None

def detect_color_with_llm(canonical, image_url, primary, secondary, confidence):

    title = canonical.raw_title or ""
    category = canonical.predicted_taxonomy.get("path") if canonical.predicted_taxonomy else ""

    prompt = f"""
You are an expert e-commerce catalog assistant.

Your task is to determine the MOST accurate MAIN COLOR of a product.

Inputs:
Title: {title}
Category: {category}
Detected Primary Color: {primary}
Secondary Colors: {secondary}
Confidence: {confidence}
Image URL: {image_url}

IMPORTANT RULES:
- Return ONLY ONE color word
- Focus ONLY on the PRODUCT (ignore background)
- For MOBILE PHONES:
  - Ignore the front screen (black display)
  - Focus on the BACK PANEL color
- If image shows multiple colors, choose the dominant one
- If primary color is unreliable (low confidence), use image + secondary (just for reference)
- Convert technical shades:
  teal → green
  navy → blue
  maroon → red
- Prefer simple human-friendly colors


STRICT OUTPUT:
- Return ONLY ONE color
- No explanation
"""

    response = call_groq_llm(prompt, image_url=image_url)

    if not response:
        return None

    import re

    response_text = response.strip().lower()

    # extract words
    words = re.findall(r"[a-z]+", response_text)

    if not words:
        return None

    # take last word
    color_name = words[-1]

    normalized = normalize_color(color_name)

    return {
        "name": normalized.capitalize(),
        "hex": color_name_to_hex(normalized)
    }