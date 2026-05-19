"""Async REPL loop for SRE Tool Chat.

Replicates the premium prompt-toolkit terminal UI from the main interactive
shell (banner, spinner, Esc-cancel, natural scrollback) but wired to run
the ReAct tool-calling loop from ``tool_chat.py``.

Launch via::

    uv run opensre chat
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.ui import (
    ANSI_DIM,
    ANSI_RESET,
    DIM,
    ERROR,
    HIGHLIGHT,
    PROMPT_ACCENT_ANSI,
    WARNING,
    render_banner,
)
from app.cli.interactive_shell.ui.streaming import MARKDOWN_THEME
from app.cli.support.output import set_silent_tracker
from app.cli.support.exception_reporting import report_exception

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CHARS_PER_TOKEN = 4
_PROMPT_REFRESH_INTERVAL_S = 0.25

# Rich style names (not raw ANSI codes) for console.print markup.
_BRAND = "bold cyan"
_BOLD_BRAND = "bold cyan"
_SECONDARY = "dim"


# ---------------------------------------------------------------------------
# Spinner (matched from loop.py)
# ---------------------------------------------------------------------------
class _SpinnerState:
    """Manages the interactive thinking/stream indicator animation."""

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _THINKING_VERBS = (
        "thinking",
        "pondering",
        "exploring",
        "reasoning",
        "considering",
        "analysing",
        "investigating",
        "deliberating",
        "ruminating",
        "deducing",
        "noodling",
    )

    def __init__(self) -> None:
        self.streaming: bool = False
        self.started_at: float = 0.0
        self.bytes_in: int = 0
        self._frame_idx: int = 0
        self._verb: str = self._THINKING_VERBS[0]

    def start(self) -> None:
        self.streaming = True
        self.started_at = time.monotonic()
        self.bytes_in = 0
        self._frame_idx = 0
        self._verb = random.choice(self._THINKING_VERBS)

    def stop(self) -> None:
        self.streaming = False

    def toolbar_ansi(self) -> ANSI:
        """Bottom toolbar hint row directly below the input."""
        if self.streaming:
            hint = "esc to interrupt"
        else:
            hint = "/ for commands  ·  ↑↓ history"
            app = get_app_or_none()
            if app is not None and app.current_buffer.text:
                hint += "  ·  esc to clear"
        return ANSI(f"{ANSI_DIM}{hint}{ANSI_RESET}")

    def inline_spinner_ansi(self) -> str:
        """Single-line thinking indicator pinned above the input."""
        if not self.streaming:
            return ""
        elapsed = time.monotonic() - self.started_at
        tokens = self.bytes_in // _CHARS_PER_TOKEN
        tokens_str = f"{tokens}t" if tokens < 1000 else f"{tokens / 1000:.1f}kt"
        glyph = self._SPINNER_FRAMES[self._frame_idx % len(self._SPINNER_FRAMES)]
        self._frame_idx += 1
        return (
            f"{PROMPT_ACCENT_ANSI}{glyph} {self._verb}…{ANSI_RESET}"
            f"{ANSI_DIM} ({elapsed:.0f}s · ↓ {tokens_str}){ANSI_RESET}"
        )


# ---------------------------------------------------------------------------
# REPL state
# ---------------------------------------------------------------------------
@dataclass
class _ReplState:
    """Thread-safe state shared between key bindings and worker thread."""

    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    current_task: asyncio.Task[None] | None = None
    current_cancel_event: threading.Event | None = None
    exit_requested: bool = False
    loop: asyncio.AbstractEventLoop | None = None

    def cancel_current_dispatch(self) -> None:
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        if self.current_task is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(self.current_task.cancel)

    def is_dispatch_running(self) -> bool:
        return self.current_task is not None


# ---------------------------------------------------------------------------
# Streaming console (mirrors loop.py's _StreamingConsole)
# ---------------------------------------------------------------------------
class _StreamingConsole(Console):
    """Custom Console that exposes cancel + progress hooks."""

    def __init__(
        self,
        spinner: _SpinnerState,
        cancel_event: threading.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spinner = spinner
        self._cancel_event = cancel_event

    def update_streaming_progress(self, bytes_received: int) -> None:
        self._spinner.bytes_in = bytes_received

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()


# ---------------------------------------------------------------------------
# Key bindings (Esc to cancel / clear, Ctrl-L to redraw)
# ---------------------------------------------------------------------------
def _build_cancel_key_bindings(state: _ReplState) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        if state.is_dispatch_running():
            state.cancel_current_dispatch()
            return
        if event.current_buffer.text:
            event.current_buffer.reset()

    @kb.add("c-l")
    def _on_ctrl_l(event: KeyPressEvent) -> None:
        event.app.renderer.clear()

    return kb


def _install_session_key_bindings(pt_session: object, extra_kb: KeyBindings) -> None:
    existing = getattr(pt_session, "key_bindings", None)
    merged = (
        merge_key_bindings([existing, extra_kb])
        if existing is not None
        else extra_kb
    )
    pt_session.key_bindings = merged  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Synchronous dispatch (runs inside asyncio.to_thread)
# ---------------------------------------------------------------------------
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


def _dispatch_one_turn(
    text: str,
    console: Console,
    messages: list[dict[str, Any]],
    llm: Any,
    tool_map: dict[str, Any],
    tool_schemas: list[dict[str, Any]],
    resolved: Any,
    cancel_event: threading.Event,
    on_exit: Callable[[], None],
) -> None:
    """Execute one user turn through the ReAct tool-calling loop."""

    # ── Slash commands ────────────────────────────────────────────────
    if text.lower() in ("exit", "quit"):
        on_exit()
        return

    if text.startswith("/"):
        cmd = text.strip().lower()
        if cmd == "/reset":
            messages.clear()
            console.print(f"[{_SECONDARY}]Conversation history cleared.[/]")
            return
        if cmd in ("/exit", "/quit"):
            on_exit()
            return
        if cmd == "/help":
            console.print()
            console.print(f"[{_BOLD_BRAND}]═══ SRE Tool Chat — Help ═══[/]")
            console.print(f"[{_SECONDARY}]Commands:[/]")
            console.print("  [bold]/reset[/]  — Clear conversation history")
            console.print("  [bold]/exit[/]   — Quit the tool chat shell")
            console.print("  [bold]/help[/]   — Show this help")
            console.print()
            return
        console.print(f"[{ERROR}]Unknown command:[/] {escape(text)}")
        return

    # ── Append user message ───────────────────────────────────────────
    messages.append({"role": "user", "content": text})

    from app.services.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    # ── ReAct loop (configurable via env, defaults to 10) ─────────────
    import os
    max_iterations = int(os.environ.get("OPENSRE_MAX_ITERATIONS", "10"))
    for _iteration in range(max_iterations):
        if cancel_event.is_set():
            console.print(f"[{WARNING}]· interrupted[/]")
            return

        try:
            response = llm.invoke(
                messages, system=_SYSTEM_PROMPT, tools=tool_schemas,
            )
        except KeyboardInterrupt:
            console.print(f"[{WARNING}]· interrupted[/]")
            return
        except Exception as exc:
            report_exception(exc, context="tool_repl.llm_invoke")
            console.print(f"[{ERROR}]assistant failed:[/] {escape(str(exc))}")
            return

        if cancel_event.is_set():
            console.print(f"[{WARNING}]· interrupted[/]")
            return

        # Record assistant message
        if isinstance(llm, AnthropicAgentClient):
            messages.append(llm.build_assistant_message(response.raw_content))
        else:
            messages.append(
                llm.build_assistant_message(response.content, response.tool_calls),
            )

        # ── No tool calls → show text and break ──────────────────────
        if not response.has_tool_calls:
            if response.content:
                console.print()
                console.print(f"[{_BOLD_BRAND}]assistant:[/]")
                with console.use_theme(MARKDOWN_THEME):
                    console.print(Markdown(response.content, code_theme="ansi_dark"))
                console.print()
            break

        # ── Execute tool calls ────────────────────────────────────────
        results: list[Any] = []
        for tc in response.tool_calls:
            if cancel_event.is_set():
                console.print(f"[{WARNING}]· interrupted[/]")
                return

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

            out_str = json.dumps(output, indent=2, ensure_ascii=False, default=str)
            if len(out_str) > 2000:
                out_str = out_str[:2000] + "\n... (truncated)"
            console.print(f"  [{HIGHLIGHT}]{tc.name} returned:[/]")
            from rich.syntax import Syntax
            syntax = Syntax(out_str, "json", theme="nord", background_color="default")
            console.print(syntax)

        # Add tool results to conversation
        if isinstance(llm, AnthropicAgentClient):
            messages.append(
                llm.build_tool_result_message(response.tool_calls, results),
            )
        elif isinstance(llm, OpenAIAgentClient):
            messages.extend(
                llm.build_tool_result_messages(response.tool_calls, results),
            )
        else:
            messages.append(
                llm.build_tool_result_message(response.tool_calls, results),
            )


# ---------------------------------------------------------------------------
# Async REPL (prompt-toolkit interactive loop)
# ---------------------------------------------------------------------------
async def _run_interactive(
    llm: Any,
    tool_map: dict[str, Any],
    tool_schemas: list[dict[str, Any]],
    resolved: Any,
) -> None:
    """Premium prompt-toolkit REPL with queue-based dispatch."""

    pt_session: PromptSession[str] = PromptSession(erase_when_done=True)
    spinner = _SpinnerState()
    state = _ReplState()

    cancel_kb = _build_cancel_key_bindings(state)
    _install_session_key_bindings(pt_session, cancel_kb)

    pt_app = pt_session.app

    # Temporarily suspend drawing during dispatch when investigation display is active.
    def should_suspend() -> bool:
        from app.cli.support.output import _active_display
        is_live_active = _active_display is not None and _active_display._live.is_started
        return state.is_dispatch_running() and is_live_active and not state.is_awaiting_confirmation()

    original_invalidate = pt_app.invalidate
    original_render = pt_app.renderer.render

    def new_invalidate() -> None:
        if should_suspend():
            try:
                pt_app.renderer.erase()
            except Exception:
                pass
            return
        original_invalidate()

    def new_render(app_arg: Any, layout: Any, is_done: bool = False) -> None:
        if should_suspend():
            return
        original_render(app_arg, layout, is_done=is_done)

    pt_app.invalidate = new_invalidate
    pt_app.renderer.render = new_render

    main_loop = asyncio.get_running_loop()
    state.loop = main_loop

    messages: list[dict[str, Any]] = []
    turn_count = [1]  # mutable counter for closures

    def _request_exit() -> None:
        state.exit_requested = True
        state.cancel_current_dispatch()
        main_loop.call_soon_threadsafe(pt_app.exit)

    async def _run_one_dispatch(text: str) -> None:
        dispatch_cancel = threading.Event()
        state.current_cancel_event = dispatch_cancel
        console = _StreamingConsole(
            spinner,
            dispatch_cancel,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )

        spinner.start()
        # We run the synchronous body in a real Thread and await its completion.
        # This allows us to shield the wait so we can guarantee the worker thread
        # is fully stopped/cleaned up before we exit the dispatch and restore the prompt.
        thread_done = asyncio.Event()
        worker_exc: Exception | None = None

        def run_thread() -> None:
            nonlocal worker_exc
            try:
                _dispatch_one_turn(
                    text,
                    console,
                    messages,
                    llm,
                    tool_map,
                    tool_schemas,
                    resolved,
                    dispatch_cancel,
                    _request_exit,
                )
            except Exception as exc:
                worker_exc = exc
            finally:
                main_loop.call_soon_threadsafe(thread_done.set)

        worker_thread = threading.Thread(target=run_thread, daemon=True)
        worker_thread.start()

        try:
            await thread_done.wait()
            if worker_exc is not None:
                raise worker_exc
            turn_count[0] += 1
        except (asyncio.CancelledError, KeyboardInterrupt):
            dispatch_cancel.set()
            try:
                await asyncio.shield(thread_done.wait())
            except KeyboardInterrupt:
                pass
            console.print(f"[{WARNING}]· interrupted[/]")
            raise asyncio.CancelledError
        except Exception as exc:
            report_exception(exc, context="tool_repl.dispatch")
            console.print(f"[{ERROR}]dispatch error:[/] {escape(str(exc))}")
        finally:
            spinner.stop()
            if state.current_cancel_event is dispatch_cancel:
                state.current_cancel_event = None

    async def _processor() -> None:
        """Drain queued prompts one dispatch at a time."""
        while not state.exit_requested:
            try:
                text = await state.queue.get()
            except asyncio.CancelledError:
                return
            if state.exit_requested:
                state.queue.task_done()
                return
            state.current_task = asyncio.create_task(_run_one_dispatch(text))
            try:
                await state.current_task
            except (asyncio.CancelledError, Exception):
                pass
            state.current_task = None
            state.queue.task_done()

    def _message_with_spinner() -> ANSI:
        n = turn_count[0]
        prompt_text = f"{PROMPT_ACCENT_ANSI}[{n}] ❯{ANSI_RESET} "
        return ANSI(f"{spinner.inline_spinner_ansi()}\n{prompt_text}")

    processor_task = asyncio.create_task(_processor())

    try:
        with patch_stdout(raw=True):
            # Console inside patch_stdout so Rich captures the patched proxy.
            echo_console = Console(
                highlight=False,
                force_terminal=True,
                color_system="truecolor",
            )
            while True:
                try:
                    text = await pt_session.prompt_async(
                        message=_message_with_spinner,
                        bottom_toolbar=spinner.toolbar_ansi,
                        refresh_interval=_PROMPT_REFRESH_INTERVAL_S,
                    )
                except EOFError:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        if state.current_task is not None:
                            try:
                                await state.current_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        continue
                    return
                except KeyboardInterrupt:
                    if state.is_dispatch_running():
                        state.cancel_current_dispatch()
                        if state.current_task is not None:
                            try:
                                await state.current_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        continue
                    echo_console.print(f"\n[{_SECONDARY}]Goodbye![/]")
                    return

                if state.exit_requested:
                    return

                stripped = (text or "").strip()
                if not stripped:
                    continue

                # Echo submitted prompt (erase_when_done=True cleared it)
                n = turn_count[0]
                echo_console.print(
                    f"[{_BRAND}][{n}] ❯[/] {escape(stripped)}",
                )
                await state.queue.put(stripped)
    finally:
        state.exit_requested = True
        state.cancel_current_dispatch()
        processor_task.cancel()
        try:
            await processor_task
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tool_repl() -> int:
    """Main entry point for ``opensre chat``."""
    set_silent_tracker()

    real_console = Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    real_console.print()
    render_banner(real_console)
    real_console.print(f"[{_SECONDARY}]Initializing SRE tools and LLM client...[/]")

    try:
        from app.services.agent_llm_client import get_agent_llm
        from app.tools.registry import get_registered_tools
        from app.agent.context import resolve_integrations

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
        report_exception(exc, context="tool_repl.init")
        real_console.print(f"[{ERROR}]Initialization failed:[/] {escape(str(exc))}")
        return 1

    real_console.print(f"[{_BOLD_BRAND}]═══ SRE Tool Chat ═══[/]")
    real_console.print(
        f"[{_SECONDARY}]Available tools: {', '.join(tool_map.keys())}[/]",
    )
    real_console.print(
        f"[{_SECONDARY}]Type '/help' for options  ·  Ctrl+C or 'exit' to quit[/]",
    )
    real_console.print()

    try:
        asyncio.run(_run_interactive(llm, tool_map, tool_schemas, resolved))
        return 0
    except (EOFError, KeyboardInterrupt):
        return 0
