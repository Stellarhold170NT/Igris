"""Per-kind message builders for scheduled tasks.

Each task kind maps to a function that produces a formatted report string
suitable for delivery to messaging providers.

The daily_summary, weekly_audit, and synthetic_run kinds query the real
investigation pipeline to produce data-driven reports. When the pipeline
returns no findings, a clear quiet-period message is delivered. Pipeline
failures raise RuntimeError so the executor records FAILED in cron logs
without masking outages as success.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.scheduler.types import ScheduledTask, SkipDeliveryException, TaskKind

logger = logging.getLogger(__name__)

# Keys that should never be forwarded to the investigation pipeline
_CREDENTIAL_KEYS = frozenset({"bot_token", "access_token", "api_key", "webhook_url", "secret"})


def _evaluate_notification_condition(report_text: str, condition: str) -> bool:
    """Use the reasoning LLM to evaluate if the report text matches SRE's notification condition."""
    try:
        from pydantic import BaseModel, Field

        from app.services import get_llm_for_reasoning

        class DeliveryDecision(BaseModel):
            should_deliver: bool = Field(
                description="True if the report matches the SRE condition/policy, False otherwise."
            )
            reason: str = Field(
                description="Brief explanation of why the report was approved or skipped."
            )

        llm = get_llm_for_reasoning()

        prompt = (
            "You are an SRE notification gatekeeper.\n"
            "An investigation report has been generated. The user has specified a condition/policy (which may be in Vietnamese or English) for sending notifications to external channels.\n\n"
            f'User Condition:\n"{condition}"\n\n'
            f"Investigation Report:\n{report_text}\n\n"
            "Guidelines to evaluate the condition:\n"
            "1. Identify the core intent of the User Condition:\n"
            "   - 'Always send' / 'Regardless of errors' (Vietnamese words like: 'dù', 'ngay cả khi', 'bất kể', 'luôn gửi', 'dù không có lỗi', 'dù không lỗi', 'dù có hay không'): The user wants to receive this report even if everything is healthy.\n"
            "   - 'Only send on failure/mismatch' (Vietnamese words like: 'gửi khi có lỗi', 'chỉ khi', 'nếu có lệch', 'khi lệch', 'chỉ khi lệch'): The user ONLY wants to be alerted if something is wrong.\n\n"
            "2. Analyze the Investigation Report:\n"
            "   - Does it indicate a failure, synchronization mismatch, lag, or error? (Look for mismatch details, error logs, or connection failures).\n\n"
            "3. Make the Decision:\n"
            "   - If the report has failures/mismatches -> set should_deliver to true.\n"
            "   - If the report indicates everything is healthy/synchronized:\n"
            "     - If the User Condition specifies sending anyway/regardless/even if healthy (e.g., 'dù không có lỗi') -> set should_deliver to true.\n"
            "     - If the User Condition restricts notifications to failures only (e.g. only notify on errors) -> set should_deliver to false.\n"
            "     - If the condition is empty, unclear, or asks for always notifying -> set should_deliver to true."
        )

        decision = (
            llm.with_structured_output(DeliveryDecision)
            .with_config(run_name="LLM – Evaluate Scheduler Notification Condition")
            .invoke(prompt)
        )

        logger.info(
            "Notification condition evaluation: should_deliver=%s, reason=%s",
            decision.should_deliver,
            decision.reason,
        )
        return decision.should_deliver
    except Exception as exc:
        logger.warning(
            "Failed to evaluate notification condition via LLM, defaulting to True: %s", exc
        )
        return True


def build_message(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Build the report message for a scheduled task based on its kind.

    Returns a tuple of (formatted message string, resolved integrations dict).
    Raises RuntimeError on unrecoverable pipeline failures.
    """
    builders = {
        TaskKind.DAILY_SUMMARY: _build_daily_summary,
        TaskKind.WEEKLY_AUDIT: _build_weekly_audit,
        TaskKind.INCIDENT_WINDOW_REPLAY: _build_incident_window_replay,
        TaskKind.SYNTHETIC_RUN: _build_synthetic_run,
        TaskKind.CUSTOM_INVESTIGATION: _build_custom_investigation,
    }
    builder = builders.get(task.kind)
    if builder is None:
        return f"⚠️ Unknown task kind: {task.kind}", {}

    message, resolved = builder(task)

    condition = task.params.get("condition")
    if condition and condition.strip() and message:
        should_deliver = _evaluate_notification_condition(message, condition)
        if not should_deliver:
            raise SkipDeliveryException(
                f"Notification policy '{condition}' not met.", resolved_integrations=resolved
            )

    return message, resolved


def _build_daily_summary(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Build a daily reliability digest by running the investigation pipeline.

    Queries the pipeline with a 'daily_summary' source over the configured
    window. Returns the pipeline report if available.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=task.window_hours)
    resolved: dict[str, Any] = {}

    try:
        from app.pipeline.runners import run_investigation

        alert_payload = {
            "source": "scheduled_daily_summary",
            "task_id": task.id,
            "window_hours": task.window_hours,
            "kind": task.kind.value,
            "window_start": window_start.isoformat(),
            "window_end": now.isoformat(),
        }
        result = run_investigation(alert_payload)
        if result:
            resolved = result.get("resolved_integrations") or {}
            resolved["_severity"] = result.get("severity") or "healthy"
            if result.get("telegram_message"):
                resolved["_telegram_message"] = result["telegram_message"]
            if result.get("report"):
                return str(result["report"]), resolved
        # Pipeline ran successfully but returned no report — genuinely quiet
    except Exception as exc:
        logger.error("Daily summary pipeline query failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Daily summary failed for task {task.id}. Check logs for details."
        ) from exc

    resolved["_severity"] = "healthy"
    return (
        f"📊 <b>Daily Reliability Summary</b>\n\n"
        f"Period: {window_start.strftime('%Y-%m-%d %H:%M')} → "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"Window: {task.window_hours}h\n\n"
        f"✅ No active incidents detected in the monitoring window.\n\n"
        f"<i>Generated by OpenSRE scheduled delivery</i>",
        resolved,
    )


def _build_weekly_audit(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Build a weekly noisy-alert audit by running the investigation pipeline.

    Queries the pipeline with a 'weekly_audit' source over the configured
    window.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=task.window_hours)
    resolved: dict[str, Any] = {}

    try:
        from app.pipeline.runners import run_investigation

        alert_payload = {
            "source": "scheduled_weekly_audit",
            "task_id": task.id,
            "window_hours": task.window_hours,
            "kind": task.kind.value,
            "window_start": window_start.isoformat(),
            "window_end": now.isoformat(),
        }
        result = run_investigation(alert_payload)
        if result:
            resolved = result.get("resolved_integrations") or {}
            resolved["_severity"] = result.get("severity") or "healthy"
            if result.get("telegram_message"):
                resolved["_telegram_message"] = result["telegram_message"]
            if result.get("report"):
                return str(result["report"]), resolved
    except Exception as exc:
        logger.error("Weekly audit pipeline query failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Weekly audit failed for task {task.id}. Check logs for details."
        ) from exc

    resolved["_severity"] = "healthy"
    return (
        f"📋 <b>Weekly Alert Audit</b>\n\n"
        f"Period: {window_start.strftime('%Y-%m-%d')} → "
        f"{now.strftime('%Y-%m-%d')} UTC\n\n"
        f"✅ No noisy or actionable alerts found for the past {task.window_hours}h.\n\n"
        f"<i>Generated by OpenSRE scheduled delivery</i>",
        resolved,
    )


def _build_incident_window_replay(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Build an incident window replay report.

    Attempts to run the investigation pipeline over the configured window.
    """
    resolved: dict[str, Any] = {}
    try:
        from app.pipeline.runners import run_investigation

        alert_payload = {
            "source": "scheduled_replay",
            "task_id": task.id,
            "window_hours": task.window_hours,
            "kind": task.kind.value,
        }
        result = run_investigation(alert_payload)
        if result:
            resolved = result.get("resolved_integrations") or {}
            resolved["_severity"] = result.get("severity") or "warning"
            if result.get("telegram_message"):
                resolved["_telegram_message"] = result["telegram_message"]
            if result.get("report"):
                return str(result["report"]), resolved
        resolved["_severity"] = "healthy"
        return (
            f"🔄 <b>Incident Window Replay</b>\n\n"
            f"Window: {task.window_hours}h\n"
            f"No incidents found in replay window.\n\n"
            f"<i>Generated by OpenSRE scheduled delivery</i>",
            resolved,
        )
    except Exception as exc:
        logger.error("Incident window replay failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Incident window replay failed for task {task.id}. Check logs for details."
        ) from exc


def _build_synthetic_run(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Build a synthetic test run summary by executing the synthetic suite.

    Runs the synthetic test suite and reports results.
    """
    now = datetime.now(UTC)
    resolved: dict[str, Any] = {}

    try:
        from app.pipeline.runners import run_investigation

        alert_payload = {
            "source": "scheduled_synthetic",
            "task_id": task.id,
            "kind": task.kind.value,
            "window_hours": task.window_hours,
        }
        result = run_investigation(alert_payload)
        if result:
            resolved = result.get("resolved_integrations") or {}
            resolved["_severity"] = result.get("severity") or "healthy"
            if result.get("telegram_message"):
                resolved["_telegram_message"] = result["telegram_message"]
            if result.get("report"):
                return str(result["report"]), resolved
    except Exception as exc:
        logger.error("Synthetic run failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Synthetic run failed for task {task.id}. Check logs for details."
        ) from exc

    resolved["_severity"] = "healthy"
    return (
        f"🧪 <b>Synthetic Test Summary</b>\n\n"
        f"Run time: {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
        f"No synthetic test results available.\n"
        f"Configure synthetic probes to see results here.\n\n"
        f"<i>Generated by OpenSRE scheduled delivery</i>",
        resolved,
    )


def _build_custom_investigation(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    """Run a custom investigation and return the report.

    On failure, raises RuntimeError so the executor records the failure.
    """
    resolved: dict[str, Any] = {}
    try:
        from app.pipeline.runners import run_investigation

        # Strip credential keys before passing params to the pipeline
        safe_params = {k: v for k, v in task.params.items() if k not in _CREDENTIAL_KEYS}

        # Resolve any skill references in safe_params
        from app.cli.commands.skill import load_skills, resolve_skills_in_text

        skills = load_skills()
        if skills:
            # First, check if there is a 'skill' parameter that needs direct lookup
            skill_val = safe_params.get("skill")
            if isinstance(skill_val, str):
                skill_name = skill_val.lstrip("@")
                if skill_name in skills:
                    skill_data = skills[skill_name]
                    resolved_prompt = (
                        skill_data.get("prompt") if isinstance(skill_data, dict) else skill_data
                    )
                    safe_params["skill"] = resolved_prompt

            # Second, resolve any remaining @skill references in any of the safe_params
            for k, v in safe_params.items():
                if isinstance(v, str) and "@" in v:
                    safe_params[k] = resolve_skills_in_text(v)
        alert_payload = {
            "source": "scheduled_custom",
            "task_id": task.id,
            "window_hours": task.window_hours,
            "kind": task.kind.value,
            **safe_params,
        }
        result = run_investigation(alert_payload)
        if result:
            resolved = result.get("resolved_integrations") or {}
            resolved["_severity"] = result.get("severity") or "warning"
            if result.get("telegram_message"):
                resolved["_telegram_message"] = result["telegram_message"]

            snapshot_instruction = safe_params.get("snapshot_instruction")
            if snapshot_instruction:
                resolved["_snapshot_instruction"] = snapshot_instruction
                resolved["_investigation_state"] = dict(result)

            if result.get("report"):
                return str(result["report"]), resolved
        resolved["_severity"] = "healthy"
        return (
            f"🔍 <b>Custom Investigation</b>\n\n"
            f"Task: {task.id}\n"
            f"No findings from custom investigation.\n\n"
            f"<i>Generated by OpenSRE scheduled delivery</i>",
            resolved,
        )
    except Exception as exc:
        logger.error("Custom investigation failed for task %s: %s", task.id, exc)
        raise RuntimeError(
            f"Custom investigation failed for task {task.id}. Check logs for details."
        ) from exc


__all__ = ["build_message"]
