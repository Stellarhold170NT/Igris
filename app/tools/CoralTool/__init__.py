"""Coral SQL Capture Tool — system state snapshot via unified SQL."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.coral_api.manager import CoralManager
from app.tools.tool_decorator import tool

_manager: CoralManager | None = None


def _resolve_coral_binary() -> str | None:
    if env_path := os.environ.get("CORAL_BINARY"):
        return env_path if Path(env_path).exists() else None

    project_bin = Path(__file__).parent.parent.parent.parent / "bin" / "coral"
    if project_bin.exists():
        return str(project_bin)

    return shutil.which("coral")


def _get_manager(resolved: dict[str, dict]) -> CoralManager:
    global _manager
    if _manager is None:
        coral_binary = _resolve_coral_binary() or "coral"
        _manager = CoralManager(resolved, coral_binary=coral_binary)
    return _manager


def _is_coral_available(_resolved: dict[str, dict]) -> bool:
    enabled = os.getenv("CORAL_ENABLED", "").lower() in ("true", "1", "yes")
    binary = _resolve_coral_binary()
    return enabled and binary is not None


@tool(
    name="coral_query",
    description=(
        "Capture a unified snapshot of system state by querying multiple "
        "data sources (Grafana, Datadog, GitHub, Slack, etc.) through a single "
        "Coral SQL statement. Useful AFTER analysis to record system posture, "
        "not for deep log drilling or root-cause investigation.\n\n"
        "Discovery-First workflow when needed:\n"
        "1. SELECT * FROM coral.tables LIMIT 20;\n"
        "2. SELECT * FROM coral.columns WHERE table_name = '...';\n"
        "3. Then query with LIMIT."
    ),
    source="coral",
    surfaces=("chat",),
    use_cases=[
        "Post-incident system state snapshot",
        "Cross-platform data capture for audit/replay",
    ],
    is_available=_is_coral_available,
)
def coral_query(sql: str) -> dict[str, Any]:
    """Execute a Coral SQL snapshot query.

    Args:
        sql: A read-only Coral SQL query. Run discovery first
             (coral.tables, coral.columns) if table schema is unknown.
    """
    manager = _get_manager({})
    return manager.execute_sql(sql)
