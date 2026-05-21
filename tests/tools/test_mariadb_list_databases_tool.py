"""Tests for MariaDBListDatabasesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MariaDBListDatabasesTool import list_mariadb_databases
from tests.tools.conftest import BaseToolContract


class TestMariaDBListDatabasesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_mariadb_databases.__opensre_registered_tool__


def test_metadata() -> None:
    rt = list_mariadb_databases.__opensre_registered_tool__
    assert rt.name == "list_mariadb_databases"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "databases": ["auth", "portal", "mydb"],
    }
    with patch("app.tools.MariaDBListDatabasesTool.get_databases", return_value=fake_result):
        result = list_mariadb_databases(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert result["databases"] == ["auth", "portal", "mydb"]


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MariaDBListDatabasesTool.get_databases",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = list_mariadb_databases(host="invalid", database="test", username="user")
    assert "error" in result
