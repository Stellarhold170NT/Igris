"""Vauthz PDP list tool."""

from typing import Any

from app.integrations.vauthz import (
    list_pdps,
    vauthz_extract_params,
    vauthz_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="vauthz_list_pdps",
    description="List monitored Vauthz PDPs and check their health, versions, and active topics.",
    source="vauthz",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking the status and list of monitored PDPs in the system",
        "Diagnosing unhealthy PDP instances or synchronization issues",
        "Retrieving topics mapped to a specific PDP",
    ],
    is_available=vauthz_is_available,
    extract_params=vauthz_extract_params,
)
def vauthz_list_pdps(pdp_syncheck_url: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Retrieve the PDP fleet status from the syncheck service."""
    if not pdp_syncheck_url:
        params = vauthz_extract_params({})
        pdp_syncheck_url = params["pdp_syncheck_url"]

    return list_pdps(pdp_syncheck_url)
