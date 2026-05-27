"""Tests for Grafana Dashboard integration tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GrafanaGetDashboardFiltersTool import (
    get_grafana_dashboard_filters,
    parse_grafana_variable_query,
    resolve_dependent_variables,
)
from app.tools.GrafanaGetDashboardValuesTool import (
    format_value,
    get_grafana_dashboard_values,
    parse_time_range,
    substitute_query_variables,
)
from app.tools.GrafanaListDashboardsTool import list_grafana_dashboards


def test_parse_grafana_variable_query() -> None:
    # label_values without metric
    q_type, label, metric = parse_grafana_variable_query("label_values(instance)")
    assert q_type == "label_values"
    assert label == "instance"
    assert metric is None

    # label_values with metric name
    q_type, label, metric = parse_grafana_variable_query("label_values(mysql_up, instance)")
    assert q_type == "label_values"
    assert label == "instance"
    assert metric == "mysql_up"

    # label_values with complex selector metric
    q_type, label, metric = parse_grafana_variable_query("label_values(mysql_up{job=\"mysql\"}, instance)")
    assert q_type == "label_values"
    assert label == "instance"
    assert metric == 'mysql_up{job="mysql"}'

    # query_result
    q_type, label, metric = parse_grafana_variable_query("query_result(mysql_up)")
    assert q_type == "query_result"
    assert label is None
    assert metric == "mysql_up"

    # label_values with multiple commas inside curly braces selector
    q_type, label, metric = parse_grafana_variable_query('label_values(node_uname_info{job="mysql", env="prod"}, instance)')
    assert q_type == "label_values"
    assert label == "instance"
    assert metric == 'node_uname_info{job="mysql", env="prod"}'

    # Raw fallback
    q_type, label, metric = parse_grafana_variable_query("just_a_word")
    assert q_type == "raw"
    assert label is None
    assert metric == "just_a_word"


def test_resolve_dependent_variables() -> None:
    current = {"cluster": "prod", "env": "live", "hosts": ["maria01", "maria02"], "instance": "$__all"}

    # Standard $ replacement
    query = 'mysql_up{cluster="$cluster", env="${env}"}'
    resolved = resolve_dependent_variables(query, current)
    assert resolved == 'mysql_up{cluster="prod", env="live"}'

    # Format modifiers replacement (e.g. ${cluster:regex} or ${cluster:pipe})
    query = 'mysql_up{cluster="${cluster:regex}", env="${env:pipe}"}'
    resolved = resolve_dependent_variables(query, current)
    assert resolved == 'mysql_up{cluster="prod", env="live"}'

    # List values replacement
    query = 'mysql_up{host=~"${hosts:regex}"}'
    resolved = resolve_dependent_variables(query, current)
    assert resolved == 'mysql_up{host=~"maria01|maria02"}'

    # $__all or All replacement
    query = 'mysql_up{instance=~"${instance}"}'
    resolved = resolve_dependent_variables(query, current)
    assert resolved == 'mysql_up{instance=~".*"}'

    # Unresolved fallback
    query = 'mysql_up{cluster="$cluster", host="$host"}'
    resolved = resolve_dependent_variables(query, current)
    assert resolved == 'mysql_up{cluster="prod", host=".*"}'


def test_parse_time_range() -> None:
    start, end = parse_time_range("30m")
    assert end - start == 30 * 60

    start, end = parse_time_range("6h")
    assert end - start == 6 * 3600

    start, end = parse_time_range("invalid")
    assert end - start == 3600  # defaults to 1h


def test_format_value() -> None:
    assert format_value(50.25, "percent") == "50.25%"
    assert format_value(1073741824 * 2.5, "bytes") == "2.50 GiB"
    assert format_value(1024 * 5, "bytes/sec") == "5.00 kB/s"
    assert format_value(12.34, "ms") == "12.34 ms"
    assert format_value(42.0, None) == "42.00"
    assert format_value(30.71, "short") == "30.71"
    assert format_value(1500.5, "short") == "1.50 K"
    assert format_value(2500000.0, "none") == "2.50 M"


def test_list_grafana_dashboards_mock() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.search_dashboards.return_value = [
        {"uid": "db1", "title": "MySQL", "folder_title": "Database", "url": "/d/db1"}
    ]

    with patch("app.tools.GrafanaListDashboardsTool._resolve_grafana_client", return_value=mock_client):
        res = list_grafana_dashboards(query="mysql", grafana_endpoint="http://grafana")

    assert res["available"] is True
    assert len(res["dashboards"]) == 1
    assert res["dashboards"][0]["uid"] == "db1"


def test_get_grafana_dashboard_filters_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.get_dashboard.return_value = {
        "dashboard": {
            "title": "MySQL Dashboard",
            "templating": {
                "list": [
                    {
                        "name": "ds_prometheus",
                        "type": "datasource",
                        "query": "prometheus",
                        "current": {"value": ""},
                    },
                    {
                        "name": "cluster",
                        "type": "custom",
                        "options": [{"value": "prod"}, {"value": "staging"}],
                        "current": {"value": "prod"},
                    },
                    {
                        "name": "host",
                        "type": "query",
                        "query": "label_values(mysql_up, instance)",
                        "current": {"value": "maria01"},
                    },
                ]
            },
        }
    }
    mock_client.get_datasources.return_value = [
        {"name": "mimir", "type": "prometheus", "uid": "cf8qy"},
        {"name": "loki", "type": "loki", "uid": "loki1"},
    ]
    mock_client.query_prometheus_label_values.return_value = ["maria01", "maria02"]

    with patch("app.tools.GrafanaGetDashboardFiltersTool._resolve_grafana_client", return_value=mock_client):
        res = get_grafana_dashboard_filters(dashboard_uid="db1", grafana_endpoint="http://grafana")

    assert res["available"] is True
    assert res["dashboard_title"] == "MySQL Dashboard"
    assert len(res["filters"]) == 3
    assert res["filters"][0]["name"] == "ds_prometheus"
    assert res["filters"][0]["options"] == ["mimir"]
    assert res["filters"][0]["current_value"] == "mimir"
    assert res["filters"][1]["name"] == "cluster"
    assert res["filters"][1]["options"] == ["prod", "staging"]
    assert res["filters"][2]["name"] == "host"
    assert res["filters"][2]["options"] == ["maria01", "maria02"]


def test_get_grafana_dashboard_values_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.get_dashboard.return_value = {
        "dashboard": {
            "title": "MySQL Dashboard",
            "panels": [
                {
                    "title": "MySQL Questions",
                    "fieldConfig": {"defaults": {"unit": "requests/sec"}},
                    "targets": [{"expr": "rate(mysql_questions[$__interval])", "refId": "A"}],
                }
            ],
        }
    }
    mock_client.query_mimir_range.return_value = {
        "success": True,
        "metrics": [
            {
                "metric": {"__name__": "mysql_questions", "instance": "maria01"},
                "values": [[1620000000, "15.4"], [1620000060, "384.0"], [1620000120, "27.6"]],
            }
        ],
    }

    with patch("app.tools.GrafanaGetDashboardValuesTool._resolve_grafana_client", return_value=mock_client):
        res = get_grafana_dashboard_values(
            dashboard_uid="db1", time_range="1h", variables={"host": "maria01"}, grafana_endpoint="http://grafana"
        )

    assert res["available"] is True
    assert res["dashboard_title"] == "MySQL Dashboard"
    assert len(res["panels"]) == 1
    panel = res["panels"][0]
    assert panel["panel_title"] == "MySQL Questions"
    assert len(panel["metrics"]) == 1
    metric = panel["metrics"][0]
    assert metric["name"] == "mysql_questions{instance=maria01}"
    assert "384.00" in metric["max"]
    assert "15.40" in metric["min"]
    assert "27.60" in metric["current"]


def test_substitute_query_variables() -> None:
    variables = {"host": "maria01", "cluster": "All"}
    defaults = {"host": "maria02", "port": "3306"}

    query = 'mysql_up{host="${host:regex}", cluster="$cluster", port="$port"}'
    resolved = substitute_query_variables(query, variables, defaults)
    assert resolved == 'mysql_up{host="maria01", cluster=".*", port="3306"}'


def test_get_grafana_dashboard_values_with_blank_defaults() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.get_dashboard.return_value = {
        "dashboard": {
            "title": "MySQL Dashboard",
            "templating": {
                "list": [
                    {
                        "name": "host",
                        "type": "query",
                        "query": "label_values(mysql_up, instance)",
                        "current": {"value": ""},
                    }
                ]
            },
            "panels": [
                {
                    "title": "MySQL Questions",
                    "targets": [{"expr": "rate(mysql_questions{instance=~\"$host\"}[$__interval])", "refId": "A"}],
                }
            ],
        }
    }
    mock_client.query_prometheus_label_values.return_value = ["maria01", "maria02"]
    mock_client.query_mimir_range.return_value = {
        "success": True,
        "metrics": [
            {
                "metric": {"__name__": "mysql_questions", "instance": "maria01"},
                "values": [[1620000000, "10.0"]],
            }
        ],
    }

    with patch("app.tools.GrafanaGetDashboardValuesTool._resolve_grafana_client", return_value=mock_client):
        res = get_grafana_dashboard_values(
            dashboard_uid="db1", time_range="1h", variables={}, grafana_endpoint="http://grafana"
        )

    assert res["available"] is True
    # The default variables resolution should have queried label values and selected the first option 'maria01'
    mock_client.query_prometheus_label_values.assert_called_once_with("instance", match="mysql_up")
    # Verify that query range was called with the resolved host 'maria01'
    called_expr = mock_client.query_mimir_range.call_args[0][0]
    assert 'instance=~"maria01"' in called_expr


def test_get_grafana_dashboard_filters_cascading() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.get_dashboard.return_value = {
        "dashboard": {
            "title": "Cascading Dashboard",
            "templating": {
                "list": [
                    {
                        "name": "job",
                        "type": "query",
                        "query": "label_values(up, job)",
                        "current": {"value": ""},
                    },
                    {
                        "name": "node",
                        "type": "query",
                        "query": "label_values(node_uname_info{job=\"$job\"}, instance)",
                        "current": {"value": ""},
                    }
                ]
            },
        }
    }

    # First call: query jobs
    # Second call: query instances with matched job
    def mock_query_label_values(label: str, match: str | None = None) -> list[str]:
        if label == "job":
            return ["integrations/unix", "kubernetes"]
        if label == "instance":
            if match == 'node_uname_info{job="integrations/unix"}':
                return ["maria01", "maria02"]
            return ["k8s-node1"]
        return []

    mock_client.query_prometheus_label_values.side_effect = mock_query_label_values

    with patch("app.tools.GrafanaGetDashboardFiltersTool._resolve_grafana_client", return_value=mock_client):
        res = get_grafana_dashboard_filters(dashboard_uid="db_cascade", grafana_endpoint="http://grafana")

    assert res["available"] is True
    filters = res["filters"]
    assert len(filters) == 2

    # job variable should have been updated with the first option
    assert filters[0]["name"] == "job"
    assert filters[0]["current_value"] == "integrations/unix"
    assert filters[0]["options"] == ["integrations/unix", "kubernetes"]

    # node variable should have been queried using the resolved job integrations/unix
    assert filters[1]["name"] == "node"
    assert filters[1]["current_value"] == "maria01"
    assert filters[1]["options"] == ["maria01", "maria02"]



