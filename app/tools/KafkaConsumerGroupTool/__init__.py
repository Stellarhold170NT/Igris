from typing import Any

from pydantic import BaseModel, Field

from app.integrations.kafka import (
    KafkaConfig,
    get_consumer_group,
    kafka_extract_params,
    kafka_is_available,
)
from app.tools.tool_decorator import tool


class KafkaConsumerGroupInput(BaseModel):
    group_id: str = Field(
        description="The consumer group ID, or a case-insensitive keyword/substring to search for it (e.g., 'vauthz', 'sync', 'portal').",
    )


@tool(
    name="get_kafka_consumer_group",
    description="Retrieve consumer group metadata, lag, and active instance assignments (including Partition, Consumer ID, and Host IP) from a Kafka cluster.",
    source="kafka",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing consumer lag causing processing delays",
        "Identifying stuck or slow consumers during an incident",
        "Checking consumer group health, active members, and Host IPs after a deployment",
    ],
    input_model=KafkaConsumerGroupInput,
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
def get_kafka_consumer_group(
    bootstrap_servers: str,
    group_id: str,
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    sasl_username: str = "",
    sasl_password: str = "",
) -> dict[str, Any]:
    """Fetch consumer group information from a Kafka cluster."""
    config = KafkaConfig(
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        sasl_username=sasl_username,
        sasl_password=sasl_password,
    )
    return get_consumer_group(config, group_id=group_id)
