ATTRIBUTE_CONFIG = {
    "color": {
        "strategy": "color_strategy"
    },
    "gender": {
        "strategy": "text",
        "values": ["men", "women", "kids"]
    },
    "brand": {
        "strategy": "direct"
    },
    "size": {
        "strategy": "regex"
    },
    "material": {
        "strategy": "text",
        "values": [
            "cotton",
            "polyester",
            "leather",
            "mesh",
            "denim",
            "linen",
            "wool",
            "silk",
            "nylon",
            "rubber"
        ]
    },
    "ram": {
        "strategy": "regex"
    },
    "storage": {
        "strategy": "regex"
    }
}