"""MongoDB Current Ops Tool."""

from typing import Any

from app.integrations.mongodb import (
    build_mongodb_config,
    get_current_ops,
    mongodb_extract_params,
    mongodb_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_current_ops",
    description="Retrieve currently executing MongoDB operations above a specific duration threshold.",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_is_available,
    extract_params=mongodb_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "threshold_ms": {
                "type": "integer",
                "description": "Only return operations running longer than this threshold in milliseconds.",
                "default": 1000,
            },
        },
    },
)
def get_mongodb_current_ops(
    connection_string: str = "",
    threshold_ms: int = 1000,
    auth_source: str = "admin",
    tls: bool = True,
    database: str = "",
) -> dict[str, Any]:
    """Fetch currently running operations above the threshold (default 1000ms)."""
    config = build_mongodb_config(
        {
            "connection_string": connection_string,
            "database": database,
            "auth_source": auth_source,
            "tls": tls,
        }
    )
    return get_current_ops(config, threshold_ms=threshold_ms)
