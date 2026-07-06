"""
GUS connector package.

Exports the public API surface needed by the rest of the Aquarium feed layer.
"""

from .connector import (
    GusClient,
    CursorStore,
    InMemoryCursorStore,
    GusPoller,
    create_app,
)
from .config import (
    POLLING_INTERVAL_SECONDS,
    MAX_REQUESTS_PER_SECOND,
    RAPID_UPDATE_WINDOW_SECONDS,
)

__all__ = [
    "GusClient",
    "CursorStore",
    "InMemoryCursorStore",
    "GusPoller",
    "create_app",
    "POLLING_INTERVAL_SECONDS",
    "MAX_REQUESTS_PER_SECOND",
    "RAPID_UPDATE_WINDOW_SECONDS",
]
