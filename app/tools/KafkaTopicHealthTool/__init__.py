from typing import Any

from pydantic import BaseModel, Field

from app.integrations.kafka import (
    KafkaConfig,
    get_topic_health,
    kafka_extract_params,
    kafka_is_available,
)
from app.tools.tool_decorator import tool


class KafkaTopicHealthInput(BaseModel):
    topic: str = Field(
        default="",
        description="Filter topics by name or keyword/substring (case-insensitive search). E.g., 'vauthz' or 'civil_affairs'. Leave empty to list all topics.",
    )
    limit: int = Field(
        default=20,
        description="Maximum number of topics to return in this call (hard capped at max_results).",
    )
    offset: int = Field(
        default=0,
        description="Pagination offset to retrieve more topics.",
    )


@tool(
    name="get_kafka_topic_health",
    description="Retrieve topic partition health from a Kafka cluster, including replica status, ISR counts, and under-replicated partitions.",
    source="kafka",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking partition health during a consumer lag incident",
        "Identifying under-replicated partitions after a broker failure",
        "Reviewing topic metadata for capacity planning",
    ],
    input_model=KafkaTopicHealthInput,
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
def get_kafka_topic_health(
    bootstrap_servers: str,
    topic: str = "",
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    sasl_username: str = "",
    sasl_password: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch topic partition health from a Kafka cluster."""
    config = KafkaConfig(
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        sasl_username=sasl_username,
        sasl_password=sasl_password,
    )
    return get_topic_health(config, topic=topic or None, limit=limit, offset=offset)
