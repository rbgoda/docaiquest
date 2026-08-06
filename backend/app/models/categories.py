from typing import Literal

from pydantic import BaseModel, Field

CategoryMode = Literal["expense", "income"]
CategoryScope = Literal["canonical", "global", "vendor"]


class Category(BaseModel):
    """One displayable category — canonical (hardcoded), global custom, or
    vendor-local custom. The frontend renders these in the picker; the
    `pk` is None for canonical entries (you can't delete the built-ins)."""
    pk: int | None = None
    name: str
    mode: CategoryMode
    scope: CategoryScope
    vendorPk: int | None = None
    createdBy: str | None = None
    createdAt: str | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: CategoryMode
    scope: Literal["global", "vendor"]
    vendorPk: int | None = None  # required when scope='vendor'
