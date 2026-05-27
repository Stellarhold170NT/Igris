"""Main orchestration node for report generation and publishing."""

import logging
from typing import Any

from app.delivery.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    format_telegram_message,
    format_whatsapp_message,
)
from app.delivery.publish_findings.gitlab_writeback import post_gitlab_mr_writeback
from app.delivery.publish_findings.renderers.editor import open_in_editor
from app.delivery.publish_findings.renderers.terminal import render_report
from app.delivery.publish_findings.report_context import build_report_context
from app.masking import MaskingContext
from app.state import InvestigationState
from app.types.config import NodeConfig
from app.utils.ingest_delivery import create_investigation_and_attach_url
from app.utils.tracing import traceable

logger = logging.getLogger(__name__)


def _get_trello_config(resolved: dict[str, Any]) -> Any | None:
    trello_int = resolved.get("trello")
    if trello_int and isinstance(trello_int, dict):
        creds = trello_int.get("credentials") or {}
        if creds.get("api_key") and creds.get("token"):
            from app.integrations.trello import build_trello_config
            return build_trello_config(creds)

    from app.integrations.trello import trello_config_from_env
    return trello_config_from_env()


def _slack_to_markdown(text: str) -> str:
    """Convert Slack-specific link formatting <url|label> to standard Markdown [label](url)."""
    import re
    # Strip ANSI color and format escape sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"<([^>|]+)\|([^>]+)>", r"[\2](\1)", text)
    text = re.sub(r"<([^>|]+)>", r"[\1](\1)", text)
    return text


def generate_report(state: InvestigationState) -> dict:
    """Generate and publish the final RCA report."""
    from app.utils.slack_delivery import build_action_blocks, send_slack_report

    ctx = build_report_context(state)
    short_summary = state.get("problem_md")
    slack_message = format_slack_message(ctx)

    # Restore any masked infrastructure identifiers in user-facing output.
    # No-op when masking is disabled or the state has no placeholders.
    masking_ctx = MaskingContext.from_state(dict(state))
    slack_message = masking_ctx.unmask(slack_message)
    if isinstance(short_summary, str):
        short_summary = masking_ctx.unmask(short_summary)

    investigation_id, investigation_url = create_investigation_and_attach_url(
        state,
        slack_message,
        short_summary,
    )

    telegram_message = masking_ctx.unmask(format_telegram_message(ctx))
    whatsapp_message = masking_ctx.unmask(format_whatsapp_message(ctx))

    all_blocks = build_slack_blocks(ctx) + build_action_blocks(investigation_url, investigation_id)
    all_blocks = masking_ctx.unmask_value(all_blocks)
    render_report(slack_message, root_cause_category=state.get("root_cause_category"))
    is_scheduled = False
    raw_alert = state.get("raw_alert") or {}
    source = state.get("source") or (raw_alert.get("source") if isinstance(raw_alert, dict) else None)
    if source and isinstance(source, str) and source.startswith("scheduled_"):
        is_scheduled = True
    if state.get("task_id") or (isinstance(raw_alert, dict) and raw_alert.get("task_id")):
        is_scheduled = True

    if not is_scheduled:
        open_in_editor(slack_message)

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    _alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")

    resolved = state.get("resolved_integrations") or {}
    discord_creds = resolved.get("discord", {})
    logger.debug("[publish] slack_ctx=%s", slack_ctx)
    logger.debug(
        "[publish] discord creds present=%s keys=%s",
        bool(discord_creds),
        list(discord_creds.keys()) if discord_creds else [],
    )

    if is_scheduled:
        report_posted, delivery_error = False, "Scheduled runs bypass direct channel publishing"
    else:
        report_posted, delivery_error = send_slack_report(
            slack_message,
            channel=_channel,
            thread_ts=thread_ts,
            access_token=_token,
            blocks=all_blocks,
        )

    logger.debug(
        "[publish] slack delivery: posted=%s channel=%s thread_ts=%s error=%s",
        report_posted,
        _channel,
        thread_ts,
        delivery_error,
    )
    if report_posted and _token and _channel and _alert_ts:
        from app.utils.slack_delivery import swap_reaction

        swap_reaction("eyes", "clipboard", _channel, _alert_ts, _token)
    elif thread_ts and not report_posted and not is_scheduled:
        raise RuntimeError(
            f"[publish] Slack delivery failed: channel={_channel}, thread_ts={thread_ts}, reason={delivery_error}"
        )

    # Discord delivery — uses integration credentials if configured
    if discord_creds and not is_scheduled:
        from app.utils.discord_delivery import send_discord_report

        discord_ctx = state.get("discord_context") or {}
        bot_token = discord_ctx.get("bot_token") or discord_creds.get("bot_token", "")
        channel_id = discord_ctx.get("channel_id") or discord_creds.get("default_channel_id", "")
        thread_id = discord_ctx.get("thread_id", "")
        logger.debug(
            "[publish] discord delivery: channel_id=%s thread_id=%s bot_token_present=%s",
            channel_id,
            thread_id,
            bool(bot_token),
        )
        if bot_token and channel_id:
            discord_posted, discord_error = send_discord_report(
                slack_message,
                {"bot_token": bot_token, "channel_id": channel_id, "thread_id": thread_id},
            )
            logger.debug(
                "[publish] discord delivery: posted=%s error=%s", discord_posted, discord_error
            )
            if not discord_posted:
                logger.warning(
                    "[publish] Discord delivery failed: channel=%s error=%s",
                    channel_id,
                    discord_error,
                )
        else:
            logger.debug(
                "[publish] discord delivery: skipped — bot_token_present=%s channel_id=%s",
                bool(bot_token),
                channel_id,
            )
    else:
        logger.debug("[publish] discord delivery: no discord integration configured or run is scheduled")

    # Telegram delivery — uses integration credentials if configured
    telegram_creds = resolved.get("telegram", {})
    if telegram_creds and not is_scheduled:
        from app.utils.telegram_delivery import send_telegram_report

        telegram_ctx = state.get("telegram_context") or {}
        bot_token = telegram_ctx.get("bot_token") or telegram_creds.get("bot_token", "")
        chat_id = telegram_ctx.get("chat_id") or telegram_creds.get("default_chat_id", "")
        reply_to = str(telegram_ctx.get("reply_to_message_id") or "")
        logger.debug(
            "[publish] telegram delivery: chat_id=%s reply_to=%s bot_token_present=%s",
            chat_id,
            reply_to,
            bool(bot_token),
        )
        if bot_token and chat_id:
            tg_posted, tg_error = send_telegram_report(
                telegram_message,
                {"bot_token": bot_token, "chat_id": chat_id, "reply_to_message_id": reply_to},
            )
            logger.debug("[publish] telegram delivery: posted=%s error=%s", tg_posted, tg_error)
            if not tg_posted:
                logger.warning(
                    "[publish] Telegram delivery failed: chat_id=%s error=%s",
                    chat_id,
                    tg_error,
                )
        else:
            logger.debug(
                "[publish] telegram delivery: skipped — bot_token_present=%s chat_id=%s",
                bool(bot_token),
                chat_id,
            )
    else:
        logger.debug("[publish] telegram delivery: no telegram integration configured or run is scheduled")

    # WhatsApp delivery — uses integration credentials if configured
    whatsapp_creds = resolved.get("whatsapp", {})
    if whatsapp_creds and not is_scheduled:
        from app.utils.whatsapp_delivery import send_whatsapp_report

        _wa_ctx: dict[str, Any] = state.get("whatsapp_context") or {}
        account_sid = _wa_ctx.get("account_sid") or whatsapp_creds.get("account_sid", "")
        auth_token = _wa_ctx.get("auth_token") or whatsapp_creds.get("auth_token", "")
        from_number = _wa_ctx.get("from_number") or whatsapp_creds.get("from_number", "")
        to = _wa_ctx.get("to") or whatsapp_creds.get("default_to", "")
        logger.debug(
            "[publish] whatsapp delivery: to=%s account_sid=%s auth_token_present=%s from_number=%s",
            to,
            account_sid,
            bool(auth_token),
            from_number,
        )
        if account_sid and auth_token and from_number and to:
            wa_posted, wa_error = send_whatsapp_report(
                whatsapp_message,
                {
                    "account_sid": account_sid,
                    "auth_token": auth_token,
                    "from_number": from_number,
                    "to": to,
                },
            )
            logger.debug("[publish] whatsapp delivery: posted=%s error=%s", wa_posted, wa_error)
            if not wa_posted:
                logger.warning(
                    "[publish] WhatsApp delivery failed: to=%s error=%s",
                    to,
                    wa_error,
                )
        else:
            logger.debug(
                "[publish] whatsapp delivery: skipped — account_sid_present=%s auth_token_present=%s from_number_present=%s to_present=%s",
                bool(account_sid),
                bool(auth_token),
                bool(from_number),
                bool(to),
            )
    else:
        logger.debug("[publish] whatsapp delivery: no whatsapp integration configured or run is scheduled")

    # Twilio SMS — dispatched independently of the legacy WhatsApp record
    # above. WhatsApp delivery is owned solely by the ``whatsapp`` integration.
    twilio_creds = resolved.get("twilio", {})
    if twilio_creds and not is_scheduled:
        sms_cfg = twilio_creds.get("sms") or {}
        if sms_cfg.get("enabled"):
            from app.utils.twilio_delivery import send_twilio_sms_report

            twilio_sms_ctx: dict[str, Any] = state.get("twilio_sms_context") or {}
            sms_to = twilio_sms_ctx.get("to") or sms_cfg.get("default_to") or ""
            sms_from = sms_cfg.get("from_number", "")
            messaging_service_sid = sms_cfg.get("messaging_service_sid", "")
            account_sid = twilio_creds.get("account_sid", "")
            auth_token = twilio_creds.get("auth_token", "")
            logger.debug(
                "[publish] twilio sms delivery: to=%s from=%s msg_service=%s account_sid_present=%s",
                sms_to,
                sms_from,
                messaging_service_sid,
                bool(account_sid),
            )
            if account_sid and auth_token and sms_to and (sms_from or messaging_service_sid):
                # SMS currently reuses the WhatsApp-formatted body — both
                # channels render the same plain-text RCA summary. If
                # formatting needs to diverge (e.g. shorter SMS body or
                # different headers), introduce ``format_sms_message`` in
                # ``formatters/report.py`` and route it through here.
                sms_message = whatsapp_message
                sms_ok, sms_error, sms_sid = send_twilio_sms_report(
                    sms_message,
                    {
                        "account_sid": account_sid,
                        "auth_token": auth_token,
                        "from_number": sms_from,
                        "messaging_service_sid": messaging_service_sid,
                        "to": sms_to,
                    },
                )
                logger.debug(
                    "[publish] twilio sms delivery: posted=%s sid=%s error=%s",
                    sms_ok,
                    sms_sid,
                    sms_error,
                )
                if not sms_ok:
                    logger.warning(
                        "[publish] Twilio SMS delivery failed: to=%s error=%s",
                        sms_to,
                        sms_error,
                    )
            else:
                # SMS channel is enabled but something required for delivery
                # is missing — most commonly no recipient (default_to unset and
                # no runtime twilio_sms_context.to). Warn so the misconfig is
                # visible rather than silently swallowed.
                logger.warning(
                    "[publish] twilio sms delivery: skipped — SMS channel is enabled "
                    "but not deliverable (recipient_present=%s sender_present=%s "
                    "account_sid_present=%s auth_token_present=%s). "
                    "Set TWILIO_SMS_DEFAULT_TO to enable auto-delivery.",
                    bool(sms_to),
                    bool(sms_from or messaging_service_sid),
                    bool(account_sid),
                    bool(auth_token),
                )
    else:
        logger.debug("[publish] twilio sms delivery: no twilio integration configured or run is scheduled")

    openclaw_creds = resolved.get("openclaw", {})
    if openclaw_creds and not is_scheduled:
        from app.utils.openclaw_delivery import send_openclaw_report

        oc_posted, oc_error = send_openclaw_report(state, slack_message, openclaw_creds)
        logger.debug("[publish] openclaw delivery: posted=%s error=%s", oc_posted, oc_error)
        if not oc_posted:
            logger.debug("[publish] OpenClaw delivery failed: %s", oc_error)
    # Trello Delivery
    trello_config = _get_trello_config(resolved)
    if trello_config and not is_scheduled:
        from app.integrations.trello import (
            create_trello_card,
            get_trello_board_lists,
            create_trello_list,
        )

        alert_name = state.get("alert_name") or "System Alert"
        severity = state.get("severity") or "warning"
        severity_emoji = "🔴" if severity == "critical" else "⚠️" if severity == "warning" else "ℹ️"
        category = state.get("root_cause_category") or "Incident"
        card_name = f"{severity_emoji} [{category}] {alert_name}"

        card_desc = _slack_to_markdown(masking_ctx.unmask(slack_message))

        try:
            list_id = trello_config.list_id
            board_id = trello_config.board_id
            pipeline_name = state.get("pipeline_name") or "General Incidents"
            if board_id:
                try:
                    from app.integrations.trello import get_trello_board
                    board_info = get_trello_board(config=trello_config, board_id=board_id)
                    long_board_id = board_info.get("id") or board_id
                except Exception as e:
                    logger.debug("[publish] Failed to resolve long board ID, using as-is: %s", e)
                    long_board_id = board_id

                lists = get_trello_board_lists(config=trello_config, board_id=long_board_id)
                matched_list = next(
                    (lst for lst in lists if lst.get("name", "").strip().lower() == pipeline_name.strip().lower()),
                    None
                )
                if matched_list:
                    list_id = matched_list["id"]
                else:
                    new_list = create_trello_list(config=trello_config, board_id=long_board_id, name=pipeline_name)
                    list_id = new_list.get("id")

            if not list_id:
                raise ValueError("No list_id found and could not resolve one via board_id.")

            card = create_trello_card(
                config=trello_config,
                name=card_name,
                desc=card_desc,
                list_id=list_id,
            )
            logger.info("[publish] Trello card created successfully: %s (ID: %s)", card.get("name"), card.get("id"))
        except Exception as exc:
            logger.warning("[publish] Failed to create Trello card: %s", exc)

    if not is_scheduled:
        post_gitlab_mr_writeback(state, slack_message)

    return {
        "slack_message": slack_message,
        "report": slack_message,
        "telegram_message": telegram_message,
    }


@traceable(name="node_publish_findings")
def node_publish_findings(
    state: InvestigationState,
    config: NodeConfig | None = None,
) -> dict:
    """Publish step wrapper with optional tracing."""
    del config
    return generate_report(state)
