"""Coral SQL Query Tool."""

import json
import subprocess
from typing import Any

from app.tools.tool_decorator import tool


def _always_available(_: Any) -> bool:
    return True


@tool(
    name="coral_query",
    description=(
        "Query multiple data sources (GitHub, GitLab, MySQL, Sentry, etc.) using Coral SQL. "
        "ALWAYS follow a Discovery-First workflow:\n"
        "1. List tables: SELECT * FROM coral.tables;\n"
        "2. List functions: SELECT * FROM coral.table_functions;\n"
        "3. Check columns/filters: SELECT * FROM coral.columns WHERE table_name = '...';\n"
        "4. Check source config: SELECT * FROM coral.inputs WHERE schema_name = '...';\n"
        "Then execute your data query. Standard SQL JOINs work across sources."
    ),
    source="coral",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Unified querying across multiple integrations",
        "Complex data joins between different platforms",
        "Discovering available data schemas and functions dynamically",
    ],
    is_available=_always_available,
)
def coral_query(sql: str) -> dict[str, Any]:
    """Execute a SQL query using the Coral runtime."""
    try:
        # We use --format json to get machine-readable output
        result = subprocess.run(
            ["coral", "sql", "--format", "json", sql],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip() or f"Coral exited with code {result.returncode}",
                "sql": sql,
            }

        try:
            data = json.loads(result.stdout)
            return {
                "ok": True,
                "data": data,
                "sql": sql,
            }
        except json.JSONDecodeError:
            # Fallback for non-JSON output if any
            return {
                "ok": True,
                "raw_output": result.stdout.strip(),
                "sql": sql,
            }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Query timed out after 60 seconds", "sql": sql}
    except Exception as e:
        return {"ok": False, "error": str(e), "sql": sql}
