"""Tests for KafkaTopicConsumersTool (function-based, @tool decorated).

Covers:
- BaseToolContract: name, description, input_schema, source metadata
- is_available: True / False / absent-key / falsy-verified
- extract_params: all connection fields forwarded correctly
- run happy path: active topic consumers
- run error path: integration returns available=False
- run not-configured: empty bootstrap_servers short-circuits before any broker contact
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.tools.KafkaTopicConsumersTool import get_kafka_topic_consumers
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

_TOPIC_CONSUMERS_RESPONSE = {
    "source": "kafka",
    "available": True,
    "topic": "vauthz.project.event",
    "consumers": [
        {
            "group_id": "vauthz-sync",
            "state": "STABLE",
            "topic_lag": 112,
            "partitions": [
                {
                    "partition": 0,
                    "committed_offset": 523,
                    "high_watermark": 635,
                    "lag": 112,
                    "consumer_id": "consumer-1",
                    "host": "/10.0.0.11",
                }
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestKafkaTopicConsumersToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_topic_consumers.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_kafka_topic_consumers.__opensre_registered_tool__
    assert rt.name == "get_kafka_topic_consumers"
    assert rt.source == "kafka"
    assert "investigation" in rt.surfaces
    assert "chat" in rt.surfaces


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestKafkaTopicConsumersIsAvailable:
    def _rt(self):
        return get_kafka_topic_consumers.__opensre_registered_tool__

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


class TestKafkaTopicConsumersExtractParams:
    def _rt(self):
        return get_kafka_topic_consumers.__opensre_registered_tool__

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
        assert params["security_protocol"] == "PLAINTEXT"
        assert params["sasl_mechanism"] == ""
        assert params["sasl_username"] == ""
        assert params["sasl_password"] == ""

    def test_strips_whitespace_from_bootstrap_servers(self) -> None:
        sources = {
            "kafka": {
                "bootstrap_servers": "   broker-with-spaces:9092   ",
                "connection_verified": True,
            }
        }
        params = self._rt().extract_params(sources)
        assert params["bootstrap_servers"] == "broker-with-spaces:9092"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestKafkaTopicConsumersRun:
    def test_happy_path_returns_topic_consumers(self) -> None:
        with patch(
            "app.tools.KafkaTopicConsumersTool.get_topic_consumers",
            return_value=_TOPIC_CONSUMERS_RESPONSE,
        ) as mock_get:
            result = get_kafka_topic_consumers(
                bootstrap_servers="broker1:9092",
                topic="vauthz.project.event",
            )

        mock_get.assert_called_once()
        config_arg = mock_get.call_args[0][0]
        assert config_arg.bootstrap_servers == "broker1:9092"
        assert mock_get.call_args[1]["topic"] == "vauthz.project.event"

        assert result["available"] is True
        assert result["topic"] == "vauthz.project.event"
        assert len(result["consumers"]) == 1
        cg = result["consumers"][0]
        assert cg["group_id"] == "vauthz-sync"
        assert cg["state"] == "STABLE"
        assert cg["topic_lag"] == 112
        assert len(cg["partitions"]) == 1
        p = cg["partitions"][0]
        assert p["partition"] == 0
        assert p["lag"] == 112
        assert p["consumer_id"] == "consumer-1"
        assert p["host"] == "/10.0.0.11"

    def test_error_path_returns_unavailable_dict(self) -> None:
        with patch(
            "app.tools.KafkaTopicConsumersTool.get_topic_consumers",
            return_value={"source": "kafka", "available": False, "error": "Connection timeout"},
        ):
            result = get_kafka_topic_consumers(
                bootstrap_servers="broker1:9092",
                topic="vauthz.project.event",
            )

        assert result["available"] is False
        assert result["error"] == "Connection timeout"

    def test_error_path_propagates_exception_from_integration(self) -> None:
        with patch(
            "app.tools.KafkaTopicConsumersTool.get_topic_consumers",
            side_effect=ValueError("Unexpected library state"),
        ):
            with pytest.raises(ValueError, match="Unexpected library state"):
                get_kafka_topic_consumers(
                    bootstrap_servers="broker1:9092",
                    topic="vauthz.project.event",
                )

    def test_not_configured_returns_unavailable_without_broker_contact(self) -> None:
        with patch(
            "app.tools.KafkaTopicConsumersTool.get_topic_consumers",
            return_value={"source": "kafka", "available": False, "error": "Not configured."},
        ) as mock_get:
            result = get_kafka_topic_consumers(
                bootstrap_servers="",
                topic="vauthz.project.event",
            )

        mock_get.assert_called_once()
        assert result["available"] is False
        assert "not configured" in result["error"].lower()
