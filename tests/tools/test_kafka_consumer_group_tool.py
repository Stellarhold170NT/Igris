"""Tests for KafkaConsumerGroupTool (function-based, @tool decorated).

Covers:
- BaseToolContract: name, description, input_schema, source metadata
- is_available: True / False / absent-key / falsy-verified
- extract_params: all connection fields forwarded correctly
- run happy path: active consumer lag, zero-lag (healthy) group
- run error path: integration returns available=False
- run not-configured: empty bootstrap_servers short-circuits before any broker contact
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.tools.KafkaConsumerGroupTool import get_kafka_consumer_group
from tests.tools.conftest import BaseToolContract

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_KAFKA_SOURCES = {
    "kafka": {
        "connection_verified": True,
        "bootstrap_servers": "broker1:9092,broker2:9092",
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "PLAIN",
        "sasl_username": "alice",
        "sasl_password": "s3cr3t",
    }
}

_CONSUMER_GROUP_LAG_RESPONSE = {
    "source": "kafka",
    "available": True,
    "group_id": "payments-consumer",
    "state": "STABLE",
    "total_lag": 1500,
    "topics": {
        "payments": {
            "topic_lag": 1500,
            "partitions": [
                {
                    "partition": 0,
                    "committed_offset": 8500,
                    "high_watermark": 9200,
                    "lag": 700,
                    "consumer_id": "consumer-1",
                    "host": "/10.0.0.1",
                },
                {
                    "partition": 1,
                    "committed_offset": 7800,
                    "high_watermark": 8600,
                    "lag": 800,
                    "consumer_id": "consumer-2",
                    "host": "/10.0.0.2",
                },
            ],
        }
    },
}

_CONSUMER_GROUP_ZERO_LAG_RESPONSE = {
    "source": "kafka",
    "available": True,
    "group_id": "events-consumer",
    "state": "STABLE",
    "total_lag": 0,
    "topics": {
        "events": {
            "topic_lag": 0,
            "partitions": [
                {
                    "partition": 0,
                    "committed_offset": 5000,
                    "high_watermark": 5000,
                    "lag": 0,
                    "consumer_id": "consumer-3",
                    "host": "/10.0.0.3",
                },
            ],
        }
    },
}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestKafkaConsumerGroupToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_consumer_group.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_kafka_consumer_group.__opensre_registered_tool__
    assert rt.name == "get_kafka_consumer_group"
    assert rt.source == "kafka"
    assert "investigation" in rt.surfaces
    assert "chat" in rt.surfaces


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestKafkaConsumerGroupIsAvailable:
    def _rt(self):
        return get_kafka_consumer_group.__opensre_registered_tool__

    def test_true_when_connection_verified(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": True}}) is True

    def test_false_when_connection_verified_is_false(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": False}}) is False

    def test_false_when_kafka_key_absent(self) -> None:
        assert self._rt().is_available({}) is False

    def test_false_when_kafka_is_empty_dict(self) -> None:
        assert self._rt().is_available({"kafka": {}}) is False

    def test_false_when_connection_verified_is_none(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": None}}) is False


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------


class TestKafkaConsumerGroupExtractParams:
    def _rt(self):
        return get_kafka_consumer_group.__opensre_registered_tool__

    def test_extracts_all_connection_fields(self) -> None:
        params = self._rt().extract_params(_KAFKA_SOURCES)
        assert params["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert params["security_protocol"] == "SASL_SSL"
        assert params["sasl_mechanism"] == "PLAIN"
        assert params["sasl_username"] == "alice"
        assert params["sasl_password"] == "s3cr3t"

    def test_returns_empty_strings_when_kafka_absent(self) -> None:
        params = self._rt().extract_params({})
        assert params["bootstrap_servers"] == ""
        # security_protocol defaults to PLAINTEXT when absent
        assert params["security_protocol"] == "PLAINTEXT"
        assert params["sasl_mechanism"] == ""
        assert params["sasl_username"] == ""
        assert params["sasl_password"] == ""

    def test_strips_whitespace_from_bootstrap_servers(self) -> None:
        sources = {"kafka": {"connection_verified": True, "bootstrap_servers": "  broker:9092  "}}
        params = self._rt().extract_params(sources)
        assert params["bootstrap_servers"] == "broker:9092"


# ---------------------------------------------------------------------------
# run — happy paths
# ---------------------------------------------------------------------------


class TestKafkaConsumerGroupRun:
    def test_happy_path_returns_total_lag(self) -> None:
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            return_value=_CONSUMER_GROUP_LAG_RESPONSE,
        ):
            result = get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="payments-consumer",
            )

        assert result["available"] is True
        assert result["group_id"] == "payments-consumer"
        assert result["state"] == "STABLE"
        assert result["total_lag"] == 1500
        assert result["source"] == "kafka"

    def test_happy_path_partition_level_lag_detail(self) -> None:
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            return_value=_CONSUMER_GROUP_LAG_RESPONSE,
        ):
            result = get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="payments-consumer",
            )

        assert "payments" in result["topics"]
        t_data = result["topics"]["payments"]
        assert t_data["topic_lag"] == 1500
        assert len(t_data["partitions"]) == 2

        p0 = t_data["partitions"][0]
        assert p0["partition"] == 0
        assert p0["lag"] == 700
        assert p0["consumer_id"] == "consumer-1"
        assert p0["host"] == "/10.0.0.1"

        p1 = t_data["partitions"][1]
        assert p1["partition"] == 1
        assert p1["lag"] == 800
        assert p1["consumer_id"] == "consumer-2"
        assert p1["host"] == "/10.0.0.2"

    def test_happy_path_zero_lag_healthy_group(self) -> None:
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            return_value=_CONSUMER_GROUP_ZERO_LAG_RESPONSE,
        ):
            result = get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="events-consumer",
            )

        assert result["available"] is True
        assert result["total_lag"] == 0
        assert "events" in result["topics"]
        p0 = result["topics"]["events"]["partitions"][0]
        assert p0["lag"] == 0
        assert p0["committed_offset"] == p0["high_watermark"]

    def test_happy_path_forwards_group_id_to_integration(self) -> None:
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            return_value=_CONSUMER_GROUP_ZERO_LAG_RESPONSE,
        ) as mock_fn:
            get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="events-consumer",
            )

        _, call_kwargs = mock_fn.call_args
        assert call_kwargs.get("group_id") == "events-consumer"

    def test_happy_path_sasl_ssl_connection(self) -> None:
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            return_value=_CONSUMER_GROUP_LAG_RESPONSE,
        ) as mock_fn:
            result = get_kafka_consumer_group(
                bootstrap_servers="broker1:9093",
                group_id="payments-consumer",
                security_protocol="SASL_SSL",
                sasl_mechanism="PLAIN",
                sasl_username="alice",
                sasl_password="s3cr3t",
            )

        assert result["available"] is True
        # Verify SASL credentials were wired into the KafkaConfig forwarded to the integration.
        config_arg = mock_fn.call_args[0][0]
        assert config_arg.security_protocol == "SASL_SSL"
        assert config_arg.sasl_mechanism == "PLAIN"
        assert config_arg.sasl_username == "alice"
        assert config_arg.sasl_password == "s3cr3t"

    # ---------------------------------------------------------------------------
    # run — error / not-configured paths
    # ---------------------------------------------------------------------------

    def test_error_path_returns_unavailable_dict(self) -> None:
        fake_error = {
            "source": "kafka",
            "available": False,
            "error": "Group 'stale-consumer' does not exist.",
        }
        with patch("app.tools.KafkaConsumerGroupTool.get_consumer_group", return_value=fake_error):
            result = get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="stale-consumer",
            )

        assert result["available"] is False
        assert "error" in result
        assert result["source"] == "kafka"

    def test_error_path_propagates_exception_from_integration(self) -> None:
        # If the integration ever raises instead of returning an error dict,
        # the tool should let the exception propagate (no silent swallowing).
        with (
            patch(
                "app.tools.KafkaConsumerGroupTool.get_consumer_group",
                side_effect=RuntimeError("consumer group timeout"),
            ),
            pytest.raises(RuntimeError, match="consumer group timeout"),
        ):
            get_kafka_consumer_group(
                bootstrap_servers="broker1:9092",
                group_id="payments-consumer",
            )

    def test_not_configured_returns_unavailable_without_broker_contact(self) -> None:
        # Empty bootstrap_servers → KafkaConfig.is_configured is False.
        # The integration short-circuits before touching confluent_kafka.
        with patch(
            "app.tools.KafkaConsumerGroupTool.get_consumer_group",
            wraps=__import__(
                "app.integrations.kafka", fromlist=["get_consumer_group"]
            ).get_consumer_group,
        ) as mock_fn:
            result = get_kafka_consumer_group(
                bootstrap_servers="",
                group_id="payments-consumer",
            )

        assert result["available"] is False
        assert result["source"] == "kafka"
        # Confirm the integration was entered but short-circuited before broker contact.
        mock_fn.assert_called_once()
