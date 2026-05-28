"""Registry for Coral API functions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CoralApiFunction


class CoralApiRegistry:
    """Registry for Coral API functions.

    Class-level storage so the registry behaves as a singleton without
    requiring instantiation.
    """

    _functions: dict[str, CoralApiFunction] = {}

    @classmethod
    def register(cls, fn: CoralApiFunction) -> None:
        """Register a CoralApiFunction."""
        cls._functions[fn.name] = fn

    @classmethod
    def get(cls, name: str) -> CoralApiFunction | None:
        """Get a registered function by name, or None if not found."""
        return cls._functions.get(name)

    @classmethod
    def all(cls) -> dict[str, CoralApiFunction]:
        """Return a copy of all registered functions."""
        return dict(cls._functions)

    @classmethod
    def available(
        cls, sources: dict[str, dict]
    ) -> dict[str, CoralApiFunction]:
        """Return only functions where is_available passes for given sources."""
        return {
            name: fn
            for name, fn in cls._functions.items()
            if fn.is_available is None or fn.is_available(sources)
        }
