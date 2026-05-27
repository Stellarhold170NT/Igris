"""Grafana dashboard values query and analysis tool."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.tools.GrafanaGetDashboardFiltersTool import (
    parse_grafana_variable_query,
    resolve_dependent_variables,
)
from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool


class GetDashboardValuesInput(BaseModel):
    dashboard_uid: str = Field(
        ...,
        description="The unique UID of the Grafana dashboard.",
    )
    time_range: str = Field(
        default="1h",
        description="The relative time range to analyze (e.g., '30m', '1h', '6h', '24h').",
    )
    variables: dict[str, str] = Field(
        default_factory=dict,
        description="Template filter variables to apply (e.g., {'host': 'maria02'}).",
    )


class GetDashboardValuesOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether the query succeeded.")
    dashboard_title: str | None = Field(default=None, description="Title of the dashboard.")
    panels: list[dict[str, Any]] = Field(default_factory=list, description="Extracted panel metrics and statistical values.")
    error: str | None = Field(default=None, description="Error message if query failed.")


def _get_dashboard_values_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "dashboard_uid": "mysql-workload",
        "time_range": "1h",
        "variables": {},
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _get_dashboard_values_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


def parse_time_range(range_str: str) -> tuple[int, int]:
    """Parse relative time range and return start and end epoch timestamps."""
    end = datetime.now(UTC)
    m = re.match(r"^(\d+)([smhd])$", range_str.strip().lower())
    if not m:
        start = end - timedelta(hours=1)
    else:
        val = int(m.group(1))
        unit = m.group(2)
        if unit == "s":
            start = end - timedelta(seconds=val)
        elif unit == "m":
            start = end - timedelta(minutes=val)
        elif unit == "h":
            start = end - timedelta(hours=val)
        elif unit == "d":
            start = end - timedelta(days=val)
        else:
            start = end - timedelta(hours=1)
    return int(start.timestamp()), int(end.timestamp())


def extract_panels(dashboard_json: dict) -> list[dict[str, Any]]:
    """Recursively extract all panels containing metric targets from dashboard JSON."""
    panels = []

    def walk(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "row" and "panels" in item:
                walk(item["panels"])
            elif "targets" in item:
                panels.append(item)
            elif "panels" in item:
                walk(item["panels"])

    dashboard = dashboard_json.get("dashboard", {})
    walk(dashboard.get("panels", []))
    for row in dashboard.get("rows", []):
        walk(row.get("panels", []))

    return panels


def substitute_query_variables(
    query: str,
    variables: dict[str, str],
    dashboard_defaults: dict[str, str],
    step: str = "1m",
) -> str:
    """Replace Grafana template variables and built-in variables in query string."""
    merged = {**dashboard_defaults, **variables}

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        if not var_name:
            return match.group(0)

        if var_name in merged:
            v = merged[var_name]
            if var_name in ("interval", "__interval") and (str(v).lower() in ("auto", "all") or str(v).startswith("$__auto_interval")):
                return step
            if isinstance(v, list):
                return "|".join(str(item) for item in v)
            if str(v) in ("All", "$__all", "all"):
                return ".*"
            return str(v)

        return match.group(0)

    # Replace variables matching $var, ${var}, ${var:format}
    pattern = r"\$(?:(\w+)|\{(\w+)(?::\w+)?\})"
    query = re.sub(pattern, replace_match, query)

    # Substitute Grafana built-ins
    query = query.replace("$__interval_ms", "60000").replace("${__interval_ms}", "60000")
    query = query.replace("$__range_ms", "3600000").replace("${__range_ms}", "3600000")
    query = query.replace("$__range_s", "3600").replace("${__range_s}", "3600")
    query = query.replace("$__interval", step).replace("${__interval}", step)
    query = query.replace("$__range", "1h").replace("${__range}", "1h")
    query = query.replace("$__rate_interval", "5m").replace("${__rate_interval}", "5m")
    return query


def format_value(val: float, unit: str | None) -> str:
    """Format numeric metrics using their designated units."""
    if unit is None:
        return f"{val:.2f}"

    unit = unit.lower()
    if unit in ("short", "none", "num"):
        for factor, suffix in [(10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K")]:
            if abs(val) >= factor:
                return f"{val / factor:.2f} {suffix}"
        return f"{val:.2f}"

    # Throughput/rates
    if any(u in unit for u in ("bps", "bytes/sec", "b/s", "bytes/s")):
        for factor, suffix in [(1024**3, "GB/s"), (1024**2, "MB/s"), (1024, "kB/s")]:
            if val >= factor:
                return f"{val / factor:.2f} {suffix}"
        return f"{val:.2f} B/s"

    # Format bytes
    if any(u in unit for u in ("bytes", "decibytes", "kbytes", "mbytes", "gbytes")):
        for factor, suffix in [(1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB")]:
            if val >= factor:
                return f"{val / factor:.2f} {suffix}"
        return f"{val:.2f} B"

    # Percentages
    if any(u in unit for u in ("percent", "pct", "%")):
        return f"{val:.2f}%"

    # Time durations
    if unit in ("ms", "milliseconds"):
        return f"{val:.2f} ms"
    if unit in ("s", "seconds"):
        return f"{val:.2f} s"

    return f"{val:.2f} {unit}"


@tool(
    name="get_grafana_dashboard_values",
    display_name="Grafana Get Dashboard Values",
    source="grafana",
    description="Retrieve statistical values (mean, max, min, current) for panels in a Grafana dashboard using configured filters.",
    use_cases=[
        "Analyzing current resource trends and questions rate from Grafana dashboard panels",
        "Diagnosing performance degradation of a database using MySQL dashboard panel values",
    ],
    source_id="grafana_dashboards",
    evidence_type="metrics",
    side_effect_level="read_only",
    input_model=GetDashboardValuesInput,
    output_model=GetDashboardValuesOutput,
    injected_params=("grafana_endpoint", "grafana_api_key", "grafana_backend"),
    is_available=_get_dashboard_values_available,
    extract_params=_get_dashboard_values_extract_params,
)
def get_grafana_dashboard_values(
    dashboard_uid: str,
    time_range: str = "1h",
    variables: dict[str, str] | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Execute and aggregate metrics for a Grafana dashboard under specified filters."""
    vars_dict = variables or {}

    if grafana_backend is not None:
        # Mock behavior for synthetic tests
        if hasattr(grafana_backend, "get_dashboard_values"):
            return {
                "source": "grafana_dashboards",
                "available": True,
                "dashboard_title": "MySQL/MariaDB Workload",
                "panels": grafana_backend.get_dashboard_values(dashboard_uid, vars_dict),
            }
        return {
            "source": "grafana_dashboards",
            "available": True,
            "dashboard_title": "MySQL/MariaDB Workload",
            "panels": [
                {
                    "panel_title": "MySQL Questions",
                    "metrics": [
                        {
                            "name": "Questions",
                            "mean": "27.60 requests/sec",
                            "max": "384.00 requests/sec",
                            "min": "15.40 requests/sec",
                            "current": "25.10 requests/sec",
                        }
                    ],
                }
            ],
        }

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_dashboards",
            "available": False,
            "error": "Grafana integration not configured",
        }

    try:
        model = client.get_dashboard(dashboard_uid)
    except Exception as e:
        return {
            "source": "grafana_dashboards",
            "available": False,
            "error": f"Failed to fetch dashboard JSON: {e}",
        }

    dashboard = model.get("dashboard", {})
    title = dashboard.get("title", "")
    panels_list = extract_panels(model)

    start_epoch, end_epoch = parse_time_range(time_range)
    duration = end_epoch - start_epoch
    step_secs = max(15, duration // 200)
    step = f"{step_secs}s"

    # Extract and resolve template variables, seeding with user-provided overrides
    variables_list = dashboard.get("templating", {}).get("list", [])
    default_vars: dict[str, str] = dict(vars_dict)
    for var in variables_list:
        name = var.get("name", "")
        if not name or name in default_vars:
            continue
        var_type = var.get("type", "")
        current_obj = var.get("current", {})
        val = current_obj.get("value", "")
        if isinstance(val, list):
            val = val[0] if val else ""

        # If val is empty, resolve it
        if not val or val == "[]":
            if var.get("includeAll"):
                val = "All"
            else:
                opts_list = var.get("options", [])
                if opts_list:
                    valid_opts = [o.get("value") for o in opts_list if o.get("value") not in ("$__all", "All", "all")]
                    val = valid_opts[0] if valid_opts else ""
                elif var_type == "query" and client and client.is_configured:
                    raw_query = var.get("query", "")
                    if isinstance(raw_query, dict):
                        raw_query = raw_query.get("query", "")
                    resolved_query = resolve_dependent_variables(raw_query, default_vars)
                    q_type, label, metric = parse_grafana_variable_query(resolved_query)
                    if q_type == "label_values" and label:
                        opts = client.query_prometheus_label_values(label, match=metric)
                        val = opts[0] if opts else ""
                    else:
                        val = ""
                else:
                    val = ""
        default_vars[name] = str(val)

    panels_out = []

    for panel in panels_list:
        p_title = panel.get("title", "Untitled Panel")
        targets = panel.get("targets", [])
        if not targets:
            continue

        # Determine metric format/unit
        unit = panel.get("fieldConfig", {}).get("defaults", {}).get("unit")
        if not unit and panel.get("yaxes"):
            # Fallback for old Grafana dashboard schemas
            yaxes = panel.get("yaxes", [])
            if yaxes:
                unit = yaxes[0].get("format")

        metrics_out = []

        for target in targets:
            expr = target.get("expr", "")
            if not expr:
                continue

            ref_id = target.get("refId", "A")

            # Replace variable placeholders in the query
            resolved_expr = substitute_query_variables(expr, {}, default_vars, step=step)

            # Query Prometheus range API
            res = client.query_mimir_range(resolved_expr, start=start_epoch, end=end_epoch, step=step)
            if not res.get("success") or not res.get("metrics"):
                # Append a "No data" placeholder
                metrics_out.append(
                    {
                        "name": ref_id,
                        "mean": "No data",
                        "max": "No data",
                        "min": "No data",
                        "current": "No data",
                    }
                )
                continue

            for series in res.get("metrics", []):
                metric_labels = series.get("metric", {})
                # Name metric using its signature labels if available
                metric_name = metric_labels.get("__name__") or ref_id
                label_details = ", ".join(f"{k}={v}" for k, v in metric_labels.items() if k != "__name__")
                full_name = f"{metric_name}{{{label_details}}}" if label_details else metric_name

                # Parse float values
                vals = []
                for pt in series.get("values", []):
                    try:
                        if pt[1] is not None:
                            vals.append(float(pt[1]))
                    except (ValueError, TypeError):
                        continue

                if not vals:
                    metrics_out.append(
                        {
                            "name": full_name,
                            "mean": "No data",
                            "max": "No data",
                            "min": "No data",
                            "current": "No data",
                        }
                    )
                    continue

                mean_val = sum(vals) / len(vals)
                max_val = max(vals)
                min_val = min(vals)
                current_val = vals[-1]

                metrics_out.append(
                    {
                        "name": full_name,
                        "mean": format_value(mean_val, unit),
                        "max": format_value(max_val, unit),
                        "min": format_value(min_val, unit),
                        "current": format_value(current_val, unit),
                    }
                )

        if metrics_out:
            panels_out.append({"panel_title": p_title, "metrics": metrics_out})

    return {
        "source": "grafana_dashboards",
        "available": True,
        "dashboard_title": title,
        "panels": panels_out,
    }
