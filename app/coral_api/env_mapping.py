"""Map OpenSRE env vars / credentials to Coral input env vars."""
from __future__ import annotations

import os
from typing import Any


def build_coral_env(
    mapping: dict[str, str],
    credentials: dict[str, Any],
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build env vars for Coral subprocess.

    Args:
        mapping: {coral_input_key: opensre_env_var_or_credential_key}
        credentials: OpenSRE integration credentials dict
        env: Base env dict (defaults to os.environ)

    Returns:
        dict of env vars to pass to Coral subprocess
    """
    base_env = env if env is not None else os.environ.copy()
    coral_env: dict[str, str] = {}

    for coral_key, opensre_key in mapping.items():
        value: str | None = None

        # First check credentials dict
        if opensre_key in credentials:
            cred_value = credentials[opensre_key]
            if cred_value is not None and cred_value != "":
                value = str(cred_value)

        # Fall back to env var
        if value is None:
            value = base_env.get(opensre_key)

        # Only add non-empty values
        if value is not None and value != "":
            coral_env[coral_key] = value

    return coral_env
