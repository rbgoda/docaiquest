"""Official Python SDK for the DocAIQ document-intelligence API."""

from .client import Client, DocaiqError

__version__ = "0.1.0"

__all__ = ["Client", "DocaiqError", "__version__"]
