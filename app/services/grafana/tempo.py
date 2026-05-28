"""Tempo trace query mixin for Grafana Cloud client."""

from __future__ import annotations

import logging
import datetime
from typing import TYPE_CHECKING, Any

import requests

from app.utils.errors import report_exception

if TYPE_CHECKING:
    from app.services.grafana.base import GrafanaClientBase

logger = logging.getLogger(__name__)


class TempoMixin:
    """Mixin providing Tempo trace query capabilities."""

    def query_tempo(  # type: ignore[misc]
        self: GrafanaClientBase,
        service_name: str,
        limit: int = 20,
        q: str | None = None,
    ) -> dict[str, Any]:
        """Query Grafana Cloud Tempo for traces.

        Args:
            service_name: Service name to filter traces
            limit: Maximum number of traces to return
            q: Custom TraceQL query string to bypass default service search

        Returns:
            Dictionary with traces and span details
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": f"Grafana client not configured for account '{self.account_id}'",
                "traces": [],
            }

        url = self._build_datasource_url(
            self.tempo_datasource_uid,
            "/api/search",
        )

        params: dict[str, str] = {
            "q": q or f'{{.service.name="{service_name}"}}',
            "limit": str(limit),
        }

        try:
            data = self._make_request(url, params=params)
            traces = data.get("traces", [])

            enriched_traces = []
            for trace in traces:
                trace_id = trace.get("traceID", "")
                span_details = self._get_trace_details(trace_id)  # type: ignore[attr-defined]

                # Convert startTimeUnixNano to human readable time
                start_time_nano = trace.get("startTimeUnixNano")
                start_time_str = ""
                if start_time_nano:
                    try:
                        dt = datetime.datetime.fromtimestamp(
                            float(start_time_nano) / 1e9, tz=datetime.timezone.utc
                        )
                        start_time_str = dt.isoformat()
                    except Exception:
                        start_time_str = str(start_time_nano)

                enriched_traces.append(
                    {
                        "trace_id": trace_id,
                        "root_service": trace.get("rootServiceName", ""),
                        "duration_ms": trace.get("durationMs", 0),
                        "span_count": trace.get("spanCount", 0),
                        "start_time": start_time_str,
                        "spans": span_details.get("spans", []),
                    }
                )

            return {
                "success": True,
                "traces": enriched_traces,
                "total_traces": len(traces),
                "service_name": service_name,
                "account_id": self.account_id,
            }
        except Exception as e:
            error_msg = str(e)
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                response_text = e.response.text[:300]
                error_msg = f"Tempo query failed: {e.response.status_code}"

            return {
                "success": False,
                "error": error_msg,
                "response": response_text,
                "traces": [],
            }

    def _get_trace_details(  # type: ignore[misc]
        self: GrafanaClientBase,
        trace_id: str,
    ) -> dict[str, Any]:
        """Get detailed span information for a trace.

        Args:
            trace_id: The trace ID to fetch details for

        Returns:
            Dictionary with spans list
        """
        url = self._build_datasource_url(
            self.tempo_datasource_uid,
            f"/api/traces/{trace_id}",
        )

        try:
            response = requests.get(
                url,
                headers=self._get_auth_headers(),
                timeout=10,
            )

            if response.status_code == 200:
                trace_data = response.json()
                spans = []

                if "batches" in trace_data:
                    for batch in trace_data["batches"]:
                        if "scopeSpans" in batch:
                            for scope in batch["scopeSpans"]:
                                if "spans" in scope:
                                    for span in scope["spans"]:
                                        attributes = self._extract_span_attributes(span)  # type: ignore[attr-defined]

                                        # Convert span start time
                                        span_start = span.get("startTimeUnixNano")
                                        span_start_str = ""
                                        if span_start:
                                            try:
                                                dt = datetime.datetime.fromtimestamp(
                                                    float(span_start) / 1e9,
                                                    tz=datetime.timezone.utc,
                                                )
                                                span_start_str = dt.isoformat()
                                            except Exception:
                                                span_start_str = str(span_start)

                                        # Calculate span duration in ms
                                        start_nano = span.get("startTimeUnixNano")
                                        end_nano = span.get("endTimeUnixNano")
                                        duration_ms = 0.0
                                        if start_nano and end_nano:
                                            try:
                                                duration_ms = (
                                                    int(end_nano) - int(start_nano)
                                                ) / 1e6
                                            except Exception:
                                                pass

                                        spans.append(
                                            {
                                                "name": span.get("name", "unknown"),
                                                "start_time": span_start_str,
                                                "duration_ms": duration_ms,
                                                "attributes": attributes,
                                            }
                                        )

                return {"spans": spans}
        except Exception as exc:
            report_exception(
                exc,
                logger=logger,
                message="Failed to fetch Tempo trace spans",
                severity="warning",
                tags={
                    "surface": "service_client",
                    "integration": "grafana",
                    "component": "app.services.grafana.tempo",
                },
                extras={"trace_id": trace_id},
            )
            return {"spans": []}

        return {"spans": []}

    def _extract_span_attributes(  # type: ignore[misc]
        self: GrafanaClientBase,
        span: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract attributes from a span.

        Args:
            span: Span data dictionary

        Returns:
            Dictionary of attribute key-value pairs
        """
        attributes: dict[str, Any] = {}

        if "attributes" in span:
            for attr in span["attributes"]:
                key = attr.get("key", "")
                if not key:
                    continue
                value = attr.get("value", {})

                if "stringValue" in value:
                    attributes[key] = value["stringValue"]
                elif "intValue" in value:
                    attributes[key] = value["intValue"]

        return attributes
