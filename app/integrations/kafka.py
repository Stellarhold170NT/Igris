"""Shared Kafka integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Kafka clusters. All operations are read-only: topic metadata,
consumer group lag, and broker health. No produce or consume operations.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.integrations._validation_helpers import report_validation_failure
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_KAFKA_SECURITY_PROTOCOL = "PLAINTEXT"
DEFAULT_KAFKA_TIMEOUT_SECONDS = 10.0
DEFAULT_KAFKA_MAX_RESULTS = 50


class KafkaConfig(StrictConfigModel):
    """Normalized Kafka connection settings."""

    bootstrap_servers: str = ""
    security_protocol: str = DEFAULT_KAFKA_SECURITY_PROTOCOL
    sasl_mechanism: str = ""
    sasl_username: str = ""
    sasl_password: str = ""
    timeout_seconds: float = Field(default=DEFAULT_KAFKA_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_KAFKA_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("bootstrap_servers", mode="before")
    @classmethod
    def _normalize_bootstrap_servers(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("security_protocol", mode="before")
    @classmethod
    def _normalize_security_protocol(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_KAFKA_SECURITY_PROTOCOL).strip().upper()
        return normalized or DEFAULT_KAFKA_SECURITY_PROTOCOL

    @property
    def is_configured(self) -> bool:
        return bool(self.bootstrap_servers)


@dataclass(frozen=True)
class KafkaValidationResult:
    """Result of validating a Kafka integration."""

    ok: bool
    detail: str


def kafka_is_available(sources: dict[str, dict]) -> bool:
    """Check if Kafka integration params are present in available sources."""
    return bool(sources.get("kafka", {}).get("connection_verified"))


def kafka_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Kafka connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply bootstrap_servers or SASL credentials directly.
    """
    kf = sources.get("kafka", {})
    cred = kf.get("credentials", {})
    return {
        "bootstrap_servers": str(kf.get("bootstrap_servers") or cred.get("bootstrap_servers", "")).strip(),
        "security_protocol": str(
            kf.get("security_protocol")
            or cred.get("security_protocol")
            or DEFAULT_KAFKA_SECURITY_PROTOCOL
        ).strip(),
        "sasl_mechanism": str(kf.get("sasl_mechanism") or cred.get("sasl_mechanism", "")).strip(),
        "sasl_username": str(kf.get("sasl_username") or cred.get("sasl_username", "")).strip(),
        "sasl_password": str(kf.get("sasl_password") or cred.get("sasl_password", "")).strip(),
    }


def build_kafka_config(raw: dict[str, Any] | None) -> KafkaConfig:
    """Build a normalized Kafka config object from env/store data."""
    return KafkaConfig.model_validate(raw or {})


def kafka_config_from_env() -> KafkaConfig | None:
    """Load a Kafka config from env vars."""
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap_servers:
        return None
    return build_kafka_config(
        {
            "bootstrap_servers": bootstrap_servers,
            "security_protocol": os.getenv(
                "KAFKA_SECURITY_PROTOCOL", DEFAULT_KAFKA_SECURITY_PROTOCOL
            ).strip(),
            "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "").strip(),
            "sasl_username": os.getenv("KAFKA_SASL_USERNAME", "").strip(),
            "sasl_password": os.getenv("KAFKA_SASL_PASSWORD", "").strip(),
        }
    )


def _get_admin_client(config: KafkaConfig) -> Any:
    """Create a confluent_kafka AdminClient from config."""
    from confluent_kafka.admin import AdminClient

    conf: dict[str, Any] = {
        "bootstrap.servers": config.bootstrap_servers,
        "security.protocol": config.security_protocol,
        "socket.timeout.ms": int(config.timeout_seconds * 1000),
        "request.timeout.ms": int(config.timeout_seconds * 1000),
    }
    if config.sasl_mechanism:
        conf["sasl.mechanism"] = config.sasl_mechanism
    if config.sasl_username:
        conf["sasl.username"] = config.sasl_username
    if config.sasl_password:
        conf["sasl.password"] = config.sasl_password
    return AdminClient(conf)


def _get_consumer(config: KafkaConfig) -> Any:
    """Create a confluent_kafka Consumer for metadata queries."""
    from confluent_kafka import Consumer

    conf: dict[str, Any] = {
        "bootstrap.servers": config.bootstrap_servers,
        "security.protocol": config.security_protocol,
        "group.id": f"opensre-internal-{config.integration_id or 'readonly'}",
        "enable.auto.commit": False,
        "auto.offset.reset": "latest",
        "socket.timeout.ms": int(config.timeout_seconds * 1000),
    }
    if config.sasl_mechanism:
        conf["sasl.mechanism"] = config.sasl_mechanism
    if config.sasl_username:
        conf["sasl.username"] = config.sasl_username
    if config.sasl_password:
        conf["sasl.password"] = config.sasl_password
    return Consumer(conf)


def validate_kafka_config(config: KafkaConfig) -> KafkaValidationResult:
    """Validate Kafka connectivity by listing topics."""
    if not config.bootstrap_servers:
        return KafkaValidationResult(ok=False, detail="Kafka bootstrap_servers is required.")

    try:
        admin = _get_admin_client(config)
        metadata = admin.list_topics(timeout=config.timeout_seconds)
        topic_count = len(metadata.topics)
        broker_count = len(metadata.brokers)
        return KafkaValidationResult(
            ok=True,
            detail=(
                f"Connected to Kafka cluster with {broker_count} broker(s) "
                f"and {topic_count} topic(s)."
            ),
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="kafka",
            method="validate_kafka_config",
        )
        return KafkaValidationResult(ok=False, detail=f"Kafka connection failed: {err}")


def get_topic_health(
    config: KafkaConfig,
    topic: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Retrieve topic partition health: offsets, replicas, ISR status.

    Read-only: uses cluster metadata. If topic is None, returns stats for
    all topics up to max_results.
    """
    if not config.is_configured:
        return {"source": "kafka", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        admin = _get_admin_client(config)
        metadata = admin.list_topics(timeout=config.timeout_seconds)

        topics: list[dict[str, Any]] = []
        skipped = 0
        for tname, tmeta in sorted(metadata.topics.items()):
            if tname.startswith("__"):
                continue
            if topic and topic.lower() not in tname.lower():
                continue
            if skipped < offset:
                skipped += 1
                continue
            if len(topics) >= effective_limit:
                break
            partitions = []
            for pid, pmeta in tmeta.partitions.items():
                partitions.append(
                    {
                        "id": pid,
                        "leader": pmeta.leader,
                        "replicas": list(pmeta.replicas),
                        "isr": list(pmeta.isrs),
                        "under_replicated": len(pmeta.isrs) < len(pmeta.replicas),
                    }
                )
            topics.append(
                {
                    "name": tname,
                    "partition_count": len(tmeta.partitions),
                    "partitions": partitions,
                }
            )
        return {
            "source": "kafka",
            "available": True,
            "broker_count": len(metadata.brokers),
            "topics_returned": len(topics),
            "cluster_topic_count": len(metadata.topics),
            "topics": topics,
        }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="kafka",
            method="get_topic_health",
        )
        return {"source": "kafka", "available": False, "error": str(err)}


def get_consumer_group(
    config: KafkaConfig,
    group_id: str,
) -> dict[str, Any]:
    """Retrieve consumer group information, including lag, consumer id, host per partition.

    Read-only: queries committed offsets and compares to high watermarks.
    """
    if not config.is_configured:
        return {"source": "kafka", "available": False, "error": "Not configured."}

    try:
        from confluent_kafka import TopicPartition
        try:
            from confluent_kafka import ConsumerGroupTopicPartitions
        except ImportError:
            from confluent_kafka.admin import ConsumerGroupTopicPartitions

        admin = _get_admin_client(config)
        consumer = _get_consumer(config)

        try:
            # List all consumer groups to support case-insensitive substring search (keyword)
            target_group = group_id
            try:
                list_groups_future = admin.list_consumer_groups()
                list_groups_res = list_groups_future.result()
                all_groups = [g.group_id for g in list_groups_res.valid] if list_groups_res.valid else []
                if all_groups:
                    if group_id in all_groups:
                        target_group = group_id
                    else:
                        matches = [g for g in all_groups if group_id.lower() in g.lower()]
                        if len(matches) == 1:
                            target_group = matches[0]
                        elif len(matches) > 1:
                            return {
                                "source": "kafka",
                                "available": True,
                                "group_id": group_id,
                                "multiple_matches": True,
                                "matched_groups": sorted(matches),
                                "error": f"Multiple consumer groups matched keyword '{group_id}': {', '.join(sorted(matches))}. Please specify the exact group_id.",
                            }
                        else:
                            return {
                                "source": "kafka",
                                "available": True,
                                "group_id": group_id,
                                "multiple_matches": False,
                                "matched_groups": [],
                                "error": f"No consumer groups matched keyword '{group_id}'. Available groups: {', '.join(sorted(all_groups))}",
                            }
            except Exception as list_err:
                logger.warning("Failed to list consumer groups, using exact group_id: %s", list_err)

            # Call describe_consumer_groups to get consumer id and host per partition
            state = "UNKNOWN"
            assignment_map = {}
            try:
                describe_future = admin.describe_consumer_groups([target_group])
                describe_res = describe_future[target_group].result()
                state = str(describe_res.state) if describe_res.state else "UNKNOWN"
                for member in describe_res.members:
                    host = member.host
                    member_id = member.member_id
                    if member.assignment and member.assignment.topic_partitions:
                        for tp in member.assignment.topic_partitions:
                            assignment_map[(tp.topic, tp.partition)] = {
                                "consumer_id": member_id,
                                "host": host
                            }
            except Exception as desc_err:
                logger.warning("Failed to describe consumer group members: %s", desc_err)

            # Get committed offsets for the group
            group_offsets = admin.list_consumer_group_offsets(
                [ConsumerGroupTopicPartitions(target_group)]
            )
            # Wait for the future to resolve
            group_result = None
            for group_future in group_offsets.values():
                group_result = group_future.result()

            # Group partition details by topic
            topics_data = {}
            for tp in group_result.topic_partitions if group_result else []:
                if tp.error:
                    continue
                # Get high watermark for this partition
                lo, hi = consumer.get_watermark_offsets(
                    TopicPartition(tp.topic, tp.partition),
                    timeout=config.timeout_seconds,
                )
                committed = tp.offset if tp.offset >= 0 else 0
                lag = max(0, hi - committed)

                # Retrieve member info if assigned
                member_info = assignment_map.get((tp.topic, tp.partition), {})

                if tp.topic not in topics_data:
                    topics_data[tp.topic] = {
                        "topic_lag": 0,
                        "partitions": [],
                    }

                topics_data[tp.topic]["topic_lag"] += lag
                topics_data[tp.topic]["partitions"].append(
                    {
                        "partition": tp.partition,
                        "committed_offset": committed,
                        "high_watermark": hi,
                        "lag": lag,
                        "consumer_id": member_info.get("consumer_id", ""),
                        "host": member_info.get("host", ""),
                    }
                )

            total_lag = sum(t["topic_lag"] for t in topics_data.values())
            clean_state = state.split(".")[-1] if state else "UNKNOWN"
            return {
                "source": "kafka",
                "available": True,
                "group_id": target_group,
                "state": clean_state,
                "total_lag": total_lag,
                "topics": topics_data,
            }
        finally:
            consumer.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="kafka",
            method="get_consumer_group",
        )
        return {"source": "kafka", "available": False, "error": str(err)}


def get_topic_consumers(
    config: KafkaConfig,
    topic: str,
) -> dict[str, Any]:
    """Retrieve all consumer groups and their members consuming from a specific topic."""
    if not config.is_configured:
        return {"source": "kafka", "available": False, "error": "Not configured."}

    try:
        from confluent_kafka import TopicPartition
        try:
            from confluent_kafka import ConsumerGroupTopicPartitions
        except ImportError:
            from confluent_kafka.admin import ConsumerGroupTopicPartitions

        admin = _get_admin_client(config)
        consumer = _get_consumer(config)

        try:
            # 1. List all topics to support fuzzy substring search
            try:
                metadata = admin.list_topics(timeout=config.timeout_seconds)
                all_topics = list(metadata.topics.keys())
            except Exception as topics_err:
                logger.warning("Failed to list topics: %s", topics_err)
                all_topics = []

            # Perform case-insensitive substring search
            target_topic = topic
            if all_topics:
                if topic in all_topics:
                    target_topic = topic
                else:
                    matches = [t for t in all_topics if topic.lower() in t.lower()]
                    if len(matches) == 1:
                        target_topic = matches[0]
                    elif len(matches) > 1:
                        return {
                            "source": "kafka",
                            "available": True,
                            "topic_query": topic,
                            "multiple_matches": True,
                            "matched_topics": sorted(matches),
                            "error": f"Multiple topics matched keyword '{topic}': {', '.join(sorted(matches))}. Please specify the exact topic name.",
                        }
                    else:
                        return {
                            "source": "kafka",
                            "available": True,
                            "topic_query": topic,
                            "multiple_matches": False,
                            "matched_topics": [],
                            "error": f"No topics matched keyword '{topic}'. Available topics: {', '.join(sorted(all_topics))}",
                        }

            # 2. List all consumer groups in the cluster
            try:
                list_groups_future = admin.list_consumer_groups()
                list_groups_res = list_groups_future.result()
                all_groups = [g.group_id for g in list_groups_res.valid] if list_groups_res.valid else []
            except Exception as list_err:
                logger.warning("Failed to list consumer groups: %s", list_err)
                all_groups = []

            if not all_groups:
                return {
                    "source": "kafka",
                    "available": True,
                    "topic": target_topic,
                    "consumers": [],
                }

            consumers_info = []
            for gid in sorted(all_groups):
                offsets_res = None
                try:
                    offsets_dict = admin.list_consumer_group_offsets(
                        [ConsumerGroupTopicPartitions(gid)]
                    )
                    if gid in offsets_dict:
                        offsets_res = offsets_dict[gid].result()
                except Exception as offset_err:
                    logger.warning("Failed to query offsets for group %s: %s", gid, offset_err)

                has_topic_in_offsets = False
                if offsets_res and offsets_res.topic_partitions:
                    for tp in offsets_res.topic_partitions:
                        if tp.topic == target_topic:
                            has_topic_in_offsets = True
                            break

                state = "UNKNOWN"
                assignment_map = {}
                has_topic_in_assignments = False
                try:
                    describe_future = admin.describe_consumer_groups([gid])
                    describe_res = describe_future[gid].result()
                    state = str(describe_res.state) if describe_res.state else "UNKNOWN"
                    for member in describe_res.members:
                        host = member.host
                        member_id = member.member_id
                        if member.assignment and member.assignment.topic_partitions:
                            for tp in member.assignment.topic_partitions:
                                if tp.topic == target_topic:
                                    has_topic_in_assignments = True
                                assignment_map[(tp.topic, tp.partition)] = {
                                    "consumer_id": member_id,
                                    "host": host
                                }
                except Exception:
                    pass

                if has_topic_in_offsets or has_topic_in_assignments:
                    partitions_detail = []
                    topic_lag = 0

                    partitions_to_check = set()
                    if offsets_res and offsets_res.topic_partitions:
                        for tp in offsets_res.topic_partitions:
                            if tp.topic == target_topic:
                                partitions_to_check.add((tp.partition, tp.offset))

                    assigned_partitions = {p for (t, p) in assignment_map.keys() if t == target_topic}
                    for p in assigned_partitions:
                        if not any(part == p for (part, _) in partitions_to_check):
                            partitions_to_check.add((p, -1))

                    for partition, offset in sorted(partitions_to_check):
                        try:
                            lo, hi = consumer.get_watermark_offsets(
                                TopicPartition(target_topic, partition),
                                timeout=config.timeout_seconds,
                            )
                            committed = offset if offset >= 0 else 0
                            lag = max(0, hi - committed)
                        except Exception:
                            hi = 0
                            committed = offset if offset >= 0 else 0
                            lag = 0

                        member_info = assignment_map.get((target_topic, partition), {})
                        partitions_detail.append({
                            "partition": partition,
                            "committed_offset": committed,
                            "high_watermark": hi,
                            "lag": lag,
                            "consumer_id": member_info.get("consumer_id", ""),
                            "host": member_info.get("host", ""),
                        })
                        topic_lag += lag

                    clean_state = state.split(".")[-1] if state else "UNKNOWN"
                    consumers_info.append({
                        "group_id": gid,
                        "state": clean_state,
                        "topic_lag": topic_lag,
                        "partitions": partitions_detail,
                    })

            return {
                "source": "kafka",
                "available": True,
                "topic": target_topic,
                "consumers": consumers_info,
            }
        finally:
            consumer.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="kafka",
            method="get_topic_consumers",
        )
        return {"source": "kafka", "available": False, "error": str(err)}
