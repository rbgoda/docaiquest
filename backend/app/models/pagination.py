"""Paginated response envelope (TODO #42).

Opt-in: every list endpoint that supports pagination accepts
`?limit=&offset=` query params and returns `Paginated[T]` when EITHER is
set; otherwise it returns the legacy list shape (backwards compatible
with existing frontend code that hasn't been migrated yet).

Frontend migration to envelope-aware fetches lands incrementally.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    """Standard paginated response envelope. Match this shape on every
    new list endpoint.

      items   — page of results
      total   — total rows matching the filter (NOT just this page)
      limit   — page size echoed back from the query
      offset  — page offset echoed back from the query

    Total is computed via a separate COUNT query at the repo layer.
    """
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
