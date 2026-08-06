"""Thin HTTP client for the DocAIQuest REST API.

Every request is authenticated with an owner-scoped API key sent in the
``X-API-Key`` header (a key looks like ``dq_live_...``).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests


class DocaiquestError(Exception):
    """Raised when the DocAIQuest API returns a non-2xx response.

    ``status_code`` holds the HTTP status; the message is taken from the
    response body's ``detail`` field when present, otherwise the raw body.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class Client:
    """A minimal, dependency-light DocAIQuest API client.

    Example::

        from docaiquest import Client
        client = Client("dq_live_...")
        print(client.ask("Which invoices are due this month?")["answer"])
    """

    def __init__(self, api_key: str, base_url: str = "https://docaiq.jicama.tech"):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        # Strip trailing slash so path joins are predictable.
        self.base_url = base_url.rstrip("/")

    # -- internal ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Perform an authenticated request and raise on non-2xx."""
        headers = kwargs.pop("headers", {}) or {}
        headers["X-API-Key"] = self.api_key

        resp = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            **kwargs,
        )

        if not (200 <= resp.status_code < 300):
            detail: str
            try:
                body = resp.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                detail = detail or resp.text
            except ValueError:
                detail = resp.text
            raise DocaiquestError(detail or f"HTTP {resp.status_code}", resp.status_code)

        return resp

    # -- public API -------------------------------------------------------

    def ask(self, question: str, top_k: int = 8) -> Dict[str, Any]:
        """Ask a grounded question across the owner's documents.

        Returns the parsed JSON with keys ``answer``, ``grounded``,
        ``confidence`` and ``citations``.
        """
        resp = self._request(
            "POST",
            "/api/v1/ask",
            json={"question": question, "topK": top_k},
        )
        return resp.json()

    def documents(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List the owner's documents, returning the ``documents`` array."""
        resp = self._request("GET", f"/api/v1/documents?limit={limit}")
        return resp.json().get("documents", [])

    def extract(self, file_path: str) -> Dict[str, Any]:
        """Extract structured fields from a single document file.

        Uploads the file as multipart form field ``file`` and returns the
        parsed JSON (``status``, ``docType``, ``fields``, ``citations``,
        ``confidence``).
        """
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            resp = self._request(
                "POST",
                "/api/extraction/extract",
                files={"file": (filename, fh)},
            )
        return resp.json()
