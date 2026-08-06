from typing import Literal

from pydantic import BaseModel

ModelStatus = Literal["active", "fallback", "disabled"]


class RoutedModel(BaseModel):
    id: str
    name: str
    provider: str
    cost: float
    weight: int
    status: ModelStatus
    # Both `calls7d` and `accuracy` are historical fixture fields that the
    # Routing admin UI doesn't edit anymore — `calls7d` is now computed from
    # `llm_calls`, and real accuracy lives in the Learning Loop screen.
    # Optional + default so the +Add model form (which omits them) saves
    # cleanly while old fixtures that DO carry these fields still round-trip.
    calls7d: int = 0
    accuracy: float | None = None


class Tier(BaseModel):
    id: str
    name: str
    models: list[RoutedModel]


class Thresholds(BaseModel):
    autoApprove: float
    escalateT2: float
    escalateT3: float
    humanReview: float
    # M28 · auto-approve threshold for **document extraction** confidence
    # (separate from requirement-matching autoApprove above). None = feature
    # OFF, every doc waits for human signoff regardless of AI confidence.
    # When set (typically 0.90–0.99), the ingestion + edit + recategorize
    # pipelines call try_auto_approve() and flip status to `reviewed` with
    # reviewedBy=`ai-auto` if the doc passes all blocker checks AND confidence
    # ≥ threshold.
    documentAutoApprove: float | None = None


class RoutingRule(BaseModel):
    id: str
    name: str
    priority: int
    hits: int
    active: bool
    condition: str
    action: str


class RoutingConfig(BaseModel):
    tiers: list[Tier]
    thresholds: Thresholds
    rules: list[RoutingRule]
