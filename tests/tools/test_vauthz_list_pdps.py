"""Tests for VauthzPDPListTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch
from app.tools.VauthzPDPListTool import vauthz_list_pdps
from tests.tools.conftest import BaseToolContract


class TestVauthzPDPListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return vauthz_list_pdps.__opensre_registered_tool__


def test_metadata() -> None:
    rt = vauthz_list_pdps.__opensre_registered_tool__
    assert rt.name == "vauthz_list_pdps"
    assert rt.source == "vauthz"


def test_run_happy_path() -> None:
    fake_result = {
        "pdps": [
            {
                "pdp_id": "pdp-1",
                "env_id": "env-1",
                "project_name": "Project A",
                "online": True,
                "desc": "Hoạt động bình thường",
                "number_count": 1,
            }
        ]
    }
    with patch("app.tools.VauthzPDPListTool.list_pdps", return_value=fake_result):
        result = vauthz_list_pdps(pdp_syncheck_url="117.5.151.111:4000/api/fleet")
    assert "pdps" in result
    assert result["pdps"][0]["pdp_id"] == "pdp-1"
    assert result["pdps"][0]["online"] is True
