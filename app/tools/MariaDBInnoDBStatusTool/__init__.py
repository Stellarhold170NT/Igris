"""MariaDB InnoDB Status Tool."""

from typing import Any

from app.integrations.mariadb import (
    get_innodb_status,
    mariadb_extract_params,
    mariadb_is_available,
    resolve_mariadb_config,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mariadb_innodb_status",
    description="Retrieve InnoDB engine internals including deadlocks, buffer pool state, and I/O activity from SHOW ENGINE INNODB STATUS.",
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
def get_mariadb_innodb_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    instance: str | None = None,
) -> dict[str, Any]:
    """Fetch InnoDB engine status.

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

    result = get_innodb_status(config)
    if _db_defaulted:
        result["default_db_warning"] = (
            "WARNING: No database was specified; defaulted to 'mysql'. Results may not reflect application data."
        )
    return result
