"""Vauthz integration helpers.

Provides configuration validation, PDP fleet status retrieval, and policy/data diff checks.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from collections import Counter
import httpx
import jsondiff

logger = logging.getLogger(__name__)


def ensure_scheme(url: str) -> str:
    """Ensure that the URL starts with http:// or https://."""
    url = url.strip()
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"http://{url}"
    return url


def get_pdp_fleet_url(url: str) -> str:
    """Get the full URL for listing PDPs."""
    url = ensure_scheme(url)
    if "/api/fleet" not in url:
        url = url.rstrip("/") + "/api/fleet"
    return url


def get_pdp_data_url(base_url: str) -> str:
    """Get the URL for retrieving OPA data."""
    base_url = ensure_scheme(base_url).rstrip("/")
    return f"{base_url}/v1/data"


def get_db_data_url(base_url: str, env_id: str) -> str:
    """Get the URL for retrieving DB source-of-truth data for an env_id."""
    base_url = ensure_scheme(base_url).rstrip("/")
    return f"{base_url}/opa_data/{env_id}"


def vauthz_is_available(sources: dict[str, dict]) -> bool:
    """Vauthz tools are always available as they fallback to env or default settings."""
    return True


def vauthz_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Vauthz configuration parameters from integration store or env fallback."""
    vauthz = sources.get("vauthz", {})
    return {
        "pdp_syncheck_url": str(
            os.getenv("PDP_SYNCHECK_URL")
            or vauthz.get("pdp_syncheck_url")
            or "http://117.5.151.111:4000/api/fleet"
        ).strip(),
        "pdp_gateway_url": str(
            os.getenv("PDP_GATEWAY_URL")
            or vauthz.get("pdp_gateway_url")
            or "http://117.5.151.111:7766"
        ).strip(),
        "vauthz_url": str(
            os.getenv("VAUTHZ_URL")
            or vauthz.get("vauthz_url")
            or "http://117.5.151.111:8081"
        ).strip(),
    }



def list_pdps(pdp_syncheck_url: str) -> dict[str, Any]:
    """Call the syncheck fleet API to list monitored PDPs."""
    url = get_pdp_fleet_url(pdp_syncheck_url)
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            raw_pdps = data.get("pdps", [])
            filtered_pdps = []
            
            # Filter pdps whose pdp_version contains "pdp"
            for pdp in raw_pdps:
                pdp_version = str(pdp.get("pdp_version", ""))
                if "pdp" in pdp_version.lower():
                    filtered_pdps.append(pdp)
            
            # Count PDP instances grouped by env_id
            env_counts = Counter(p["env_id"] for p in filtered_pdps if p.get("env_id"))
            
            processed_pdps = []
            for pdp in filtered_pdps:
                env_id = pdp.get("env_id")
                online = bool(pdp.get("is_healthy", False))
                desc = "Hoạt động bình thường" if online else "PDP đã bị chết trên k8s server"
                
                processed_pdps.append({
                    "pdp_id": pdp.get("id"),
                    "env_id": env_id,
                    "project_name": pdp.get("project_name"),
                    "pdp_version": pdp.get("pdp_version"),
                    "online": online,
                    "desc": desc,
                    "number_count": env_counts.get(env_id, 0),
                })
                
            return {"pdps": processed_pdps}
    except Exception as e:
        logger.error(f"Error fetching PDP fleet list from {url}: {e}")
        return {"error": f"Failed to list PDPs: {e}"}


def extract_pdp_name(pdp_version: str) -> str:
    """Derive the X-pdp-name header value from pdp_version.

    Example: 'military-youth-pdp-9976fdc6b-5ckvt' -> 'military-youth'
    """
    idx = pdp_version.lower().find("-pdp-")
    if idx > 0:
        return pdp_version[:idx]
    return pdp_version


def get_pdp_events(pdp_syncheck_url: str, pdp_id: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Retrieve event history for a specific PDP."""
    pdp_syncheck_url = ensure_scheme(pdp_syncheck_url).rstrip("/")
    if pdp_syncheck_url.endswith("/api/fleet"):
        pdp_syncheck_url = pdp_syncheck_url[:-10].rstrip("/")
    url = f"{pdp_syncheck_url}/api/pdp/{pdp_id}/events"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Error fetching events for PDP {pdp_id} from {url}: {e}")
        return {"error": str(e)}


def extract_and_group_events(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Extract and group dst_path values from OpalCallback events by prefix."""
    grouped: dict[str, set[str]] = {}
    for event in events:
        if event.get("event_type") != "OpalCallback":
            continue
        
        paths: set[str] = set()
        
        # Check details.verify_results
        verify_results = event.get("details", {})
        if isinstance(verify_results, dict):
            vr = verify_results.get("verify_results", [])
            if isinstance(vr, list):
                for r in vr:
                    if isinstance(r, dict) and "dst_path" in r:
                        paths.add(r["dst_path"])
        
        # Check callback_info.delta_snapshots
        cb_info = event.get("callback_info", {})
        if isinstance(cb_info, dict):
            delta = cb_info.get("delta_snapshots", [])
            if isinstance(delta, list):
                for s in delta:
                    if isinstance(s, dict) and "dst_path" in s:
                        paths.add(s["dst_path"])
            
            # Check callback_info.raw_payload.reports
            payload = cb_info.get("raw_payload", {})
            if isinstance(payload, dict):
                reports = payload.get("reports", [])
                if isinstance(reports, list):
                    for r in reports:
                        if isinstance(r, dict):
                            entry = r.get("entry", {})
                            if isinstance(entry, dict) and "dst_path" in entry:
                                paths.add(entry["dst_path"])
        
        for path in paths:
            if not path:
                continue
            parts = [p for p in path.split("/") if p]
            if parts:
                prefix = parts[0]
                if prefix not in grouped:
                    grouped[prefix] = set()
                grouped[prefix].add(path)
                
    return {k: sorted(list(v)) for k, v in grouped.items()}


def extract_auth_token(events: list[dict[str, Any]]) -> str | None:
    """Find and return the Authorization token from events."""
    for event in events:
        cb_info = event.get("callback_info", {})
        if not isinstance(cb_info, dict):
            continue
        payload = cb_info.get("raw_payload", {})
        if not isinstance(payload, dict):
            continue
        reports = payload.get("reports", [])
        if not isinstance(reports, list):
            continue
        for r in reports:
            if not isinstance(r, dict):
                continue
            entry = r.get("entry", {})
            if not isinstance(entry, dict):
                continue
            config = entry.get("config", {})
            if not isinstance(config, dict):
                continue
            headers = config.get("headers", {})
            if isinstance(headers, dict):
                for k, v in headers.items():
                    if k.lower() == "authorization":
                        return v
    return None


def remove_null_fields(data: Any) -> Any:
    """Recursively strip fields containing None/null values."""
    if isinstance(data, dict):
        return {k: remove_null_fields(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [remove_null_fields(item) for item in data if item is not None]
    return data


def jsonify_diff(diff: Any) -> Any:
    """Recursively convert jsondiff Symbol keys/values to JSON-serializable strings."""
    if isinstance(diff, dict):
        return {
            (str(k) if isinstance(k, jsondiff.Symbol) else k): jsonify_diff(v)
            for k, v in diff.items()
        }
    elif isinstance(diff, list):
        return [jsonify_diff(item) for item in diff]
    elif isinstance(diff, jsondiff.Symbol):
        return str(diff)
    return diff


def diff_db_minus_opa(db_val: Any, opa_val: Any) -> Any:
    """Recursively computes differences between db_val and opa_val.
    Returns a dictionary with 'db' and 'opa' keys showing the mismatched/missing data.
    """
    if isinstance(db_val, dict) and isinstance(opa_val, dict):
        diff_dict = {}
        for k, v in db_val.items():
            if k not in opa_val:
                diff_dict[k] = {"db": v, "opa": None}
            else:
                sub_diff = diff_db_minus_opa(v, opa_val[k])
                if sub_diff is not None and sub_diff != {} and sub_diff != []:
                    diff_dict[k] = sub_diff
        return diff_dict if diff_dict else None
    elif isinstance(db_val, list) and isinstance(opa_val, list):
        diff_list = []
        for item in db_val:
            found = False
            for opa_item in opa_val:
                if type(item) == type(opa_item):
                    if isinstance(item, (dict, list)):
                        sub = diff_db_minus_opa(item, opa_item)
                        sub_rev = diff_db_minus_opa(opa_item, item)
                        if sub is None and sub_rev is None:
                            found = True
                            break
                    elif item == opa_item:
                        found = True
                        break
            if not found:
                diff_list.append(item)
        if diff_list:
            return {"db": diff_list, "opa": []}
        return None
    else:
        if db_val == opa_val:
            return None
        return {"db": db_val, "opa": opa_val}


def compare_pdp_data(
    pdp_gateway_url: str,
    vauthz_url: str,
    pdp_id: str,
    pdp_syncheck_url: str,
    env_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve events, token, DB and OPA data, and return a comparison report."""
    # 1. Fetch events
    events_res = get_pdp_events(pdp_syncheck_url, pdp_id)
    if isinstance(events_res, dict) and "error" in events_res:
        return {"pdp_id": pdp_id, "success": False, "error": f"Failed to fetch PDP events: {events_res['error']}"}

    # events API returns {"pdp_id": ..., "events": [...]}
    if isinstance(events_res, dict):
        events = events_res.get("events", [])
    else:
        events = events_res if isinstance(events_res, list) else []
    
    # 2. Extract and group events
    grouped_events = extract_and_group_events(events)
    
    # 3. Extract auth token
    token = extract_auth_token(events)
    
    # 4. Determine env_id
    if not env_id:
        # Try to extract from event topics
        for event in events:
            cb_info = event.get("callback_info", {})
            if isinstance(cb_info, dict):
                topics = cb_info.get("topics", [])
                if isinstance(topics, list):
                    for t in topics:
                        if isinstance(t, str) and t.startswith("policy_data:"):
                            env_id = t.split(":", 1)[1]
                            break
            if env_id:
                break
        
        # Fallback to list_pdps lookup
        if not env_id:
            fleet = list_pdps(pdp_syncheck_url)
            for pdp in fleet.get("pdps", []):
                if pdp.get("pdp_id") == pdp_id:
                    env_id = pdp.get("env_id")
                    break
                    
    if not env_id:
        return {"pdp_id": pdp_id, "success": False, "error": "Could not determine env_id for the PDP."}
        
    # 5. Fetch DB data
    db_data_url = get_db_data_url(vauthz_url, env_id)
    
    # 6. Fetch OPA data
    pdp_data_url = get_pdp_data_url(pdp_gateway_url)
    
    opa_data = None
    db_data = None
    errors = {}
    
    with httpx.Client(timeout=10.0) as client:
        # Fetch DB data
        try:
            resp = client.get(db_data_url)
            resp.raise_for_status()
            db_data = resp.json()
        except Exception as e:
            logger.error(f"Error fetching DB data from {db_data_url}: {e}")
            errors["db_error"] = str(e)
            
        # Fetch OPA data (requires X-pdp-name header)
        try:
            headers = {}
            if token:
                headers["Authorization"] = token
            # Look up pdp_version from fleet to derive X-pdp-name
            fleet = list_pdps(pdp_syncheck_url)
            for pdp in fleet.get("pdps", []):
                if pdp.get("pdp_id") == pdp_id:
                    pdp_name = extract_pdp_name(str(pdp.get("pdp_version", "")))
                    if pdp_name:
                        headers["X-pdp-name"] = pdp_name
                    break
            resp = client.get(pdp_data_url, headers=headers)
            resp.raise_for_status()
            opa_data = resp.json()
            if isinstance(opa_data, dict) and "result" in opa_data:
                opa_data = opa_data["result"]
        except Exception as e:
            logger.error(f"Error fetching OPA data from {pdp_data_url}: {e}")
            errors["opa_error"] = str(e)
            
    if errors:
        return {
            "pdp_id": pdp_id,
            "env_id": env_id,
            "success": False,
            "events": grouped_events,
            **errors,
        }
        
    # Remove null fields from db_data
    db_data = remove_null_fields(db_data)
    
    # Filter both to only compare common keys, ignoring irrelevant fields
    if isinstance(opa_data, dict) and isinstance(db_data, dict):
        common_keys = set(db_data.keys()).intersection(set(opa_data.keys()))
        common_keys.discard("vauthz")
        opa_data = {k: opa_data[k] for k in common_keys}
        db_data = {k: db_data[k] for k in common_keys}
        
    # Compare using direct subtraction
    try:
        diff_res = diff_db_minus_opa(db_data, opa_data) or {}
        is_identical = not bool(diff_res)
        return {
            "pdp_id": pdp_id,
            "env_id": env_id,
            "success": True,
            "events": grouped_events,
            "is_identical": is_identical,
            "diff": diff_res,
        }
    except Exception as e:
        return {
            "pdp_id": pdp_id,
            "env_id": env_id,
            "success": False,
            "events": grouped_events,
            "error": f"Failed to diff JSON data: {e}",
        }
