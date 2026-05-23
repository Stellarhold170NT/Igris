"""MongoDB Profiler Tool."""

from typing import Any

from app.integrations.mongodb import (
    build_mongodb_config,
    get_profiler_data,
    mongodb_database_is_available,
    mongodb_extract_params,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_profiler_data",
    description="Retrieve slow queries from the MongoDB database system.profile collection (requires profiling enabled).",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_database_is_available,
    extract_params=mongodb_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "The specific database name to retrieve profiler data from.",
            },
            "threshold_ms": {
                "type": "integer",
                "description": "Minimum query duration in milliseconds to include.",
                "default": 100,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of profiler entries to return.",
                "default": 50,
            },
        },
        "required": ["database"],
    },
)
def get_mongodb_profiler_data(
    connection_string: str = "",
    database: str = "",
    threshold_ms: int = 100,
    auth_source: str = "admin",
    tls: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch recent slow query entries for a specific database."""
    config = build_mongodb_config(
        {
            "connection_string": connection_string,
            "database": database,
            "auth_source": auth_source,
            "tls": tls,
            "max_results": max_results,
        }
    )
    return get_profiler_data(config, threshold_ms=threshold_ms, limit=max_results)
