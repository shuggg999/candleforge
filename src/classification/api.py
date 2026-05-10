"""FastAPI router for classification module."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from .classifier import Classifier

router = APIRouter(prefix="/api/v1/classification", tags=["classification"])

_classifier: Optional[Classifier] = None


def set_classifier(c: Optional[Classifier]) -> None:
    """Inject the singleton Classifier instance from the application lifespan."""
    global _classifier
    _classifier = c


def get_classifier() -> Classifier:
    if _classifier is None:
        raise HTTPException(status_code=503, detail="Classifier not initialized")
    return _classifier


@router.get("/tiers")
async def get_tiers() -> dict[str, str]:
    """Return current in-memory tier_cache as JSON {symbol: tier}."""
    classifier = get_classifier()
    return dict(classifier._tier_cache)
