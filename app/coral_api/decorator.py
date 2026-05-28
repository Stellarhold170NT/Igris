"""The @coralapi decorator for declaring Coral API functions."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from .models import CoralApiFunction, CoralColumn, CoralFilter
from .registry import CoralApiRegistry


def coralapi(
    name: str,
    description: str,
    columns: dict[str, CoralColumn | str],
    filters: dict[str, CoralFilter | str | None] | None = None,
    source: str | None = None,
    is_available: Callable[[dict[str, dict]], bool] | None = None,
) -> Callable[[Callable[..., list[dict[str, Any]]]], Callable[..., list[dict[str, Any]]]]:
    """Decorator to register a function as a Coral API function.

    Args:
        name: Unique identifier for the function.
        description: Human-readable description of what the function returns.
        columns: Mapping of column name to CoralColumn or a type string shorthand.
        filters: Optional mapping of filter name to CoralFilter, a string shorthand
            for description, or None. String values are treated as the filter
            description with required=False.
        source: Optional source identifier for the function.
        is_available: Optional callable taking sources dict and returning bool.

    Returns:
        A decorator that preserves the original function's signature and
        registers it in the CoralApiRegistry.

    Shorthand normalization:
        - Column value as string: treated as type, creates CoralColumn(type=string)
        - Filter value as string: treated as description, creates CoralFilter(description=string)
        - Filter value as None: creates CoralFilter()
    """

    def _normalize_column(value: CoralColumn | str) -> CoralColumn:
        if isinstance(value, str):
            return CoralColumn(type=value)
        return value

    def _normalize_filter(
        value: CoralFilter | str | None,
    ) -> CoralFilter:
        if value is None:
            return CoralFilter()
        if isinstance(value, str):
            return CoralFilter(description=value)
        return value

    normalized_columns = {k: _normalize_column(v) for k, v in columns.items()}

    normalized_filters: dict[str, CoralFilter] = {}
    if filters is not None:
        normalized_filters = {k: _normalize_filter(v) for k, v in filters.items()}

    def decorator(
        func: Callable[..., list[dict[str, Any]]]
    ) -> Callable[..., list[dict[str, Any]]]:
        wrapped = functools.wraps(func)(func)

        api_fn = CoralApiFunction(
            func=wrapped,
            name=name,
            description=description,
            columns=normalized_columns,
            filters=normalized_filters,
            source=source,
            is_available=is_available,
        )

        CoralApiRegistry.register(api_fn)

        return wrapped

    return decorator
