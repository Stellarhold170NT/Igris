from __future__ import annotations

import pytest

from app.coral_api import CoralApiRegistry, CoralColumn, CoralFilter, coralapi
from app.coral_api.manifest import generate_bridge_manifest
from app.coral_api.source_mapping import get_enabled_sources


@pytest.fixture(autouse=True)
def clear_registry():
    CoralApiRegistry._functions.clear()
    yield
    CoralApiRegistry._functions.clear()


class TestCoralapiDecorator:
    def test_register_function(self):
        @coralapi(
            name="test_pods",
            description="Test pods",
            columns={"name": "Utf8", "restarts": CoralColumn("Int64")},
            filters={"cluster": CoralFilter(required=True)},
        )
        def test_pods(cluster: str) -> list[dict]:
            return [{"name": "pod-1", "restarts": 0}]

        fn = CoralApiRegistry.get("test_pods")
        assert fn is not None
        assert fn.name == "test_pods"
        assert fn.description == "Test pods"
        assert fn.columns["name"].type == "Utf8"
        assert fn.columns["restarts"].type == "Int64"
        assert fn.filters["cluster"].required is True
        assert fn.func(cluster="prod") == [{"name": "pod-1", "restarts": 0}]

    def test_column_shorthand(self):
        @coralapi(
            name="shorthand",
            description="Test",
            columns={"id": "Int64"},
        )
        def shorthand() -> list[dict]:
            return []

        fn = CoralApiRegistry.get("shorthand")
        assert fn.columns["id"].type == "Int64"
        assert fn.columns["id"].nullable is True

    def test_filter_shorthand(self):
        @coralapi(
            name="filter_test",
            description="Test",
            columns={"id": "Int64"},
            filters={"ns": "Namespace"},
        )
        def filter_test() -> list[dict]:
            return []

        fn = CoralApiRegistry.get("filter_test")
        assert fn.filters["ns"].description == "Namespace"
        assert fn.filters["ns"].required is False

    def test_is_available(self):
        @coralapi(
            name="conditional",
            description="Test",
            columns={"id": "Int64"},
            is_available=lambda resolved: "eks" in resolved,
        )
        def conditional() -> list[dict]:
            return []

        assert "conditional" in CoralApiRegistry.available({"eks": {}})
        assert "conditional" not in CoralApiRegistry.available({})


class TestManifestGeneration:
    def test_generate_bridge_manifest(self):
        @coralapi(
            name="manifest_test",
            description="Manifest test",
            columns={"id": CoralColumn("Int64"), "name": CoralColumn("Utf8")},
            filters={"cluster": CoralFilter(required=True)},
        )
        def manifest_test() -> list[dict]:
            return []

        fn = CoralApiRegistry.get("manifest_test")
        yaml_str = generate_bridge_manifest(fn, "http://localhost:9999")

        assert "dsl_version: 3" in yaml_str
        assert "backend: http" in yaml_str
        assert "manifest_test" in yaml_str
        assert "http://localhost:9999/coral-api/manifest_test" in yaml_str
        assert "id" in yaml_str
        assert "Int64" in yaml_str
        assert "cluster" in yaml_str
        assert "required: true" in yaml_str


class TestSourceMapping:
    def test_get_enabled_sources_explicit_env(self, monkeypatch):
        monkeypatch.setenv("CORAL_GRAFANA", "true")
        enabled = get_enabled_sources(
            {"grafana": {"credentials": {"endpoint": "http://g", "api_key": "k"}}}
        )
        assert "grafana" in enabled

    def test_get_enabled_sources_auto_enable(self, monkeypatch):
        monkeypatch.setenv("CORAL_AUTO_ENABLE", "true")
        enabled = get_enabled_sources(
            {"datadog": {"credentials": {"api_key": "k", "app_key": "a"}}}
        )
        assert "datadog" in enabled

    def test_get_enabled_sources_missing_creds(self, monkeypatch):
        monkeypatch.setenv("CORAL_AUTO_ENABLE", "true")
        enabled = get_enabled_sources({"grafana": {"credentials": {}}})
        assert "grafana" not in enabled

    def test_get_enabled_sources_no_env(self):
        enabled = get_enabled_sources({})
        assert enabled == {}
