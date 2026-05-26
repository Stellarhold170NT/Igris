from typing import Any

from pydantic import BaseModel, Field

from app.integrations.kafka import (
    KafkaConfig,
    get_topic_consumers,
    kafka_extract_params,
    kafka_is_available,
)
from app.tools.tool_decorator import tool


class KafkaTopicConsumersInput(BaseModel):
    topic: str = Field(
        description="The Kafka topic name, or a case-insensitive keyword/substring to search for it (e.g., 'vauthz', 'outbox').",
    )


@tool(
    name="get_kafka_topic_consumers",
    description="Retrieve all consumer groups, active instances, and host IPs consuming from a specific topic.",
    source="kafka",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Finding all active consumer IPs and groups consuming from a specific topic to identify rogue or local consumers",
        "Diagnosing lag on a specific topic across multiple consumer groups",
        "Mapping a topic to its active consumer services and hosts",
    ],
    input_model=KafkaTopicConsumersInput,
    is_available=kafka_is_available,
    extract_params=kafka_extract_params,
    injected_params=(
        "bootstrap_servers",
        "security_protocol",
        "sasl_mechanism",
        "sasl_username",
        "sasl_password",
    ),
)
def get_kafka_topic_consumers(
    bootstrap_servers: str,
    topic: str,
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    sasl_username: str = "",
    sasl_password: str = "",
) -> dict[str, Any]:
    """Fetch consumer groups and members for a specific topic."""
    config = KafkaConfig(
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        sasl_username=sasl_username,
        sasl_password=sasl_password,
    )
    return get_topic_consumers(config, topic=topic)
