from __future__ import annotations

import json

import pytest

from app.integrations.mariadb import resolve_mariadb_config


def test_resolve_mariadb_config_fallback_no_env_or_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.integrations.store.load_integrations", lambda: [])
    monkeypatch.setattr("app.integrations.catalog.load_env_integrations", lambda: [])

    # If there are no configured integrations, it should return the parameters passed directly
    config = resolve_mariadb_config(
        host="direct-host",
        database="direct-db",
        username="direct-user",
        password="direct-password",
        port=3307,
        ssl=False,
    )
    assert config.host == "direct-host"
    assert config.database == "direct-db"
    assert config.username == "direct-user"
    assert config.password == "direct-password"
    assert config.port == 3307
    assert config.ssl is False


def test_resolve_mariadb_config_from_instances_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.integrations.store.load_integrations", lambda: [])

    instances = [
        {
            "name": "finance",
            "host": "10.0.0.110",
            "port": 3306,
            "username": "user-finance",
            "password": "pass-finance",
            "database": "db_finance",
            "ssl": True,
        },
        {
            "name": "inventory",
            "host": "10.0.0.111",
            "port": 3308,
            "username": "user-inventory",
            "password": "pass-inventory",
            "database": "db_inventory",
            "ssl": False,
        },
    ]
    monkeypatch.setenv("MARIADB_INSTANCES", json.dumps(instances))

    # Test resolving by instance name (case-insensitive)
    config = resolve_mariadb_config(instance="INVENTORY")
    assert config.host == "10.0.0.111"
    assert config.database == "db_inventory"
    assert config.username == "user-inventory"
    assert config.password == "pass-inventory"
    assert config.port == 3308
    assert config.ssl is False

    # Test resolving implicitly by database name
    config = resolve_mariadb_config(database="db_finance")
    assert config.host == "10.0.0.110"
    assert config.database == "db_finance"
    assert config.username == "user-finance"
    assert config.password == "pass-finance"
    assert config.port == 3306
    assert config.ssl is True

    # Test fallback to first instance when no matches found
    config = resolve_mariadb_config()
    assert config.host == "10.0.0.110"
    assert config.database == "db_finance"
