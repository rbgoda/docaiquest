"""Tenant-scoped data access. Every function takes a Session as its first
arg (FastAPI injects via Depends) and reads the current tenant/owner from the
contextvars in app.db. Submodules are imported directly where needed
(`from app.repositories import documents`); no eager registry here.
"""
