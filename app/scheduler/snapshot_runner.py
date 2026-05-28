"""Snapshot runner — ReAct loop using only coral_query for system state capture."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.agent.context import resolve_integrations
from app.services.agent_llm_client import get_agent_llm
from app.tools.registry import get_registered_tools

logger = logging.getLogger(__name__)
_MAX_SNAPSHOT_ITERATIONS = int(os.environ.get("OPENSRE_SNAPSHOT_MAX_ITERATIONS", "5"))


@dataclass
class SnapshotResult:
    queries: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    markdown_summary: str = ""
    errors: list[str] = field(default_factory=list)


def _build_system_prompt(instruction: str) -> str:
    return (
        "You are a system-state snapshot agent. Your ONLY tool is coral_query, "
        "which runs SQL against federated data sources (Grafana, Datadog, GitHub, etc.).\n\n"
        "INSTRUCTION:\n" + instruction + "\n\n"
        "Rules:\n"
        "1. Use ONLY the coral_query tool.\n"
        "2. Run discovery first if schema is unknown: SELECT * FROM coral.tables LIMIT 20;\n"
        "3. Then run targeted queries with LIMIT.\n"
        "4. When you have captured enough state, stop.\n"
        "5. Do not explain your reasoning — only run queries."
    )


def run_snapshot(
    instruction: str,
    resolved: dict[str, dict] | None = None,
) -> SnapshotResult:
    llm = get_agent_llm()
    resolved_integrations = resolved or resolve_integrations({"raw_alert": {}})

    chat_tools = {t.name: t for t in get_registered_tools("chat")}
    coral_tool = chat_tools.get("coral_query")
    if not coral_tool:
        return SnapshotResult(errors=["coral_query tool not available"])

    if not coral_tool.is_available(resolved_integrations):
        return SnapshotResult(
            errors=["coral_query tool not available (CORAL_ENABLED not set or binary missing)"]
        )

    tool_map = {"coral_query": coral_tool}
    tool_schemas = llm.tool_schemas([coral_tool])

    system = _build_system_prompt(instruction)
    messages: list[dict[str, Any]] = []

    queries: list[str] = []
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for _iteration in range(_MAX_SNAPSHOT_ITERATIONS):
        try:
            response = llm.invoke(messages, system=system, tools=tool_schemas)
        except Exception as exc:
            errors.append(f"LLM invoke error: {exc}")
            break

        if not response.has_tool_calls:
            break

        tool_results: list[Any] = []
        for tc in response.tool_calls:
            tool = tool_map.get(tc.name)
            if tool is None:
                errors.append(f"Unknown tool: {tc.name}")
                tool_results.append({"error": f"unknown tool: {tc.name}"})
                continue

            try:
                injected = tool.extract_params(resolved_integrations)
                kwargs = {**injected, **tc.input}
                output = tool.run(**kwargs)
            except Exception as exc:
                output = {"error": str(exc)}
                errors.append(f"coral_query error: {exc}")

            if tc.input.get("sql"):
                queries.append(tc.input["sql"])
            results.append(output)
            tool_results.append(output)

        messages.append(
            llm.build_assistant_message(response.content or "", response.tool_calls)
        )

        if hasattr(llm, "build_tool_result_messages"):
            messages.extend(llm.build_tool_result_messages(response.tool_calls, tool_results))
        elif hasattr(llm, "build_tool_result_message"):
            messages.append(llm.build_tool_result_message(response.tool_calls, tool_results))
        else:
            for tc, output in zip(response.tool_calls, tool_results):
                messages.append({
                    "role": "user",
                    "content": json.dumps({"tool_call_id": tc.id, "output": output}),
                })

    parts: list[str] = ["# Coral Query State Capture (System Snapshot)\n"]
    for query, result in zip(queries, results):
        parts.append(f"```sql\n{query}\n```")
        if result.get("ok") and result.get("data"):
            parts.append("```json")
            try:
                parts.append(json.dumps(result["data"], indent=2, ensure_ascii=False, default=str)[:2000])
            except (ValueError, TypeError):
                parts.append(str(result["data"])[:2000])
            parts.append("```")
        elif result.get("error"):
            parts.append(f"Error: {result['error']}")
        else:
            parts.append("No data returned.")
        parts.append("")

    return SnapshotResult(
        queries=queries,
        results=results,
        markdown_summary="\n".join(parts),
        errors=errors,
    )
