from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CoralColumn:
    type: str
    description: str = ""
    nullable: bool = True


@dataclass(frozen=True)
class CoralFilter:
    required: bool = False
    description: str = ""


@dataclass
class CoralApiFunction:
    func: Callable[..., list[dict[str, Any]]]
    name: str
    description: str
    columns: dict[str, CoralColumn] = field(default_factory=dict)
    filters: dict[str, CoralFilter] = field(default_factory=dict)
    source: str | None = None
    is_available: Callable[[dict[str, dict]], bool] | None = None
