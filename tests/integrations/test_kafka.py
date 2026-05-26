"""Unit tests for the Kafka integration module."""

from app.integrations.kafka import (
    KafkaConfig,
    KafkaValidationResult,
    build_kafka_config,
    kafka_config_from_env,
)


class TestKafkaConfig:
    """Tests for KafkaConfig model."""

    def test_defaults(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092")
        assert config.bootstrap_servers == "localhost:9092"
        assert config.security_protocol == "PLAINTEXT"
        assert config.sasl_mechanism == ""
        assert config.sasl_username == ""
        assert config.sasl_password == ""
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_servers(self) -> None:
        config = KafkaConfig(bootstrap_servers="broker1:9092,broker2:9092")
        assert config.is_configured is True

    def test_is_configured_without_servers(self) -> None:
        config = KafkaConfig()
        assert config.is_configured is False

    def test_normalize_bootstrap_servers_strips_whitespace(self) -> None:
        config = KafkaConfig(bootstrap_servers="  broker:9092  ")
        assert config.bootstrap_servers == "broker:9092"

    def test_normalize_security_protocol_uppercase(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092", security_protocol="sasl_ssl")
        assert config.security_protocol == "SASL_SSL"

    def test_normalize_empty_security_protocol_uses_default(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092", security_protocol="")
        assert config.security_protocol == "PLAINTEXT"

    def test_sasl_config(self) -> None:
        config = KafkaConfig(
            bootstrap_servers="broker:9092",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="user",
            sasl_password="pass",
        )
        assert config.sasl_mechanism == "PLAIN"
        assert config.sasl_username == "user"
        assert config.sasl_password == "pass"


class TestBuildKafkaConfig:
    """Tests for build_kafka_config helper."""

    def test_from_dict(self) -> None:
        config = build_kafka_config({"bootstrap_servers": "broker:9092"})
        assert config.bootstrap_servers == "broker:9092"
        assert config.is_configured is True

    def test_from_none(self) -> None:
        config = build_kafka_config(None)
        assert config.bootstrap_servers == ""
        assert config.is_configured is False


class TestKafkaConfigFromEnv:
    """Tests for kafka_config_from_env helper."""

    def test_returns_none_without_servers(self) -> None:
        import os

        old = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
        os.environ.pop("KAFKA_BOOTSTRAP_SERVERS", None)
        try:
            result = kafka_config_from_env()
            assert result is None
        finally:
            if old is not None:
                os.environ["KAFKA_BOOTSTRAP_SERVERS"] = old

    def test_returns_config_with_servers(self) -> None:
        import os

        os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "broker1:9092,broker2:9092"
        os.environ["KAFKA_SECURITY_PROTOCOL"] = "SASL_SSL"
        os.environ["KAFKA_SASL_MECHANISM"] = "PLAIN"
        os.environ["KAFKA_SASL_USERNAME"] = "testuser"
        os.environ["KAFKA_SASL_PASSWORD"] = "testpass"
        try:
            config = kafka_config_from_env()
            assert config is not None
            assert config.bootstrap_servers == "broker1:9092,broker2:9092"
            assert config.security_protocol == "SASL_SSL"
            assert config.sasl_mechanism == "PLAIN"
            assert config.sasl_username == "testuser"
            assert config.sasl_password == "testpass"
        finally:
            for key in [
                "KAFKA_BOOTSTRAP_SERVERS",
                "KAFKA_SECURITY_PROTOCOL",
                "KAFKA_SASL_MECHANISM",
                "KAFKA_SASL_USERNAME",
                "KAFKA_SASL_PASSWORD",
            ]:
                os.environ.pop(key, None)


class TestKafkaValidationResult:
    """Tests for KafkaValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = KafkaValidationResult(ok=True, detail="Connected.")
        assert result.ok is True

    def test_error_result(self) -> None:
        result = KafkaValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False


def test_load_env_integrations_loads_kafka() -> None:
    import os
    from app.integrations._catalog_impl import load_env_integrations

    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    os.environ["KAFKA_SECURITY_PROTOCOL"] = "PLAINTEXT"
    try:
        results = load_env_integrations()
        kafka_entries = [r for r in results if r.get("service") == "kafka"]
        assert len(kafka_entries) == 1
        assert kafka_entries[0]["credentials"]["bootstrap_servers"] == "localhost:9092"
        assert kafka_entries[0]["credentials"]["security_protocol"] == "PLAINTEXT"
    finally:
        os.environ.pop("KAFKA_BOOTSTRAP_SERVERS", None)
        os.environ.pop("KAFKA_SECURITY_PROTOCOL", None)


class TestGetConsumerGroupLagKeywordMatching:
    """Tests for consumer group keyword/substring matching logic."""

    def test_multiple_matches(self) -> None:
        from unittest.mock import MagicMock, patch
        from app.integrations.kafka import get_consumer_group_lag

        config = KafkaConfig(bootstrap_servers="localhost:9092")
        mock_admin = MagicMock()

        # Mock list_consumer_groups to return a list containing multiple matches
        mock_future = MagicMock()
        mock_listing_1 = MagicMock()
        mock_listing_1.group_id = "vauthz-sync"
        mock_listing_2 = MagicMock()
        mock_listing_2.group_id = "debezium-vauthz"
        mock_listing_3 = MagicMock()
        mock_listing_3.group_id = "other-group"

        mock_res = MagicMock()
        mock_res.valid = [mock_listing_1, mock_listing_2, mock_listing_3]
        mock_future.result.return_value = mock_res
        mock_admin.list_consumer_groups.return_value = mock_future

        mock_consumer = MagicMock()

        with (
            patch("app.integrations.kafka._get_admin_client", return_value=mock_admin),
            patch("app.integrations.kafka._get_consumer", return_value=mock_consumer),
        ):
            res = get_consumer_group_lag(config, "vauthz")

        assert res["available"] is True
        assert res["multiple_matches"] is True
        assert res["matched_groups"] == ["debezium-vauthz", "vauthz-sync"]
        assert "Multiple consumer groups matched" in res["error"]

    def test_single_match(self) -> None:
        from unittest.mock import MagicMock, patch
        from app.integrations.kafka import get_consumer_group_lag

        config = KafkaConfig(bootstrap_servers="localhost:9092")
        mock_admin = MagicMock()

        mock_future = MagicMock()
        mock_listing_1 = MagicMock()
        mock_listing_1.group_id = "vauthz-sync"
        mock_listing_2 = MagicMock()
        mock_listing_2.group_id = "other-group"

        mock_res = MagicMock()
        mock_res.valid = [mock_listing_1, mock_listing_2]
        mock_future.result.return_value = mock_res
        mock_admin.list_consumer_groups.return_value = mock_future

        # Mock list_consumer_group_offsets to return a future
        mock_offsets_future = MagicMock()
        mock_offsets_res = MagicMock()
        mock_admin.list_consumer_group_offsets.return_value = {"group": mock_offsets_future}

        mock_tp = MagicMock()
        mock_tp.topic = "vauthz-topic"
        mock_tp.partition = 0
        mock_tp.offset = 100
        mock_tp.error = None

        mock_offsets_res.topic_partitions = [mock_tp]
        mock_offsets_future.result.return_value = mock_offsets_res

        mock_consumer = MagicMock()
        mock_consumer.get_watermark_offsets.return_value = (0, 150)

        with (
            patch("app.integrations.kafka._get_admin_client", return_value=mock_admin),
            patch("app.integrations.kafka._get_consumer", return_value=mock_consumer),
        ):
            res = get_consumer_group_lag(config, "vauthz")

        assert res["available"] is True
        assert res["group_id"] == "vauthz-sync"
        assert res["total_lag"] == 50
        assert len(res["partitions"]) == 1
        assert res["partitions"][0]["lag"] == 50

