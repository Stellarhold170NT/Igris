"""Report structurer — LLM-based structuring of investigation + snapshot into SreReport."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm_client import get_llm_for_reasoning
from app.utils.pdf_generator import SnapshotSection, SnapshotTable, SreReport

logger = logging.getLogger(__name__)


def _extract_str_list(data: Any) -> list[str]:
    out: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                out.append(str(item.get("claim") or item.get("content") or item))
            else:
                out.append(str(item))
    elif isinstance(data, dict):
        out.append(str(data.get("claim") or data.get("content") or data))
    elif data:
        out.append(str(data))
    return out


def _build_prompt(investigation_state: dict[str, Any], snapshot_md: str, language: str) -> str:
    lang_label = "Vietnamese" if language in ("vi", "vietnamese") else "English"

    safe_state = {
        "task_id": investigation_state.get("task_id", ""),
        "source": investigation_state.get("source", ""),
        "pipeline_name": investigation_state.get("pipeline_name", ""),
        "severity": investigation_state.get("severity", ""),
        "root_cause": investigation_state.get("root_cause", ""),
        "root_cause_category": investigation_state.get("root_cause_category", ""),
        "report": investigation_state.get("report", "")[:2000],
        "validated_claims": _extract_str_list(investigation_state.get("validated_claims")),
        "non_validated_claims": _extract_str_list(investigation_state.get("non_validated_claims")),
        "investigation_recommendations": _extract_str_list(investigation_state.get("investigation_recommendations")),
        "remediation_steps": _extract_str_list(investigation_state.get("remediation_steps")),
        "evidence_entries": _extract_str_list(investigation_state.get("evidence_entries")),
    }

    return (
        f"You are an expert SRE report writer. Structure the following investigation results "
        f"and snapshot data into a formal SRE incident report in {lang_label}.\n\n"
        f"INVESTIGATION STATE:\n{json.dumps(safe_state, indent=2, ensure_ascii=False, default=str)[:4000]}\n\n"
        f"SNAPSHOT DATA:\n{snapshot_md[:4000]}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Produce valid JSON matching the SreReport schema.\n"
        f"2. All text fields must be in {lang_label}.\n"
        f"3. incident_id: use task_id or alert_id from state.\n"
        f"4. alert_name: use source or alert title.\n"
        f"5. pipeline_service: use pipeline_name or service name.\n"
        f"6. severity: use severity from state (WARNING, CRITICAL, etc.).\n"
        f"7. executive_summary: 2-4 sentences summarizing the incident.\n"
        f"8. validated_findings: list of confirmed observations.\n"
        f"9. non_validated_claims: list of hypotheses needing verification.\n"
        f"10. snapshot_sections: each Coral SQL query + its results as a table.\n"
        f"11. recommended_actions: actionable remediation steps.\n"
        f"12. cited_evidence: list of data sources queried.\n"
        f"Return ONLY valid JSON, no markdown fences."
    )


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("\n", 1)[0]
    result: Any = json.loads(text.strip())
    return result if isinstance(result, dict) else {}


def structure_report(
    investigation_state: dict[str, Any],
    snapshot_result: dict[str, Any],
    language: str = "en",
) -> SreReport:
    """Structure investigation + snapshot into SreReport via LLM.

    Falls back to manual extraction if LLM fails.
    """
    snapshot_md = snapshot_result.get("markdown_summary", "") if isinstance(snapshot_result, dict) else ""
    prompt = _build_prompt(investigation_state, snapshot_md, language)

    try:
        llm = get_llm_for_reasoning()
        response = llm.invoke([{"role": "user", "content": prompt}])
        content = response.content if hasattr(response, "content") else str(response)
        data = _parse_llm_json(content)
        return SreReport.model_validate(data)
    except Exception as exc:
        logger.warning("LLM report structuring failed: %s", exc)
        return _fallback_report(investigation_state, snapshot_result, language)


def _fallback_report(
    investigation_state: dict[str, Any],
    snapshot_result: dict[str, Any],
    language: str,
) -> SreReport:
    is_vi = language in ("vi", "vietnamese")
    return SreReport(
        incident_id=investigation_state.get("task_id", "unknown"),
        alert_name=investigation_state.get("source", ""),
        pipeline_service=investigation_state.get("pipeline_name", ""),
        severity=investigation_state.get("severity", "WARNING"),
        investigation_duration="",
        status="ĐÃ XỬ LÝ & GHI NHẬN" if is_vi else "RESOLVED & CAPTURED",
        executive_summary=investigation_state.get("report", "")[:800],
        validated_findings=_extract_str_list(investigation_state.get("validated_claims")),
        non_validated_claims=_extract_str_list(investigation_state.get("non_validated_claims")),
        snapshot_sections=_build_snapshot_sections(snapshot_result),
        recommended_actions=_extract_str_list(
            investigation_state.get("investigation_recommendations", [])
            + investigation_state.get("remediation_steps", [])
        ),
        cited_evidence=_extract_str_list(investigation_state.get("evidence_entries", [])),
    )


def _build_snapshot_sections(snapshot_result: Any) -> list[SnapshotSection]:
    if not isinstance(snapshot_result, dict):
        return []
    queries = snapshot_result.get("queries", [])
    results = snapshot_result.get("results", [])
    sections: list[SnapshotSection] = []
    for query, result in zip(queries, results):
        rows: list[list[str]] = []
        headers: list[str] = []
        if isinstance(result, dict) and result.get("ok") and isinstance(result.get("data"), list):
            data = result["data"]
            if data and isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [[str(row.get(h, "")) for h in headers] for row in data]
        sections.append(SnapshotSection(
            title="Snapshot Query",
            sql_query=str(query),
            description="",
            table=SnapshotTable(headers=headers, rows=rows) if headers else None,
        ))
    return sections
