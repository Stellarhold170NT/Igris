"""Enhanced Coral SQL Query Tool with source discovery and bridge support."""
from __future__ import annotations

import os
import shutil
from typing import Any

from app.coral_api.manager import CoralManager
from app.tools.tool_decorator import tool

_manager: CoralManager | None = None


def _get_manager(resolved: dict[str, dict]) -> CoralManager:
    global _manager
    if _manager is None:
        coral_binary = os.environ.get("CORAL_BINARY", "coral")
        _manager = CoralManager(resolved, coral_binary=coral_binary)
    return _manager


def _is_coral_available(_resolved: dict[str, dict]) -> bool:
    enabled = os.getenv("CORAL_ENABLED", "").lower() in ("true", "1", "yes")
    binary = shutil.which(os.environ.get("CORAL_BINARY", "coral"))
    return enabled and binary is not None


@tool(
    name="coral_query",
    description=(
        "Query multiple data sources (GitHub, GitLab, Datadog, Grafana, etc.) "
        "using Coral SQL. Supports JOINs across sources.\n\n"
        "ALWAYS follow a Discovery-First workflow:\n"
        "1. List tables: SELECT * FROM coral.tables;\n"
        "2. List functions: SELECT * FROM coral.table_functions;\n"
        "3. Check columns: SELECT * FROM coral.columns WHERE table_name = '...';\n"
        "4. Then execute your data query.\n\n"
        "Sources are enabled via CORAL_<SOURCE>=true env vars."
    ),
    source="coral",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Unified querying across multiple integrations",
        "Complex data joins between different platforms",
        "Discovering available data schemas dynamically",
    ],
    is_available=_is_coral_available,
)
def coral_query(sql: str) -> dict[str, Any]:
    """Execute a SQL query using the Coral runtime.

    Args:
        sql: A Coral SQL query. Start with discovery queries
             (coral.tables, coral.columns) before running data queries.
    """
    manager = _get_manager({})
    return manager.execute_sql(sql)
