from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from typing import Optional
from typing import List

raw_features: List = []
raw_images: List = []
raw_categories: List = []


class CanonicalProduct(BaseModel):
    # Source Info (BRD aligned)
    source_product_id: Optional[str] = None
    source_system: Optional[str] = None

    # Raw Input
    raw_title: str
    raw_description: Optional[str] = None

    raw_features: list = []
    
    raw_images: list = []
    
    raw_categories: list = []

   

    raw_price: Optional[float] = None
    raw_brand: Optional[str] = None


    # Normalized Fields
    normalized_title: Optional[str] = None

    # Taxonomy
    predicted_taxonomy: Optional[Dict] = None  # {"path": "...", "confidence": 0.0}

    # Attributes
    attributes: Dict = Field(default_factory=dict)
    identity_attributes: dict = {}
    variant_attributes: dict = {}
    # Grouping
    family_name: Optional[str] = None
    family_signature: Optional[str] = None   # NEW (BRD aligned)
    group_id: Optional[str] = None           # NEW (for variants)
    variant_axes: List[str] = Field(default_factory=list)
    variants: List[Dict] = Field(default_factory=list)

    # Content
    content: Dict = Field(default_factory=dict)

    # Media
    media: Dict = Field(default_factory=dict)

    # Quality / Validation
    quality: Dict = Field(default_factory=dict)

    # Provenance (BRD important)
    provenance: Dict = Field(default_factory=dict)