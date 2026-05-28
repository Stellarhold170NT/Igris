"""Tests for the task executor with isolated stores."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.scheduler.executor import execute_task
from app.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def _tmp_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both stores at tmp_path so tests are isolated."""
    monkeypatch.setattr(
        "app.scheduler.claim_store._default_db_path", lambda: tmp_path / "scheduler.db"
    )
    monkeypatch.setattr("app.scheduler.store._default_store_path", lambda: tmp_path / "tasks.json")


@pytest.mark.usefixtures("_tmp_stores")
class TestExecutor:
    def test_telegram_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_tg_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor.resolve_telegram_credentials") as mock_creds,
            patch("app.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_build.return_value = (
                "Fake daily summary",
                {"telegram": {"credentials": {"bot_token": "fake_token"}}},
            )
            mock_creds.return_value = {"bot_token": "fake_token"}
            mock_deliver.return_value = (True, "", "msg_42")

            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_telegram_missing_credentials(self) -> None:
        task = ScheduledTask(
            id="test_tg_02",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor.resolve_telegram_credentials") as mock_creds,
        ):
            mock_build.return_value = ("Fake daily summary", {})
            mock_creds.return_value = {}
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False

    def test_slack_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_sl_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123456",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor._deliver_slack") as mock_deliver,
        ):
            mock_build.return_value = (
                "Fake daily summary",
                {"slack": {"credentials": {"access_token": "fake_token"}}},
            )
            mock_deliver.return_value = (True, "", "ts_123")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_discord_delivery_success(self) -> None:
        task = ScheduledTask(
            id="test_dc_01",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.DISCORD,
            chat_id="123456789",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor._deliver_discord") as mock_deliver,
        ):
            mock_build.return_value = (
                "Fake daily summary",
                {"discord": {"credentials": {"bot_token": "fake_token"}}},
            )
            mock_deliver.return_value = (True, "", "msg_99")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        mock_deliver.assert_called_once()

    def test_claim_dedup_prevents_double_execution(self) -> None:
        task = ScheduledTask(
            id="test_dedup",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_build.return_value = ("Fake daily summary", {})
            mock_deliver.return_value = (True, "", "msg_1")

            # First execution succeeds
            result1 = execute_task(task, "2026-01-01T09:00")
            # Second execution with same fire_time is deduped
            result2 = execute_task(task, "2026-01-01T09:00")

        assert result1 is True
        assert result2 is False
        # Only called once due to dedup
        assert mock_deliver.call_count == 1

    def test_message_build_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_fail",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch("app.scheduler.executor.build_message") as mock_build:
            mock_build.side_effect = RuntimeError("Pipeline crashed")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False

    def test_delivery_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_del_fail",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch("app.scheduler.executor.build_message") as mock_build,
            patch("app.scheduler.executor._deliver_telegram") as mock_deliver,
        ):
            mock_build.return_value = ("Fake daily summary", {})
            mock_deliver.return_value = (False, "Connection refused", "")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is False

    def test_skipped_delivery_records_success(self) -> None:
        from app.scheduler.types import SkipDeliveryException, TaskStatus
        from app.scheduler.claim_store import get_runs

        task = ScheduledTask(
            id="test_skip_del",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
            params={"condition": "gửi khi có lỗi"},
        )

        with patch("app.scheduler.executor.build_message") as mock_build:
            mock_build.side_effect = SkipDeliveryException("Condition not met")
            result = execute_task(task, "2026-01-01T09:00")

        assert result is True
        runs = get_runs(task.id)
        assert len(runs) == 1
        assert runs[0].status == TaskStatus.SKIPPED
        assert "skipped:Condition not met" in runs[0].posted_message_id

    def test_telegram_delivery_uses_preformatted_html(self) -> None:
        from app.scheduler.executor import _deliver_telegram

        task = ScheduledTask(
            id="test_tg_pref",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )
        resolved_integrations = {"_telegram_message": "<b>Preformatted HTML Report</b>"}
        with (
            patch("app.scheduler.executor.resolve_telegram_credentials") as mock_creds,
            patch("app.utils.telegram_delivery.post_telegram_message") as mock_post,
        ):
            mock_creds.return_value = {"bot_token": "fake_token"}
            mock_post.return_value = (True, "", "msg_pref")

            ok, err, msg_id = _deliver_telegram(task, "Markdown message", resolved_integrations)

        assert ok is True
        assert msg_id == "msg_pref"
        mock_post.assert_called_once_with(
            "-100123", "<b>Preformatted HTML Report</b>", "fake_token", parse_mode="HTML"
        )

    def test_telegram_delivery_fallback_to_markdown_conversion(self) -> None:
        from app.scheduler.executor import _deliver_telegram

        task = ScheduledTask(
            id="test_tg_fallback",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )
        resolved_integrations = {}
        with (
            patch("app.scheduler.executor.resolve_telegram_credentials") as mock_creds,
            patch("app.utils.telegram_delivery.post_telegram_message") as mock_post,
        ):
            mock_creds.return_value = {"bot_token": "fake_token"}
            mock_post.return_value = (True, "", "msg_fallback")

            ok, err, msg_id = _deliver_telegram(task, "**Markdown bold**", resolved_integrations)

        assert ok is True
        assert msg_id == "msg_fallback"
        # Verify markdown is converted to HTML: **Markdown bold** -> <b>Markdown bold</b>
        mock_post.assert_called_once_with(
            "-100123", "<b>Markdown bold</b>", "fake_token", parse_mode="HTML"
        )

    def test_trello_delivery_severity_emoji(self) -> None:
        from app.scheduler.executor import _deliver_trello

        task = ScheduledTask(
            id="test_trello_sev",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            trello_board_id="fake_board",
            params={"pipeline_name": "my_pipeline"},
        )
        resolved_integrations = {
            "_severity": "critical",
            "trello": {
                "credentials": {
                    "api_key": "fake_api_key",
                    "token": "fake_token",
                }
            },
        }
        with (
            patch("app.integrations.trello.create_trello_card") as mock_create,
            patch("app.integrations.trello.get_trello_board_lists") as mock_lists,
            patch("app.integrations.trello.get_trello_board") as mock_board,
        ):
            mock_board.return_value = {"id": "fake_board_long"}
            mock_lists.return_value = [{"id": "fake_list", "name": "my_pipeline"}]
            mock_create.return_value = {
                "id": "card_123",
                "name": "🔴 [custom_investigation] my_pipeline",
            }

            ok, err = _deliver_trello(task, "Hello World", resolved_integrations)

        assert ok is True
        mock_create.assert_called_once()
        called_kwargs = mock_create.call_args[1]
        assert called_kwargs["name"] == "🔴 [custom_investigation] my_pipeline"
