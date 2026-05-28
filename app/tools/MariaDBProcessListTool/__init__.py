"""MariaDB Process List Tool."""

from typing import Any

from app.integrations.mariadb import (
    MariaDBConfig,
    get_process_list,
    mariadb_extract_params,
    mariadb_is_available,
    resolve_mariadb_config,
)
from app.tools.tool_decorator import tool
from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


@tool(
    name="get_mariadb_process_list",
    description=(
        "Retrieve active MariaDB threads and queries from"
        " information_schema.PROCESSLIST, excluding idle connections."
    ),
    source="mariadb",
    surfaces=("investigation", "chat"),
    is_available=mariadb_is_available,
    extract_params=mariadb_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "description": "The specific database name to check active processes for.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of process records to return.",
                "default": 50,
            },
        },
        "required": ["database"],
    },
)
def get_mariadb_process_list(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
    instance: str | None = None,
) -> dict[str, Any]:
    """Fetch active threads from information_schema.PROCESSLIST.

    Args:
        host: Target host.
        username: Username.
        database: Optional target database.
        password: Password.
        port: Port.
        ssl: SSL enabled flag.
        max_results: Maximum results to retrieve.
        instance: Optional name of the configured MariaDB instance to target.
    """

    def mariadb_config_builder(database: str | None) -> MariaDBConfig:
        config = resolve_mariadb_config(
            host=host,
            database=database,
            username=username,
            password=password,
            port=port,
            ssl=ssl,
            instance=instance,
        )
        config.max_results = max_results
        return config

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_process_list,
    )
