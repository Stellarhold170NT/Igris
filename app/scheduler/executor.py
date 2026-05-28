"""Task execution with claim-based dedup and per-provider delivery."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

from app.scheduler.claim_store import complete_run, try_claim
from app.scheduler.credentials import (
    resolve_discord_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
)
from app.scheduler.tasks import build_message
from app.scheduler.types import Provider, ScheduledTask, SkipDeliveryException, TaskStatus

logger = logging.getLogger(__name__)

# Strip HTML tags for providers that don't support them
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def execute_task(
    task: ScheduledTask,
    fire_time: str,
) -> bool:
    """Execute a scheduled task with claim-based dedup.

    Args:
        task: The scheduled task definition.
        fire_time: The canonical fire time string (UTC, minute-precision) from the
            scheduler trigger, used as the dedup key.

    Returns:
        True if the task was executed and delivered successfully.
        False if the claim was lost (another instance handled it) or delivery failed.
    """
    # Attempt to claim this execution slot
    if not try_claim(task.id, fire_time):
        logger.info(
            "Task %s fire_time=%s already claimed by another instance",
            task.id,
            fire_time,
        )
        return False

    logger.info("Executing task %s (kind=%s, fire_time=%s)", task.id, task.kind, fire_time)
    _emit_analytics_started(task)

    # Build the message
    resolved_integrations: dict[str, Any] = {}
    try:
        message, resolved_integrations = build_message(task)
    except SkipDeliveryException as exc:
        resolved_integrations = exc.resolved_integrations
        # Mark the task run status as SKIPPED since delivery is bypassed based on condition, but task ran successfully.
        complete_run(
            task.id,
            fire_time,
            status=TaskStatus.SKIPPED,
            posted_message_id=f"skipped:{exc.reason}",
            provider=task.provider.value,
        )
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s completed but skipped delivery: %s", task.id, exc.reason)

        sys.stderr.write(
            f"\n[Notification Gatekeeper] AI Decision: Skip delivery -> {exc.reason}\n"
        )
        _log_task_channels(task, skipped=True)
        sys.stderr.write("\n")

        return True
    except RuntimeError as exc:
        # Pipeline failures — record without leaking details to chat
        _record_failure(task, fire_time, str(exc))
        return False
    except Exception as exc:
        _record_failure(task, fire_time, f"Message build error: {type(exc).__name__}")
        return False

    # --- Snapshot + PDF Report phase ---
    pdf_path = _maybe_generate_pdf_report(task, resolved_integrations)
    if pdf_path:
        resolved_integrations["_pdf_path"] = str(pdf_path)

    # --- Delivery phase: only send to channels configured in the task ---
    sys.stderr.write(
        "\n[Notification Gatekeeper] AI Decision: Approved delivery -> Condition met.\n"
    )

    # 1. Chat channel delivery
    chat_ok, chat_error, message_id = _deliver(task, message, resolved_integrations)
    if chat_ok:
        sys.stderr.write(
            f"[Notification Gatekeeper] Chat channel '{task.provider.value.upper()}' -> Delivered to '{task.chat_id}' (id={message_id})\n"
        )
    elif task.chat_id:
        sys.stderr.write(
            f"[Notification Gatekeeper] Chat channel '{task.provider.value.upper()}' -> FAILED: {chat_error}\n"
        )
    else:
        sys.stderr.write(
            f"[Notification Gatekeeper] Chat channel '{task.provider.value.upper()}' -> Skipped (no chat_id configured)\n"
        )

    # 2. Trello delivery (task management)
    trello_ok = False
    if task.trello_board_id:
        trello_ok, trello_error = _deliver_trello(task, message, resolved_integrations)
        if trello_ok:
            sys.stderr.write(
                f"[Notification Gatekeeper] Trello -> Card created on board '{task.trello_board_id}'\n"
            )
        else:
            sys.stderr.write(f"[Notification Gatekeeper] Trello -> FAILED: {trello_error}\n")
    else:
        sys.stderr.write(
            "[Notification Gatekeeper] Trello -> Skipped (no trello_board_id configured)\n"
        )

    sys.stderr.write("\n")

    # At least one channel must succeed
    any_ok = chat_ok or trello_ok
    if any_ok:
        complete_run(
            task.id,
            fire_time,
            status=TaskStatus.SUCCESS,
            posted_message_id=message_id or "trello_only",
            provider=task.provider.value,
        )
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s delivered successfully", task.id)
        return True
    else:
        error = chat_error or "No delivery channel configured or all failed"
        _record_failure(task, fire_time, error)
        return False


def _log_task_channels(task: ScheduledTask, *, skipped: bool) -> None:
    """Log the task-configured channels and their suppression status."""
    action = "suppressed" if skipped else "ready"
    if task.chat_id:
        sys.stderr.write(
            f"[Notification Gatekeeper] Chat '{task.provider.value.upper()}' (chat_id: '{task.chat_id}') -> {action}\n"
        )
    else:
        sys.stderr.write(
            f"[Notification Gatekeeper] Chat '{task.provider.value.upper()}' -> not configured (no chat_id)\n"
        )
    if task.trello_board_id:
        sys.stderr.write(
            f"[Notification Gatekeeper] Trello (board: '{task.trello_board_id}') -> {action}\n"
        )
    else:
        sys.stderr.write("[Notification Gatekeeper] Trello -> not configured\n")


def _deliver(
    task: ScheduledTask,
    message: str,
    resolved_integrations: dict[str, Any],
) -> tuple[bool, str, str]:
    """Route delivery to the appropriate provider.

    Returns (success, error, message_id).
    """
    if not task.chat_id:
        return False, "No chat_id configured", ""
    if task.provider == Provider.TELEGRAM:
        return _deliver_telegram(task, message, resolved_integrations)
    elif task.provider == Provider.SLACK:
        return _deliver_slack(task, message)
    elif task.provider == Provider.DISCORD:
        return _deliver_discord(task, message)
    else:
        return False, f"Unsupported provider: {task.provider}", ""


def _deliver_trello(
    task: ScheduledTask,
    message: str,
    resolved_integrations: dict[str, Any],
) -> tuple[bool, str]:
    """Create a Trello card for the investigation report.

    Credentials are resolved from resolved_integrations or environment variables.
    The target list is auto-resolved from the board using the pipeline_name.

    Returns (success, error).
    """
    import os

    # Resolve Trello credentials
    trello_int = resolved_integrations.get("trello")
    if trello_int and isinstance(trello_int, dict):
        api_key = (
            trello_int.get("api_key") or trello_int.get("credentials", {}).get("api_key") or ""
        )
        token = trello_int.get("token") or trello_int.get("credentials", {}).get("token") or ""
    else:
        api_key = ""
        token = ""

    api_key = api_key or os.getenv("TRELLO_API_KEY", "")
    token = token or os.getenv("TRELLO_TOKEN", "")

    if not api_key or not token:
        return False, "Missing Trello API key or token"

    try:
        from app.integrations.trello import (
            build_trello_config,
            create_trello_card,
            create_trello_list,
            get_trello_board,
            get_trello_board_lists,
        )

        config = build_trello_config(
            {
                "api_key": api_key,
                "token": token,
                "board_id": task.trello_board_id,
            }
        )

        # Resolve the target list on the board
        pipeline_name = task.params.get("pipeline_name") or "Scheduled Investigations"
        try:
            board_info = get_trello_board(config=config, board_id=task.trello_board_id)
            long_board_id = board_info.get("id") or task.trello_board_id
        except Exception:
            long_board_id = task.trello_board_id

        lists = get_trello_board_lists(config=config, board_id=long_board_id)
        matched_list = next(
            (
                lst
                for lst in lists
                if lst.get("name", "").strip().lower() == pipeline_name.strip().lower()
            ),
            None,
        )
        if matched_list:
            list_id = matched_list["id"]
        else:
            new_list = create_trello_list(config=config, board_id=long_board_id, name=pipeline_name)
            list_id = new_list.get("id")

        if not list_id:
            return False, "Could not resolve or create a list on the Trello board"

        # Build card content
        severity = (resolved_integrations.get("_severity") or "warning").lower()
        severity_emoji = {
            "critical": "🔴",
            "crit": "🔴",
            "high": "🟠",
            "error": "🟠",
            "medium": "🟡",
            "warning": "🟡",
            "warn": "🟡",
            "low": "🟢",
            "info": "🟢",
            "none": "⚪",
            "healthy": "🟢",
            "normal": "🟢",
        }.get(severity, "⚠️")
        card_name = (
            f"{severity_emoji} [{task.kind.value}] {task.params.get('pipeline_name', task.id)}"
        )
        # Convert HTML to plain text for Trello
        card_desc = _strip_html(message)

        card = create_trello_card(
            config=config,
            name=card_name,
            desc=card_desc,
            list_id=list_id,
        )
        card_id = card.get("id")
        logger.info("[executor] Trello card created: %s (ID: %s)", card.get("name"), card_id)

        pdf_path = resolved_integrations.get("_pdf_path")
        if pdf_path and card_id:
            from app.integrations.trello import attach_file_to_trello_card

            attach_file_to_trello_card(
                config=config,
                card_id=card_id,
                file_path=str(pdf_path),
                name="SRE Report",
            )

        return True, ""
    except Exception as exc:
        logger.warning("[executor] Trello delivery failed: %s", exc)
        return False, str(exc)


def _strip_html(text: str) -> str:
    """Strip HTML tags for providers that use plain text or Markdown."""
    return _HTML_TAG_RE.sub("", text)


def _deliver_telegram(
    task: ScheduledTask,
    message: str,
    resolved_integrations: dict[str, Any],
) -> tuple[bool, str, str]:
    """Deliver via Telegram using the truncation helper then posting directly.

    Uses truncate_for_telegram_html to respect the 4096-char limit, then
    posts via post_telegram_message (no reply_to — new top-level message).
    """
    creds = resolve_telegram_credentials(task.params)
    bot_token = creds.get("bot_token", "")
    if not bot_token or not task.chat_id:
        return False, "Missing bot_token or chat_id for Telegram", ""

    from app.utils.telegram_delivery import post_telegram_message, truncate_for_telegram_html

    # Check if a specialized Telegram HTML report is already available
    telegram_html = resolved_integrations.get("_telegram_message")
    if not telegram_html:
        from app.remote.telegram_bot import _format_chat_response

        telegram_html = _format_chat_response(message)

    truncated = truncate_for_telegram_html(telegram_html, 4096, suffix="…")
    ok, error, msg_id = post_telegram_message(task.chat_id, truncated, bot_token, parse_mode="HTML")
    if not ok:
        return False, error, ""

    pdf_path = resolved_integrations.get("_pdf_path")
    if pdf_path:
        from app.utils.telegram_delivery import send_telegram_document

        doc_ok, doc_err, _ = send_telegram_document(
            task.chat_id,
            str(pdf_path),
            bot_token,
            caption="SRE Report PDF",
            reply_to_message_id=msg_id,
        )
        if not doc_ok:
            logger.warning("[executor] Telegram PDF attachment failed: %s", doc_err)

    return True, "", msg_id


def _deliver_slack(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Slack using direct chat.postMessage (no thread_ts needed).

    Scheduled deliveries start a new top-level message, not a thread reply.
    Falls back to webhook if no access_token is available.
    """
    creds = resolve_slack_credentials(task.params)
    access_token = creds.get("access_token", "")

    # Strip HTML tags — Slack uses mrkdwn, not HTML
    plain_message = _strip_html(message)

    if access_token and task.chat_id:
        # Direct API post as a new top-level message
        from app.utils.delivery_transport import post_json

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "channel": task.chat_id,
            "text": plain_message,
        }
        response = post_json(
            url="https://slack.com/api/chat.postMessage",
            payload=payload,
            headers=headers,
        )
        if not response.ok:
            return False, f"Slack API error: {response.error}", ""
        if not 200 <= response.status_code < 300:
            error_text = response.text[:200] if response.text else f"HTTP {response.status_code}"
            return False, f"Slack HTTP error: {error_text}", ""
        if response.data.get("ok") is not True:
            error = response.data.get("error", "unknown")
            return False, f"Slack error: {error}", ""
        msg_ts = str(response.data.get("ts", ""))
        return True, "", msg_ts

    # No access_token — cannot deliver to the configured chat_id
    if not task.chat_id:
        return False, "Missing chat_id for Slack delivery", ""
    return (
        False,
        "Scheduled tasks require a Slack bot access_token; webhook delivery is not supported",
        "",
    )


def _deliver_discord(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Discord using the existing report helper (handles embed truncation)."""
    creds = resolve_discord_credentials(task.params)
    bot_token = creds.get("bot_token", "")
    if not bot_token or not task.chat_id:
        return False, "Missing bot_token or channel_id for Discord", ""

    from app.utils.discord_delivery import send_discord_report

    # Strip HTML tags — Discord uses embeds, not HTML
    plain_message = _strip_html(message)

    discord_ctx = {
        "channel_id": task.chat_id,
        "bot_token": bot_token,
        # No thread_id — scheduled deliveries post to the channel directly
    }
    ok, error = send_discord_report(plain_message, discord_ctx)
    return ok, error, ""


def _record_failure(task: ScheduledTask, fire_time: str, error: str) -> None:
    """Record a failed execution in the claim store and emit analytics."""
    complete_run(
        task.id,
        fire_time,
        status=TaskStatus.FAILED,
        error=error,
        provider=task.provider.value,
    )
    _emit_analytics(task, TaskStatus.FAILED, error=error)
    logger.warning("Task %s failed: %s", task.id, error)


def _emit_analytics_started(task: ScheduledTask) -> None:
    """Emit SCHEDULED_TASK_STARTED event after a claim is won."""
    try:
        from app.analytics.events import Event
        from app.analytics.provider import Properties, get_analytics

        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
        }
        get_analytics().capture(Event.SCHEDULED_TASK_STARTED, properties)
    except Exception:
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def _emit_analytics(task: ScheduledTask, status: TaskStatus, error: str = "") -> None:
    """Emit analytics event for task execution completion."""
    try:
        from app.analytics.events import Event
        from app.analytics.provider import Properties, get_analytics

        event_name = (
            Event.SCHEDULED_TASK_COMPLETED
            if status == TaskStatus.SUCCESS
            else Event.SCHEDULED_TASK_FAILED
        )
        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
            "status": status.value,
        }
        if error:
            properties["error"] = error[:200]
        get_analytics().capture(event_name, properties)
    except Exception:
        # Analytics must never crash the scheduler
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def _maybe_generate_pdf_report(
    task: ScheduledTask,
    resolved_integrations: dict[str, Any],
) -> str | None:
    snapshot_instruction = resolved_integrations.get("_snapshot_instruction")
    investigation_state = resolved_integrations.get("_investigation_state")
    if not snapshot_instruction or not investigation_state:
        return None

    try:
        from app.scheduler.snapshot_runner import run_snapshot
        from app.utils.pdf_generator import generate_pdf
        from app.utils.report_structurer import structure_report

        lang = os.getenv("OPENSRE_LANGUAGE", "en").strip().lower()
        is_vi = lang in ("vi", "vietnamese")

        sys.stderr.write(
            f"[Snapshot] Running snapshot for task {task.id}...\n"
        )
        snapshot_result = run_snapshot(
            instruction=str(snapshot_instruction),
            resolved=resolved_integrations,
        )

        sys.stderr.write(
            f"[Snapshot] Structuring report for task {task.id}...\n"
        )
        report = structure_report(
            investigation_state=investigation_state,
            snapshot_result={
                "queries": snapshot_result.queries,
                "results": snapshot_result.results,
                "markdown_summary": snapshot_result.markdown_summary,
                "errors": snapshot_result.errors,
            },
            language="vi" if is_vi else "en",
        )

        pdf_path = generate_pdf(report)
        sys.stderr.write(
            f"[Snapshot] PDF generated: {pdf_path}\n"
        )
        return str(pdf_path)
    except Exception as exc:
        logger.warning("[Snapshot] PDF generation failed for task %s: %s", task.id, exc)
        sys.stderr.write(f"[Snapshot] PDF generation failed: {exc}\n")
        return None


__all__ = ["execute_task"]
