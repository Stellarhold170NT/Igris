"""MongoDB Collection Stats Tool."""

from typing import Any

from app.integrations.mongodb import (
    build_mongodb_config,
    get_collection_stats,
    mongodb_database_is_available,
    mongodb_extract_params,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_collection_stats",
    description="Retrieve document counts, size metrics, and index information for a specific MongoDB collection.",
    source="mongodb",
    surfaces=("investigation", "chat"),
    is_available=mongodb_database_is_available,
    extract_params=mongodb_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "The specific database name containing the collection.",
            },
            "collection": {
                "type": "string",
                "description": "The collection name to retrieve statistics for.",
            },
        },
        "required": ["database", "collection"],
    },
)
def get_mongodb_collection_stats(
    connection_string: str = "",
    database: str = "",
    collection: str = "",
    auth_source: str = "admin",
    tls: bool = True,
) -> dict[str, Any]:
    """Fetch collection-level metrics (e.g. document count, index size) for a specific collection."""
    config = build_mongodb_config(
        {
            "connection_string": connection_string,
            "database": database,
            "auth_source": auth_source,
            "tls": tls,
        }
    )
    return get_collection_stats(config, collection=collection)
