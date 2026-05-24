from __future__ import annotations
from unittest.mock import MagicMock, patch
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
