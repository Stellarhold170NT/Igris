from __future__ import annotations
from unittest.mock import MagicMock, patch, ANY
from app.delivery.publish_findings.node import generate_report, _slack_to_markdown
from app.state import InvestigationState

def test_slack_to_markdown_link_conversion() -> None:
    text = "Check <https://grafana.net|Grafana> for logs or view <https://google.com>."
    converted = _slack_to_markdown(text)
    assert converted == "Check [Grafana](https://grafana.net) for logs or view [https://google.com](https://google.com)."

def test_generate_report_calls_create_trello_card_when_configured() -> None:
    state: InvestigationState = {
        "alert_name": "Database latency high",
        "root_cause_category": "database_failure",
        "severity": "critical",
        "resolved_integrations": {
            "trello": {
                "credentials": {
                    "api_key": "test-key",
                    "token": "test-token",
                    "list_id": "test-list"
                }
            }
        },
        "validity_score": 0.9,
        "validated_claims": [],
        "non_validated_claims": [],
        "investigation_recommendations": [],
        "remediation_steps": [],
        "available_sources": {},
        "evidence": {}
    }

    # Mock all other deliveries to avoid external calls or assertions failing
    with patch("app.utils.slack_delivery.send_slack_report", return_value=(True, None)), \
         patch("app.utils.slack_delivery.swap_reaction"), \
         patch("app.delivery.publish_findings.node.create_investigation_and_attach_url", return_value=("id", "url")), \
         patch("app.delivery.publish_findings.node.open_in_editor"), \
         patch("app.delivery.publish_findings.node.render_report"), \
         patch("app.integrations.trello.create_trello_card") as mock_create_card:
         
        mock_create_card.return_value = {"name": "test", "id": "123"}
        
        generate_report(state)
        
        mock_create_card.assert_called_once()
        args, kwargs = mock_create_card.call_args
        assert kwargs["name"] == "🔴 [database_failure] Database latency high"
        assert "Not determined" in kwargs["desc"]
        assert kwargs["list_id"] == "test-list"


def test_generate_report_resolves_list_via_board_id_and_pipeline_name() -> None:
    state: InvestigationState = {
        "alert_name": "API high error rate",
        "root_cause_category": "network_issue",
        "severity": "warning",
        "pipeline_name": "vauthz-sync-issue",
        "resolved_integrations": {
            "trello": {
                "credentials": {
                    "api_key": "test-key",
                    "token": "test-token",
                    "board_id": "test-board"
                }
            }
        },
        "validity_score": 0.9,
        "validated_claims": [],
        "non_validated_claims": [],
        "investigation_recommendations": [],
        "remediation_steps": [],
        "available_sources": {},
        "evidence": {}
    }

    # Mock all other deliveries to avoid external calls or assertions failing
    with patch("app.utils.slack_delivery.send_slack_report", return_value=(True, None)), \
         patch("app.utils.slack_delivery.swap_reaction"), \
         patch("app.delivery.publish_findings.node.create_investigation_and_attach_url", return_value=("id", "url")), \
         patch("app.delivery.publish_findings.node.open_in_editor"), \
         patch("app.delivery.publish_findings.node.render_report"), \
         patch("app.integrations.trello.get_trello_board_lists") as mock_get_lists, \
         patch("app.integrations.trello.create_trello_list") as mock_create_list, \
         patch("app.integrations.trello.create_trello_card") as mock_create_card:
         
        mock_get_lists.return_value = [{"id": "list-existing", "name": "existing-pipeline"}]
        mock_create_list.return_value = {"id": "list-new-created", "name": "vauthz-sync-issue"}
        mock_create_card.return_value = {"name": "test", "id": "123"}
        
        generate_report(state)
        
        # Should verify get_trello_board_lists is called with board_id
        mock_get_lists.assert_called_once_with(config=ANY, board_id="test-board")
        # Since 'vauthz-sync-issue' is not in existing lists, it should create the list
        mock_create_list.assert_called_once_with(config=ANY, board_id="test-board", name="vauthz-sync-issue")
        # Should create card inside the newly created list
        mock_create_card.assert_called_once()
        args, kwargs = mock_create_card.call_args
        assert kwargs["name"] == "⚠️ [network_issue] API high error rate"
        assert kwargs["list_id"] == "list-new-created"

