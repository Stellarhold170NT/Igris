"""Click command for interactive SRE tool chat."""

from __future__ import annotations

import click


@click.command(name="chat")
@click.option(
    "--interactive/--no-interactive",
    "interactive",
    default=True,
    help="Start the interactive SRE tool-calling chat shell.",
)
@click.pass_context
def chat_command(ctx: click.Context, interactive: bool) -> None:
    """Start the interactive SRE tool-calling chat shell with direct tool access."""
    if not interactive:
        click.echo("Interactive mode disabled.")
        return

    from app.cli.interactive_shell import run_repl
    from app.cli.interactive_shell.config import ReplConfig

    config = ReplConfig.load(
        cli_enabled=True,
        cli_layout=None,
        cli_reload=None,
    )
    raise SystemExit(run_repl(config=config, tool_calling=True))
