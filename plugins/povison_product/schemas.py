"""Typed schemas for the Povison product lookup plugin."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


DEFAULT_STORE_ID = 3
DEFAULT_TIMEOUT_SECONDS = 15.0


class ProductLookupRequest(BaseModel):
    """Validated input for a Povison product lookup."""

    url: str = Field(..., min_length=1)
    include_raw: bool = False
    timeout_seconds: float = Field(DEFAULT_TIMEOUT_SECONDS, gt=0, le=60)

    @field_validator("url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("url is required")
        return cleaned


class ParsedProductLink(BaseModel):
    """Normalized product URL components used by the Povison API."""

    original_url: str
    host: str | None = None
    path: str
    variant: str
    store_id: int = DEFAULT_STORE_ID
