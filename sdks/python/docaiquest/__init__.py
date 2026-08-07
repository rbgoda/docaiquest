"""Official Python SDK for the DocAIQuest document-intelligence API."""

from .client import Client, DocaiquestError

__version__ = "1.0.3"

__all__ = ["Client", "DocaiquestError", "__version__"]
