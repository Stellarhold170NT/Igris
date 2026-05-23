"""Tests for VauthzPDPCompareTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch
from app.tools.VauthzPDPCompareTool import vauthz_compare_pdp_data
from tests.tools.conftest import BaseToolContract


class TestVauthzPDPCompareToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return vauthz_compare_pdp_data.__opensre_registered_tool__


def test_metadata() -> None:
    rt = vauthz_compare_pdp_data.__opensre_registered_tool__
    assert rt.name == "vauthz_compare_pdp_data"
    assert rt.source == "vauthz"


def test_run_happy_path() -> None:
    fake_result = {
        "pdp_id": "pdp-1",
        "env_id": "env-1",
        "success": True,
        "is_identical": True,
        "diff": {},
        "events": {},
    }
    with patch("app.tools.VauthzPDPCompareTool.compare_pdp_data", return_value=fake_result):
        result = vauthz_compare_pdp_data(
            pdp_id="pdp-1",
            env_id="env-1",
            pdp_gateway_url="117.5.151.111:7766",
            vauthz_url="117.5.151.111:8081",
        )
    assert result["success"] is True
    assert result["is_identical"] is True
    assert result["pdp_id"] == "pdp-1"
