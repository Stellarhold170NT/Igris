"""Vauthz PDP compare tool."""

from typing import Any

from app.integrations.vauthz import (
    compare_pdp_data,
    vauthz_extract_params,
    vauthz_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="vauthz_compare_pdp_data",
    description="Compare the OPA data of a specific PDP with the DB source-of-truth data (if there is a data mismatch but no event exists relating to the missing/changed data, it indicates a lost event).",
    source="vauthz",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Verifying that OPA policy data on a PDP is correctly synchronized with the database",
        "Diagnosing synchronization delays or data inconsistencies for a topic",
        "Inspecting differences between PDP local cache and DB state",
    ],
    is_available=vauthz_is_available,
    extract_params=vauthz_extract_params,
)
def vauthz_compare_pdp_data(
    pdp_id: str,
    env_id: str | None = None,
    pdp_gateway_url: str | None = None,
    vauthz_url: str | None = None,
    pdp_syncheck_url: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Retrieve and compare OPA data and DB data for a topic using jsondiff."""
    if not pdp_gateway_url or not vauthz_url or not pdp_syncheck_url:
        params = vauthz_extract_params({})
        if not pdp_gateway_url:
            pdp_gateway_url = params["pdp_gateway_url"]
        if not vauthz_url:
            vauthz_url = params["vauthz_url"]
        if not pdp_syncheck_url:
            pdp_syncheck_url = params["pdp_syncheck_url"]

    return compare_pdp_data(
        pdp_gateway_url=pdp_gateway_url,
        vauthz_url=vauthz_url,
        pdp_id=pdp_id,
        pdp_syncheck_url=pdp_syncheck_url,
        env_id=env_id,
    )
