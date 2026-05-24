"""Telegram Bot for V-SRE: ReAct chat and investigation via long-polling.

Two modes:
  /chat <message>       — ReAct loop with tool calling (like tool_chat.py)
  /investigate <alert>  — Full investigation pipeline (like /investigate endpoint)
  /help                 — Usage instructions

Start standalone:
    uv run python -m app.remote.telegram_bot

Or integrate into the FastAPI server lifespan for background polling.

Requires env vars:
    TELEGRAM_BOT_TOKEN          — Bot token from @BotFather
    TELEGRAM_DEFAULT_CHAT_ID    — (optional) restrict to specific chat
"""

from __future__ import annotations

import asyncio
import html as _html
import json
import logging
import os
import re
import textwrap
import threading
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
_ALLOWED_CHAT_ID = (os.getenv("TELEGRAM_DEFAULT_CHAT_ID") or "").strip()
_MAX_MESSAGE_LEN = 4096
_POLL_TIMEOUT = 30  # Telegram long-poll timeout (seconds)
_MAX_REACT_ITERATIONS = 10


# ---------------------------------------------------------------------------
# Telegram API helpers (reuse existing httpx-based transport)
# ---------------------------------------------------------------------------


def _tg_api(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call a Telegram Bot API method. Returns the parsed JSON response."""
    from app.utils.delivery_transport import post_json

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/{method}"
    resp = post_json(url=url, payload=payload or {})
    if resp.data and resp.data.get("ok"):
        return resp.data
    error_desc = ""
    if resp.data:
        error_desc = resp.data.get("description", "")
    raise RuntimeError(f"Telegram API {method} failed: {resp.status_code} {error_desc}")


def _send_message(
    chat_id: str,
    text: str,
    *,
    reply_to: str = "",
    parse_mode: str = "",
) -> str:
    """Send a message and return the message_id."""
    from app.utils.telegram_delivery import truncate_for_telegram_html
    from app.utils.truncation import truncate

    if parse_mode.upper() == "HTML":
        text = truncate_for_telegram_html(text, _MAX_MESSAGE_LEN, suffix="…")
    else:
        text = truncate(text, _MAX_MESSAGE_LEN, suffix="…")

    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to and reply_to != "0":
        payload["reply_to_message_id"] = int(reply_to)

    data = _tg_api("sendMessage", payload)
    return str(data.get("result", {}).get("message_id", ""))


def _send_typing(chat_id: str) -> None:
    """Send 'typing...' indicator."""
    try:
        _tg_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        pass  # non-critical


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_markdown_tables(text: str) -> str:
    """Find markdown tables in the text, align their columns, and wrap them in <pre>...</pre>."""
    lines = text.splitlines()
    output_lines = []
    in_table = False
    table_lines = []

    def flush_table():
        if not table_lines:
            return []
        
        parsed_rows = []
        for line in table_lines:
            stripped = line.strip()
            if stripped.startswith("|"):
                stripped = stripped[1:]
            if stripped.endswith("|"):
                stripped = stripped[:-1]
            cells = [c.strip() for c in stripped.split("|")]
            parsed_rows.append(cells)

        if not parsed_rows:
            return []

        cols_count = max(len(row) for row in parsed_rows)
        for row in parsed_rows:
            while len(row) < cols_count:
                row.append("")

        is_sep_row = False
        if len(parsed_rows) > 1:
            second_row = parsed_rows[1]
            if all(re.match(r"^:?-+:?$", cell) for cell in second_row if cell):
                is_sep_row = True

        widths = [0] * cols_count
        for idx, row in enumerate(parsed_rows):
            if idx == 1 and is_sep_row:
                continue
            for col_idx, cell in enumerate(row):
                widths[col_idx] = max(widths[col_idx], len(cell))

        formatted_lines = []
        for idx, row in enumerate(parsed_rows):
            if idx == 1 and is_sep_row:
                sep_cells = ["-" * (widths[c_idx] + 2) for c_idx in range(cols_count)]
                formatted_lines.append("|" + "|".join(sep_cells) + "|")
            else:
                formatted_cells = [f" {row[c_idx].ljust(widths[c_idx])} " for c_idx in range(cols_count)]
                formatted_lines.append("|" + "|".join(formatted_cells) + "|")

        table_text = "\n".join(formatted_lines)
        return [f"<pre>{table_text}</pre>"]

    for line in lines:
        stripped = line.strip()
        is_table_line = (
            (stripped.startswith("|") and stripped.endswith("|"))
            or (stripped.count("|") >= 2 and re.match(r"^\|?.*\|.*\|?$", stripped))
        )
        
        if is_table_line:
            if not in_table:
                in_table = True
                table_lines = [line]
            else:
                table_lines.append(line)
        else:
            if in_table:
                output_lines.extend(flush_table())
                table_lines = []
                in_table = False
            output_lines.append(line)

    if in_table:
        output_lines.extend(flush_table())

    return "\n".join(output_lines)


def _format_chat_response(text: str) -> str:
    """Convert LLM plain-text response to Telegram HTML.

    Handles common markdown patterns from LLM output so the message
    renders nicely in Telegram with parse_mode=HTML.
    """
    # Escape HTML entities first
    s = _html.escape(text)
    s = _format_markdown_tables(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    # *italic* → <i>italic</i>  (only single stars not already consumed)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)
    # `code` → <code>code</code>
    s = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)
    # ### heading → <b>heading</b>
    s = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", s, flags=re.MULTILINE)
    return s


# ---------------------------------------------------------------------------
# Mode 1: ReAct tool chat (mirrors tool_chat.py logic)
# ---------------------------------------------------------------------------


def _run_react_chat(chat_id: str, user_text: str, reply_to: str) -> None:
    """Run a ReAct tool-calling loop and send results back to Telegram."""
    from app.agent.context import resolve_integrations
    from app.services.agent_llm_client import (
        AnthropicAgentClient,
        OpenAIAgentClient,
        get_agent_llm,
    )
    from app.tools.registry import get_registered_tools

    _send_typing(chat_id)

    llm = get_agent_llm()
    resolved = resolve_integrations({"raw_alert": {}})
    all_tools = get_registered_tools("investigation")

    tools = []
    for t in all_tools:
        try:
            if t.is_available(resolved):
                tools.append(t)
        except Exception:
            pass

    tool_map = {t.name: t for t in tools}
    tool_schemas = llm.tool_schemas(tools)

    system = (
        "You are V-SRE Assistant on Telegram with direct tool access. "
        "When the user asks for data, USE the available tools immediately. "
        "For coral_query: write SQL queries. Start with discovery:\n"
        "  1. SELECT * FROM coral.tables LIMIT 20;\n"
        "  2. SELECT * FROM coral.columns WHERE table_name = '...';\n"
        "  3. Then query the actual data with LIMIT 10.\n"
        "Be concise. Do NOT use markdown formatting. Use plain text only. "
        "Always respond in Vietnamese with accents (tiếng Việt có dấu). Do NOT use unsigned Vietnamese (tiếng Việt không dấu)."
    )

    messages: list[dict] = [{"role": "user", "content": user_text}]

    last_reply_id = reply_to

    for _iteration in range(_MAX_REACT_ITERATIONS):
        _send_typing(chat_id)

        try:
            response = llm.invoke(messages, system=system, tools=tool_schemas)
        except Exception as exc:
            logger.exception("[telegram] LLM invoke failed")
            _send_message(chat_id, f"⚠️ LLM error: {exc}", reply_to=last_reply_id)
            return

        # Build assistant message for history
        if isinstance(llm, AnthropicAgentClient):
            messages.append(llm.build_assistant_message(response.raw_content))
        else:
            messages.append(
                llm.build_assistant_message(response.content, response.tool_calls)
            )

        # No tool calls → final answer
        if not response.has_tool_calls:
            if response.content:
                last_reply_id = _send_message(
                    chat_id,
                    _format_chat_response(response.content),
                    reply_to=last_reply_id,
                    parse_mode="HTML",
                )
            return

        # Execute tools
        results = []
        tool_summaries = []
        for tc in response.tool_calls:
            tool = tool_map.get(tc.name)
            if tool is None:
                output = {"error": f"unknown tool: {tc.name}"}
            else:
                try:
                    injected = tool.extract_params(resolved)
                    kwargs = {**injected, **tc.input}
                    output = tool.run(**kwargs)
                except Exception as exc:
                    output = {"error": str(exc)}

            results.append(output)
            out_str = json.dumps(output, ensure_ascii=False, default=str)
            if len(out_str) > 500:
                out_str = out_str[:500] + "…"
            tool_summaries.append(f"⚡ {tc.name} → {out_str}")


        # Feed results back to LLM
        if isinstance(llm, AnthropicAgentClient):
            messages.append(llm.build_tool_result_message(response.tool_calls, results))
        elif isinstance(llm, OpenAIAgentClient):
            messages.extend(llm.build_tool_result_messages(response.tool_calls, results))
        else:
            messages.append(llm.build_tool_result_message(response.tool_calls, results))

    # If we exhaust iterations
    _send_message(
        chat_id,
        "⚠️ Reached max iterations without final answer.",
        reply_to=last_reply_id,
    )


# ---------------------------------------------------------------------------
# Mode 2: Investigation pipeline
# ---------------------------------------------------------------------------


def _run_investigation(chat_id: str, alert_text: str, reply_to: str) -> None:
    """Run the full investigation pipeline.

    The pipeline's publish_findings node auto-sends the formatted HTML report
    to Telegram via send_telegram_report(), so we only send a "starting"
    notification here and avoid a duplicate message.
    """
    _send_typing(chat_id)

    # Parse as JSON or treat as plain text alert
    try:
        raw_alert: dict[str, Any] = json.loads(alert_text)
    except (json.JSONDecodeError, ValueError):
        raw_alert = {"alert_name": alert_text, "description": alert_text}

    alert_name = raw_alert.get("alert_name") or alert_text[:100]

    # Inject language preference so pipeline LLM nodes respond in Vietnamese
    _lang_hint = "\n[IMPORTANT: All analysis and report MUST be written in Vietnamese with accents (tiếng Việt có dấu). Absolutely do NOT write in Vietnamese without accents (tiếng Việt không dấu).]"
    raw_alert["description"] = (raw_alert.get("description") or "") + _lang_hint

    _send_message(
        chat_id,
        f"🔍 <b>Starting investigation for:</b> {_html.escape(alert_name)}\n"
        f"<i>This may take a few minutes…</i>",
        reply_to=reply_to,
        parse_mode="HTML",
    )

    try:
        from app.analytics.source import EntrypointSource, TriggerMode
        from app.analytics.cli import track_investigation
        from app.cli.investigation import resolve_investigation_context, run_investigation_cli

        investigation_metadata = resolve_investigation_context(
            raw_alert=raw_alert,
            alert_name=raw_alert.get("alert_name"),
            pipeline_name=raw_alert.get("pipeline_name"),
            severity=raw_alert.get("severity"),
        )

        with track_investigation(
            entrypoint=EntrypointSource.REMOTE_HTTP,
            trigger_mode=TriggerMode.SERVICE_RUNTIME,
        ):
            # The pipeline's publish_findings node handles Telegram delivery
            # automatically with proper HTML formatting (severity header,
            # findings, evidence, trace, etc.) — no manual send needed.
            run_investigation_cli(
                raw_alert=raw_alert,
                investigation_metadata=investigation_metadata,
            )

    except Exception as exc:
        logger.exception("[telegram] Investigation failed")
        _send_message(
            chat_id,
            f"❌ Investigation failed: {_html.escape(f'{type(exc).__name__}: {exc}')}",
            reply_to=reply_to,
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# Message dispatcher
# ---------------------------------------------------------------------------

_HELP_TEXT = textwrap.dedent("""\
    🤖 <b>V-SRE Telegram Bot</b>

    <b>Commands:</b>
    /chat &lt;message&gt; — Chat with AI + tools (ReAct loop)
    /investigate &lt;alert&gt; — Run full RCA investigation
    /help — Show this help

    <b>Examples:</b>
    <code>/chat show me grafana service names</code>
    <code>/chat query datadog logs for errors in last 1h</code>
    <code>/investigate High CPU on prod-api server</code>

    <i>You can also just send a message without a command — it will use /chat mode by default.</i>
""")


def _handle_message(message: dict[str, Any]) -> None:
    """Dispatch a single Telegram message to the appropriate handler."""
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    message_id = str(message.get("message_id", ""))

    if not chat_id or not text:
        return

    # Optional: restrict to configured chat
    if _ALLOWED_CHAT_ID and chat_id != _ALLOWED_CHAT_ID:
        logger.debug("[telegram] Ignoring message from chat %s (allowed: %s)", chat_id, _ALLOWED_CHAT_ID)
        return

    logger.info("[telegram] Message from chat %s: %s", chat_id, text[:100])

    # ----------------------------------------------------
    # Skill Injection and Suggestions
    # ----------------------------------------------------
    from app.cli.commands.skill import load_skills
    skills = load_skills()

    # List all skills if user types exactly /skills, /skill, or @
    if text in ("@", "/skills", "/skill"):
        if not skills:
            _send_message(
                chat_id,
                "💡 <b>No skills registered yet.</b>\nUse <code>opensre skill add &lt;name&gt; &lt;prompt&gt;</code> to add one.",
                reply_to=message_id,
                parse_mode="HTML",
            )
            return
        lines = ["💡 <b>Available Skills:</b>"]
        for name, data in skills.items():
            prompt = data.get("prompt") if isinstance(data, dict) else data
            lines.append(f"• <code>@{name}</code> — {prompt[:150]}...")
        lines.append("\nType <code>@name</code> in your message to inject the skill prompt.")
        _send_message(chat_id, "\n".join(lines), reply_to=message_id, parse_mode="HTML")
        return

    # Replace any @skill_name with its prompt content
    if skills:
        def replacer(match):
            skill_name = match.group(1)
            if skill_name in skills:
                skill_data = skills[skill_name]
                prompt = skill_data.get("prompt") if isinstance(skill_data, dict) else skill_data
                return str(prompt or "")
            return match.group(0)

        text = re.sub(r"@([a-zA-Z0-9_-]+)", replacer, text)
    # ----------------------------------------------------

    if text.startswith("/help"):
        _send_message(chat_id, _HELP_TEXT, reply_to=message_id, parse_mode="HTML")
        return

    if text.startswith("/investigate"):
        alert_text = text.removeprefix("/investigate").strip()
        # Handle bot username suffix like /investigate@MyBot
        if alert_text.startswith("@"):
            alert_text = alert_text.split(" ", 1)[-1].strip() if " " in alert_text else ""
        if not alert_text:
            _send_message(
                chat_id,
                "Usage: /investigate &lt;alert description or JSON&gt;",
                reply_to=message_id,
                parse_mode="HTML",
            )
            return
        _run_investigation(chat_id, alert_text, reply_to=message_id)
        return

    if text.startswith("/chat"):
        user_text = text.removeprefix("/chat").strip()
        if user_text.startswith("@"):
            user_text = user_text.split(" ", 1)[-1].strip() if " " in user_text else ""
        if not user_text:
            _send_message(
                chat_id,
                "Usage: /chat &lt;your question&gt;",
                reply_to=message_id,
                parse_mode="HTML",
            )
            return
        _run_react_chat(chat_id, user_text, reply_to=message_id)
        return

    # Default: treat any plain message as /chat
    if text.startswith("/"):
        _send_message(
            chat_id,
            "Unknown command. Use /help to see available commands.",
            reply_to=message_id,
        )
        return

    _run_react_chat(chat_id, text, reply_to=message_id)


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------


def _poll_loop() -> None:
    """Block forever, polling Telegram getUpdates and dispatching messages."""
    logger.info("[telegram] Bot polling started (token: %s…)", _BOT_TOKEN[:12])

    offset = 0
    while True:
        try:
            params: dict[str, Any] = {
                "timeout": _POLL_TIMEOUT,
                "allowed_updates": ["message"],
            }
            if offset:
                params["offset"] = offset

            data = _tg_api("getUpdates", params)
            updates = data.get("result", [])

            for update in updates:
                update_id = update.get("update_id", 0)
                offset = max(offset, update_id + 1)

                msg = update.get("message")
                if not msg:
                    continue

                # Handle each message in a thread so polling isn't blocked
                t = threading.Thread(
                    target=_handle_message_safe,
                    args=(msg,),
                    daemon=True,
                )
                t.start()

        except KeyboardInterrupt:
            logger.info("[telegram] Bot stopped by user")
            break
        except Exception as exc:
            logger.warning("[telegram] Poll error: %s, retrying in 5s", exc)
            time.sleep(5)


def _handle_message_safe(message: dict[str, Any]) -> None:
    """Wrapper that catches exceptions so a single bad message doesn't crash the bot."""
    try:
        _handle_message(message)
    except Exception as exc:
        logger.exception("[telegram] Unhandled error processing message")
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id:
            try:
                _send_message(chat_id, f"❌ Internal error: {exc}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Async wrapper for FastAPI lifespan integration
# ---------------------------------------------------------------------------


async def run_telegram_polling() -> None:
    """Async wrapper to run the blocking poll loop in a thread."""
    if not _BOT_TOKEN:
        logger.info("[telegram] TELEGRAM_BOT_TOKEN not set, skipping bot startup")
        return
    await asyncio.to_thread(_poll_loop)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Telegram bot standalone."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not _BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set. Set it in .env or environment.")
        return

    # Suppress OpenSRE's Rich progress tracker
    try:
        from app.cli.support.output import set_silent_tracker
        set_silent_tracker()
    except Exception:
        pass

    print(f"🤖 V-SRE Telegram Bot starting...")
    print(f"   Token: {_BOT_TOKEN[:12]}…")
    if _ALLOWED_CHAT_ID:
        print(f"   Restricted to chat: {_ALLOWED_CHAT_ID}")
    else:
        print(f"   Accepting all chats")
    print(f"   Commands: /chat, /investigate, /help")
    print(f"   Press Ctrl+C to stop\n")

    _poll_loop()


if __name__ == "__main__":
    main()
