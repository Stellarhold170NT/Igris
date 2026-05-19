"""Tool-calling assistant handler for the interactive shell.

Drop-in replacement for ``answer_cli_agent`` when ``session.tool_calling``
is True (``--coral`` flag).  Uses the agent LLM client with direct tool
execution — same ReAct loop as ``tool_chat.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    MARKDOWN_THEME,
    STREAM_LABEL_ASSISTANT,
)
from app.cli.support.exception_reporting import report_exception

_SYSTEM_PROMPT = (
    "You are V-SRE Assistant with direct tool access.\n"
    "When the user asks for data or wants to query systems "
    "(GitHub, DBs, Logs, etc.), USE the available tools immediately.\n"
    "For coral_query: write SQL queries. Start with discovery:\n"
    "  1. SELECT * FROM coral.tables LIMIT 20;\n"
    "  2. SELECT * FROM coral.columns WHERE table_name = '...';\n"
    "  3. Then query the actual data with LIMIT 10.\n"
    "Be concise. Show results clearly in markdown."
)


def answer_with_tools(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
) -> None:
    """ReAct tool-calling handler — mirrors ``tool_chat.py`` logic.

    Called by the dispatch loop in place of ``answer_cli_agent`` when
    ``session.tool_calling`` is True.
    """
    try:
        from app.services.agent_llm_client import get_agent_llm
        from app.tools.registry import get_registered_tools
        from app.agent.context import resolve_integrations
        from app.cli.support.output import set_silent_tracker
        set_silent_tracker()
    except Exception as exc:
        report_exception(exc, context="interactive_shell.tool_agent.import")
        console.print(f"[{ERROR}]LLM or tool registry unavailable:[/] {escape(str(exc))}")
        return

    # --- Initialise LLM + tools (lazy, on first call) ---
    try:
        llm = get_agent_llm()
        resolved = resolve_integrations({"raw_alert": {}})
        all_tools = get_registered_tools("investigation")

        tools = []
        for t in all_tools:
            try:
                if t.is_available(resolved) or t.name == "coral_query":
                    tools.append(t)
            except Exception:
                pass

        tool_map = {t.name: t for t in tools}
        tool_schemas = llm.tool_schemas(tools)
    except Exception as exc:
        report_exception(exc, context="interactive_shell.tool_agent.init")
        console.print(f"[{ERROR}]Tool agent init failed:[/] {escape(str(exc))}")
        return

    # --- Build conversation messages ---
    if not session.tool_chat_messages:
        session.tool_chat_messages = []
    session.tool_chat_messages.append({"role": "user", "content": message})

    from app.services.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    # --- ReAct loop (configurable via env, defaults to 10) ---
    import os
    max_iterations = int(os.environ.get("OPENSRE_MAX_ITERATIONS", "10"))
    final_text = ""

    for _iteration in range(max_iterations):
        try:
            response = llm.invoke(
                session.tool_chat_messages,
                system=_SYSTEM_PROMPT,
                tools=tool_schemas,
            )
        except KeyboardInterrupt:
            console.print(f"[{DIM}]· cancelled[/]")
            return
        except Exception as exc:
            report_exception(exc, context="interactive_shell.tool_agent.invoke")
            console.print(f"[{ERROR}]assistant failed:[/] {escape(str(exc))}")
            return

        # Record assistant message in history
        if isinstance(llm, AnthropicAgentClient):
            session.tool_chat_messages.append(
                llm.build_assistant_message(response.raw_content),
            )
        else:
            session.tool_chat_messages.append(
                llm.build_assistant_message(response.content, response.tool_calls),
            )

        # No tool calls → show text response and break
        if not response.has_tool_calls:
            final_text = response.content or ""
            if final_text:
                console.print()
                console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
                with console.use_theme(MARKDOWN_THEME):
                    console.print(Markdown(final_text, code_theme="ansi_dark"))
                console.print()
            break

        # Execute each tool call
        results: list[Any] = []
        for tc in response.tool_calls:
            console.print(f"  [{HIGHLIGHT}]calling {tc.name}[/]", end="")
            if tc.input:
                short_args = json.dumps(tc.input, ensure_ascii=False)
                if len(short_args) > 80:
                    short_args = short_args[:80] + "..."
                console.print(f" [{DIM}]{escape(short_args)}[/]")
            else:
                console.print()

            tool = tool_map.get(tc.name)
            if tool is None:
                output: Any = {"error": f"unknown tool: {tc.name}"}
            else:
                try:
                    injected = tool.extract_params(resolved)
                    kwargs = {**injected, **tc.input}
                    output = tool.run(**kwargs)
                except Exception as exc:
                    output = {"error": str(exc)}

            results.append(output)

            # Show tool output
            out_str = json.dumps(output, indent=2, ensure_ascii=False, default=str)
            if len(out_str) > 2000:
                out_str = out_str[:2000] + "\n... (truncated)"
            console.print(f"  [{HIGHLIGHT}]{tc.name} returned:[/]")
            from rich.syntax import Syntax
            syntax = Syntax(out_str, "json", theme="nord", background_color="default")
            console.print(syntax)

        # Add tool results to conversation
        if isinstance(llm, AnthropicAgentClient):
            messages_to_add = llm.build_tool_result_message(response.tool_calls, results)
            session.tool_chat_messages.append(messages_to_add)
        elif isinstance(llm, OpenAIAgentClient):
            session.tool_chat_messages.extend(
                llm.build_tool_result_messages(response.tool_calls, results),
            )
        else:
            session.tool_chat_messages.append(
                llm.build_tool_result_message(response.tool_calls, results),
            )

    # Record the turn in session history
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", final_text))


__all__ = ["answer_with_tools"]
