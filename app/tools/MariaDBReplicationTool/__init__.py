"""MariaDB Replication Status Tool."""

from typing import Any

from app.integrations.mariadb import (
    get_replication_status,
    mariadb_extract_params,
    mariadb_is_available,
    resolve_mariadb_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_replication_status",
    description="Retrieve MariaDB replication status including I/O and SQL thread state, lag, and errors from SHOW ALL SLAVES STATUS.",
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "Optional name of the target database.",
            },
        },
    },
)
def get_mariadb_replication_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    instance: str | None = None,
) -> dict[str, Any]:
    """Fetch replication status from SHOW ALL SLAVES STATUS.

    Args:
        host: Target host.
        username: Username.
        database: Optional target database.
        password: Password.
        port: Port.
        ssl: SSL enabled flag.
        instance: Optional name of the configured MariaDB instance to target.
    """
    config = resolve_mariadb_config(
        host=host,
        database=database,
        username=username,
        password=password,
        port=port,
        ssl=ssl,
        instance=instance,
    )
    _db_defaulted = not config.database
    if not config.database:
        config.database = "mysql"

    result = get_replication_status(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
        )
    return result
