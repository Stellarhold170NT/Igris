"""Map OpenSRE integrations to Coral native sources."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CoralSourceMapping:
    coral_name: str
    env_mapping: dict[str, str]  # {coral_input_key: opensre_credential_key}
    requires: list[str]  # Required credential keys
    is_available_env: str  # e.g. "CORAL_GRAFANA"


# Define mappings for grafana, datadog, github, slack, sentry, gitlab
# grafana: GRAFANA_URL→endpoint, GRAFANA_TOKEN→api_key
# datadog: DD_API_KEY→api_key, DD_APPLICATION_KEY→app_key, DD_SITE→site
# github: GITHUB_TOKEN→token
# ... etc

# Native Coral sources bundled with Coral CLI.
# env_mapping: {coral_input_key: opensre_credential_key}
# The env_mapping values match keys in OpenSRE's integration credentials dict.

GRAFANA_MAPPING = CoralSourceMapping(
    coral_name="grafana",
    env_mapping={
        "GRAFANA_URL": "endpoint",
        "GRAFANA_TOKEN": "api_key",
    },
    requires=["endpoint", "api_key"],
    is_available_env="CORAL_GRAFANA",
)

DATADOG_MAPPING = CoralSourceMapping(
    coral_name="datadog",
    env_mapping={
        "DD_API_KEY": "api_key",
        "DD_APPLICATION_KEY": "app_key",
        "DD_SITE": "site",
    },
    requires=["api_key", "app_key"],
    is_available_env="CORAL_DATADOG",
)

GITHUB_MAPPING = CoralSourceMapping(
    coral_name="github",
    env_mapping={
        "GITHUB_TOKEN": "token",
    },
    requires=["token"],
    is_available_env="CORAL_GITHUB",
)

SLACK_MAPPING = CoralSourceMapping(
    coral_name="slack",
    env_mapping={
        "SLACK_BOT_TOKEN": "token",
        "SLACK_TEAM_ID": "team_id",
    },
    requires=["token"],
    is_available_env="CORAL_SLACK",
)

SENTRY_MAPPING = CoralSourceMapping(
    coral_name="sentry",
    env_mapping={
        "SENTRY_TOKEN": "token",
        "SENTRY_ORG": "organization_slug",
    },
    requires=["token"],
    is_available_env="CORAL_SENTRY",
)

GITLAB_MAPPING = CoralSourceMapping(
    coral_name="gitlab",
    env_mapping={
        "GITLAB_TOKEN": "token",
        "GITLAB_URL": "base_url",
    },
    requires=["token"],
    is_available_env="CORAL_GITLAB",
)

# Registry of all source mappings
SOURCE_MAPPINGS: dict[str, CoralSourceMapping] = {
    "grafana": GRAFANA_MAPPING,
    "datadog": DATADOG_MAPPING,
    "github": GITHUB_MAPPING,
    "slack": SLACK_MAPPING,
    "sentry": SENTRY_MAPPING,
    "gitlab": GITLAB_MAPPING,
}


def get_enabled_sources(resolved_integrations: dict[str, dict[str, Any]]) -> dict[str, CoralSourceMapping]:
    """Return enabled Coral source mappings.

    A source is enabled if its CORAL_<NAME> env var is "true"/"1"/"yes",
    OR if CORAL_AUTO_ENABLE is set and the integration has credentials.
    """
    enabled: dict[str, CoralSourceMapping] = {}
    coral_auto_enable = os.environ.get("CORAL_AUTO_ENABLE", "").lower() in ("true", "1", "yes")

    for name, mapping in SOURCE_MAPPINGS.items():
        is_available = os.environ.get(mapping.is_available_env, "").lower() in ("true", "1", "yes")

        if is_available:
            enabled[mapping.coral_name] = mapping
            continue

        # Check if integration has credentials and CORAL_AUTO_ENABLE is set
        if coral_auto_enable and name in resolved_integrations:
            integration = resolved_integrations[name]
            credentials = integration.get("credentials", {})
            if all(credentials.get(key) for key in mapping.requires):
                enabled[mapping.coral_name] = mapping

    return enabled
