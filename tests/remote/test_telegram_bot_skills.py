from __future__ import annotations

from unittest.mock import MagicMock, patch
from app.remote.telegram_bot import _handle_message


def test_telegram_bot_lists_skills_when_requested() -> None:
    message = {
        "chat": {"id": "12345"},
        "text": "/skills",
        "message_id": "999",
    }

    mock_skills = {
        "vauthz": {"prompt": "Check all PDPs"},
        "cpu": "Check CPU usage",
    }

    with (
        patch("app.cli.commands.skill.load_skills", return_value=mock_skills),
        patch("app.remote.telegram_bot._ALLOWED_CHAT_ID", ""),
        patch("app.remote.telegram_bot._send_message") as mock_send,
    ):
        _handle_message(message)

        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert args[0] == "12345"
        assert "@vauthz" in args[1]
        assert "@cpu" in args[1]
        assert "Available Skills" in args[1]


def test_telegram_bot_injects_skill_in_chat() -> None:
    message = {
        "chat": {"id": "12345"},
        "text": "/chat @vauthz",
        "message_id": "999",
    }

    mock_skills = {
        "vauthz": {"prompt": "Check all PDPs"},
    }

    with (
        patch("app.cli.commands.skill.load_skills", return_value=mock_skills),
        patch("app.remote.telegram_bot._ALLOWED_CHAT_ID", ""),
        patch("app.remote.telegram_bot._run_react_chat") as mock_run_chat,
    ):
        _handle_message(message)

        expected = (
            "=== SRE Skill Guidelines ===\n"
            "- @vauthz:\n"
            "Check all PDPs\n"
            "============================\n\n"
            "[User Request]\n"
            "@vauthz"
        )
        mock_run_chat.assert_called_once_with("12345", expected, reply_to="999")


def test_telegram_bot_injects_skill_in_investigate() -> None:
    message = {
        "chat": {"id": "12345"},
        "text": "/investigate @vauthz and tell me results",
        "message_id": "999",
    }

    mock_skills = {
        "vauthz": {"prompt": "Check all PDPs"},
    }

    with (
        patch("app.cli.commands.skill.load_skills", return_value=mock_skills),
        patch("app.remote.telegram_bot._ALLOWED_CHAT_ID", ""),
        patch("app.remote.telegram_bot._run_investigation") as mock_run_investigation,
    ):
        _handle_message(message)

        expected = (
            "=== SRE Skill Guidelines ===\n"
            "- @vauthz:\n"
            "Check all PDPs\n"
            "============================\n\n"
            "[User Request]\n"
            "@vauthz and tell me results"
        )
        mock_run_investigation.assert_called_once_with("12345", expected, reply_to="999")
