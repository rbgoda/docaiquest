"""Reusable cross-cutting services. Routers depend on this layer for
logic that's bigger than a repository (multi-table mutations, async jobs
modeled synchronously, etc.) but smaller than an agent."""
