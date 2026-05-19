"""Grafana Tempo trace query tool."""

from __future__ import annotations

import re
from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _grafana_source,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool
from app.tools.utils.compaction import DEFAULT_TRACE_LIMIT, compact_traces, summarize_counts


def _extract_pipeline_spans(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pipeline_spans: list[dict[str, Any]] = []
    for trace in traces:
        for span in trace.get("spans", []):
            if span.get("name") in ["extract_data", "validate_data", "transform_data", "load_data"]:
                pipeline_spans.append(
                    {
                        "span_name": span.get("name"),
                        "execution_run_id": span.get("attributes", {}).get("execution.run_id"),
                        "record_count": span.get("attributes", {}).get("record_count"),
                    }
                )
    return pipeline_spans


def _match_target(pattern: str, target: str) -> bool:
    if not pattern or not target:
        return False
    if pattern in target:
        return True
        
    has_glob = '*' in pattern or '?' in pattern
    
    if has_glob:
        p = pattern
        has_end_wildcard = False
        if p.endswith('/**'):
            p = p[:-3]
            has_end_wildcard = True
        elif p.endswith('/*'):
            p = p[:-2]
            has_end_wildcard = True
            
        escaped = re.escape(p)
        regex_pattern = escaped.replace(r'\*', r'.*').replace(r'\?', r'.')
        
        if has_end_wildcard:
            regex_pattern = regex_pattern + r'(?:/.*)?'
    else:
        regex_pattern = pattern
        
    try:
        if re.search(regex_pattern, target, re.IGNORECASE):
            return True
    except re.error:
        pass
    return False


def _query_grafana_traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "service_name": grafana.get("service_name", ""),
        "trace_id": grafana.get("trace_id"),
        "http_method": grafana.get("http_method"),
        "http_target": grafana.get("http_target"),
        "execution_run_id": grafana.get("execution_run_id"),
        "limit": grafana.get("limit", DEFAULT_TRACE_LIMIT),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_traces_available(sources: dict[str, dict]) -> bool:
    # `no_traces` is set for RDS/database resource-threshold alerts (storage,
    # CPU, connections, IOPS) where Tempo contains no useful data. Removing the
    # action from the planner's choice set is more reliable than the soft prompt
    # prohibition — the LLM was observed picking traces anyway and burning the
    # trajectory_budget gate (see scenario
    # 008-storage-full-missing-metric).
    if _grafana_source(sources).get("no_traces"):
        return False
    return _grafana_available(sources)


@tool(
    name="query_grafana_traces",
    display_name="Grafana Tempo",
    source="grafana",
    description="Query Grafana Cloud Tempo for pipeline traces.",
    use_cases=[
        "Tracing distributed request flows during a pipeline failure",
        "Identifying slow spans or timeout patterns",
        "Correlating trace data with log errors",
    ],
    requires=["service_name"],
    input_schema={
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "trace_id": {"type": "string"},
            "http_method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "description": "Filter traces by HTTP method (GET, POST, etc.)"
            },
            "http_target": {
                "type": "string",
                "description": "Filter traces by HTTP target path, substring, glob patterns (e.g., /v2/facts/** or /v2/facts/*), or regex (e.g., ^/v2/facts/[0-9]+$)"
            },
            "execution_run_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["service_name"],
    },
    is_available=_query_grafana_traces_available,
    extract_params=_query_grafana_traces_extract_params,
)
def query_grafana_traces(
    service_name: str,
    trace_id: str | None = None,
    http_method: str | None = None,
    http_target: str | None = None,
    execution_run_id: str | None = None,
    limit: int = 20,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces."""
    if grafana_backend is not None:
        raw = grafana_backend.query_traces(service_name=service_name)
        traces = raw.get("traces", [])
        if trace_id:
            traces = [t for t in traces if t.get("traceID") == trace_id]
        if http_method and traces:
            method_upper = http_method.upper()
            filtered_traces = []
            for t in traces:
                matches = False
                for s in t.get("spans", []):
                    span_method = s.get("attributes", {}).get("http.method")
                    if span_method and str(span_method).upper() == method_upper:
                        matches = True
                        break
                    span_name = s.get("name", "")
                    if span_name.upper().startswith(method_upper + " "):
                         matches = True
                         break
                if matches:
                    filtered_traces.append(t)
            traces = filtered_traces
        if http_target and traces:
            filtered_traces = []
            for t in traces:
                matches = False
                for s in t.get("spans", []):
                    span_target = s.get("attributes", {}).get("http.target")
                    if span_target and _match_target(http_target, str(span_target)):
                        matches = True
                        break
                    span_name = s.get("name", "")
                    if _match_target(http_target, span_name):
                         matches = True
                         break
                if matches:
                    filtered_traces.append(t)
            traces = filtered_traces
        if execution_run_id and traces:
            filtered = [
                t
                for t in traces
                if any(
                    s.get("attributes", {}).get("execution.run_id") == execution_run_id
                    for s in t.get("spans", [])
                )
            ]
            traces = filtered if filtered else traces
        compacted_traces = compact_traces(traces, limit=limit)
        summary = summarize_counts(len(traces), len(compacted_traces), "traces")
        result_data: dict[str, Any] = {
            "source": "grafana_tempo",
            "available": True,
            "traces": compacted_traces,
            "pipeline_spans": _extract_pipeline_spans(compacted_traces),
            "total_traces": len(traces),
            "service_name": service_name,
            "trace_id": trace_id,
            "http_method": http_method,
            "execution_run_id": execution_run_id,
        }
        if summary:
            result_data["truncation_note"] = summary
        return result_data

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Grafana integration not configured",
            "traces": [],
        }
    if not client.tempo_datasource_uid:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Tempo datasource not found",
            "traces": [],
        }

    if trace_id:
        # Fetch details for the specific trace directly
        result = client._get_trace_details(trace_id)
        traces = [{
            "traceID": trace_id,
            "spans": result.get("spans", [])
        }]
        total_traces = 1
    else:
        # Construct TraceQL query if http_method or http_target are specified,
        # to filter on the Tempo side using span name. This prevents flooding from background Redis PING.
        q = None
        if http_method or http_target:
            method_part = http_method.upper() if http_method else ".*"
            if http_target:
                target_clean = http_target
                has_glob = '*' in target_clean or '?' in target_clean
                if has_glob:
                    escaped = re.escape(target_clean)
                    regex_target = escaped.replace(r'\*', r'.*').replace(r'\?', r'.')
                else:
                    regex_target = f".*{re.escape(target_clean)}.*"
            else:
                regex_target = ".*"
                
            name_regex = f"(?i)^{method_part} {regex_target}"
            name_regex_escaped = name_regex.replace('\\', '\\\\')
            q = f'{{.service.name="{service_name}" && name =~ "{name_regex_escaped}"}}'

        result = client.query_tempo(service_name, limit=limit, q=q)
        if not result.get("success"):
            return {
                "source": "grafana_tempo",
                "available": False,
                "error": result.get("error", "Unknown error"),
                "traces": [],
            }
        traces = result.get("traces", [])
        total_traces = result.get("total_traces", 0)

    if http_method and traces:
        method_upper = http_method.upper()
        filtered_traces = []
        for t in traces:
            matches = False
            for s in t.get("spans", []):
                span_method = s.get("attributes", {}).get("http.method")
                if span_method and str(span_method).upper() == method_upper:
                    matches = True
                    break
                span_name = s.get("name", "")
                if span_name.upper().startswith(method_upper + " "):
                     matches = True
                     break
            if matches:
                filtered_traces.append(t)
        traces = filtered_traces

    if http_target and traces:
        filtered_traces = []
        for t in traces:
            matches = False
            for s in t.get("spans", []):
                span_target = s.get("attributes", {}).get("http.target")
                if span_target and _match_target(http_target, str(span_target)):
                    matches = True
                    break
                span_name = s.get("name", "")
                if _match_target(http_target, span_name):
                     matches = True
                     break
            if matches:
                filtered_traces.append(t)
        traces = filtered_traces

    if execution_run_id and traces:
        filtered = [
            t
            for t in traces
            if any(
                s.get("attributes", {}).get("execution.run_id") == execution_run_id
                for s in t.get("spans", [])
            )
        ]
        traces = filtered if filtered else traces

    # Compact traces to stay within prompt limits
    compacted_traces = compact_traces(traces, limit=limit)
    summary = summarize_counts(len(traces), len(compacted_traces), "traces")

    result_data = {
        "source": "grafana_tempo",
        "available": True,
        "traces": compacted_traces,
        "pipeline_spans": _extract_pipeline_spans(compacted_traces),
        "total_traces": total_traces,
        "service_name": service_name,
        "trace_id": trace_id,
        "http_method": http_method,
        "http_target": http_target,
        "execution_run_id": execution_run_id,
        "account_id": client.account_id,
    }
    if summary:
        result_data["truncation_note"] = summary
    return result_data
