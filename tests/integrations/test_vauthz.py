"""Tests for Vauthz integration helpers."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.integrations.vauthz import (
    ensure_scheme,
    extract_pdp_name,
    get_pdp_fleet_url,
    get_pdp_data_url,
    get_db_data_url,
    vauthz_is_available,
    vauthz_extract_params,
    list_pdps,
    get_pdp_events,
    extract_and_group_events,
    extract_auth_token,
    remove_null_fields,
    jsonify_diff,
    compare_pdp_data,
)


def test_ensure_scheme() -> None:
    assert ensure_scheme("117.5.151.111:4000") == "http://117.5.151.111:4000"
    assert ensure_scheme("http://117.5.151.111:4000") == "http://117.5.151.111:4000"
    assert ensure_scheme("https://117.5.151.111:4000") == "https://117.5.151.111:4000"
    assert ensure_scheme("") == ""


def test_get_pdp_fleet_url() -> None:
    assert (
        get_pdp_fleet_url("117.5.151.111:4000/api/fleet") == "http://117.5.151.111:4000/api/fleet"
    )
    assert get_pdp_fleet_url("http://117.5.151.111:4000") == "http://117.5.151.111:4000/api/fleet"


def test_get_pdp_data_url() -> None:
    assert get_pdp_data_url("http://117.5.151.111:7766") == "http://117.5.151.111:7766/v1/data"


def test_get_db_data_url() -> None:
    assert (
        get_db_data_url("http://117.5.151.111:8081", "env-123")
        == "http://117.5.151.111:8081/opa_data/env-123"
    )


def test_vauthz_is_available() -> None:
    assert vauthz_is_available({}) is True


def test_vauthz_extract_params_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        params = vauthz_extract_params({})
        assert params["pdp_syncheck_url"] == "http://117.5.151.111:4000/api/fleet"
        assert params["pdp_gateway_url"] == "http://117.5.151.111:7766"
        assert params["vauthz_url"] == "http://117.5.151.111:8081"


def test_vauthz_extract_params_with_sources() -> None:
    sources = {
        "vauthz": {
            "pdp_syncheck_url": "http://my-syncheck:4000/api/fleet",
            "pdp_gateway_url": "http://my-gateway:7766",
            "vauthz_url": "http://my-vauthz:8081",
        }
    }
    with patch.dict(os.environ, {}, clear=True):
        params = vauthz_extract_params(sources)
        assert params["pdp_syncheck_url"] == "http://my-syncheck:4000/api/fleet"
        assert params["pdp_gateway_url"] == "http://my-gateway:7766"
        assert params["vauthz_url"] == "http://my-vauthz:8081"


def test_vauthz_extract_params_env_priority() -> None:
    sources = {
        "vauthz": {
            "pdp_syncheck_url": "http://my-syncheck:4000/api/fleet",
            "pdp_gateway_url": "http://my-gateway:7766",
            "vauthz_url": "http://my-vauthz:8081",
        }
    }
    env = {
        "PDP_SYNCHECK_URL": "http://env-syncheck:4000/api/fleet",
        "PDP_GATEWAY_URL": "http://env-gateway:7766",
        "VAUTHZ_URL": "http://env-vauthz:8081",
    }
    with patch.dict(os.environ, env, clear=True):
        params = vauthz_extract_params(sources)
        assert params["pdp_syncheck_url"] == "http://env-syncheck:4000/api/fleet"
        assert params["pdp_gateway_url"] == "http://env-gateway:7766"
        assert params["vauthz_url"] == "http://env-vauthz:8081"


def test_list_pdps_filtering_and_counts() -> None:
    # 2 PDP instances under env-1 (1 contains "pdp" version, 1 doesn't - local)
    # 2 PDP instances under env-2 (both contain "pdp" version)
    raw_response = {
        "pdps": [
            {
                "id": "pdp-k8s-1",
                "env_id": "env-1",
                "project_name": "Project K8s A",
                "pdp_version": "military-youth-pdp-9976fdc6b-5ckvt",
                "is_healthy": True,
            },
            {
                "id": "pdp-local-1",
                "env_id": "env-1",
                "project_name": "Project Local A",
                "pdp_version": "6bcba78bb41c",
                "is_healthy": False,
            },
            {
                "id": "pdp-k8s-2",
                "env_id": "env-2",
                "project_name": "Project K8s B",
                "pdp_version": "pdp-v1.0",
                "is_healthy": False,
            },
            {
                "id": "pdp-k8s-3",
                "env_id": "env-2",
                "project_name": "Project K8s B",
                "pdp_version": "pdp-v1.0",
                "is_healthy": True,
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = raw_response
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_resp):
        res = list_pdps("117.5.151.111:4000/api/fleet")

    pdps = res["pdps"]
    # pdp-local-1 must be filtered out
    assert len(pdps) == 3

    # Check fields are mapped correctly
    p1 = [p for p in pdps if p["pdp_id"] == "pdp-k8s-1"][0]
    assert p1["online"] is True
    assert p1["desc"] == "Hoạt động bình thường"
    assert p1["env_id"] == "env-1"
    assert p1["pdp_version"] == "military-youth-pdp-9976fdc6b-5ckvt"
    # Counts of remaining PDPs: env-1 has 1 ("pdp-k8s-1"), env-2 has 2 ("pdp-k8s-2", "pdp-k8s-3")
    assert p1["number_count"] == 1

    p2 = [p for p in pdps if p["pdp_id"] == "pdp-k8s-2"][0]
    assert p2["online"] is False
    assert p2["desc"] == "PDP đã bị chết trên k8s server"
    assert p2["number_count"] == 2


def test_extract_and_group_events() -> None:
    events = [
        {
            "event_type": "OpalCallback",
            "details": {
                "verify_results": [{"dst_path": "/role_permissions/thong_tin_doan_vien/nguoi_xem"}]
            },
            "callback_info": {
                "delta_snapshots": [{"dst_path": "/role_permissions/thong_tin_doan_vien/admin"}],
                "raw_payload": {
                    "reports": [{"entry": {"dst_path": "/resource_types/thong_tin_doan_vien"}}]
                },
            },
        },
        {
            "event_type": "OtherType",
            "details": {"verify_results": [{"dst_path": "/ignored_prefix/path"}]},
        },
    ]
    grouped = extract_and_group_events(events)
    assert "role_permissions" in grouped
    assert "resource_types" in grouped
    assert "ignored_prefix" not in grouped

    assert "/role_permissions/thong_tin_doan_vien/nguoi_xem" in grouped["role_permissions"]
    assert "/role_permissions/thong_tin_doan_vien/admin" in grouped["role_permissions"]
    assert "/resource_types/thong_tin_doan_vien" in grouped["resource_types"]


def test_extract_auth_token() -> None:
    events = [
        {
            "event_type": "OpalCallback",
            "callback_info": {
                "raw_payload": {
                    "reports": [
                        {
                            "entry": {
                                "config": {"headers": {"Authorization": "Bearer vauthz_token_123"}}
                            }
                        }
                    ]
                }
            },
        }
    ]
    token = extract_auth_token(events)
    assert token == "Bearer vauthz_token_123"


def test_remove_null_fields() -> None:
    data = {
        "keep_str": "val",
        "remove_null": None,
        "nested": {
            "keep_int": 42,
            "remove_null_nested": None,
        },
        "list": ["val", None, {"nested_in_list": None, "keep_bool": True}],
    }
    cleaned = remove_null_fields(data)
    assert cleaned == {
        "keep_str": "val",
        "nested": {
            "keep_int": 42,
        },
        "list": ["val", {"keep_bool": True}],
    }


def test_extract_pdp_name() -> None:
    assert extract_pdp_name("military-youth-pdp-9976fdc6b-5ckvt") == "military-youth"
    assert extract_pdp_name("cdtq-pdp-abc123") == "cdtq"
    assert extract_pdp_name("simple") == "simple"


def test_jsonify_diff() -> None:
    import jsondiff

    diff = jsondiff.diff({"a": 1}, {"b": 2}, syntax="symmetric")
    jsonified = jsonify_diff(diff)
    # The keys should be strings like '$insert' and '$delete'
    assert "$insert" in jsonified
    assert "$delete" in jsonified
    assert isinstance(jsonified["$insert"], dict)


def test_compare_pdp_data_flow() -> None:
    # 1. Mock events response (dict with pdp_id + events list)
    events_mock = {
        "pdp_id": "pdp-123",
        "events": [
            {
                "event_type": "OpalCallback",
                "callback_info": {
                    "topics": ["policy_data:env-uuid-xyz"],
                    "raw_payload": {
                        "reports": [
                            {
                                "entry": {
                                    "dst_path": "/role_permissions/abc",
                                    "config": {"headers": {"Authorization": "Bearer tok-1"}},
                                }
                            }
                        ]
                    },
                },
            }
        ],
    }

    events_resp = MagicMock()
    events_resp.json.return_value = events_mock
    events_resp.raise_for_status = MagicMock()

    # 2. Mock DB Data (with nulls to be cleaned, and differ from OPA data)
    db_data_raw = {"rules": {"allow": True}, "redundant": None}
    db_resp = MagicMock()
    db_resp.json.return_value = db_data_raw
    db_resp.raise_for_status = MagicMock()

    # 3. Mock fleet response (for X-pdp-name lookup)
    fleet_mock = {
        "pdps": [
            {
                "id": "pdp-123",
                "env_id": "env-uuid-xyz",
                "project_name": "Test",
                "pdp_version": "test-pdp-abc",
                "is_healthy": True,
            }
        ]
    }
    fleet_resp = MagicMock()
    fleet_resp.json.return_value = fleet_mock
    fleet_resp.raise_for_status = MagicMock()

    # 4. Mock OPA data (differs from DB rules allow)
    opa_data_raw = {"rules": {"allow": False}}
    opa_resp = MagicMock()
    opa_resp.json.return_value = opa_data_raw
    opa_resp.raise_for_status = MagicMock()

    # httpx.Client.get calls:
    # 1. fetch events -> events_resp
    # 2. fetch DB data -> db_resp
    # 3. fetch fleet (for X-pdp-name) -> fleet_resp
    # 4. fetch OPA data -> opa_resp
    with patch(
        "httpx.Client.get", side_effect=[events_resp, db_resp, fleet_resp, opa_resp]
    ) as mock_get:
        res = compare_pdp_data(
            pdp_gateway_url="http://gateway",
            vauthz_url="http://vauthz",
            pdp_id="pdp-123",
            pdp_syncheck_url="http://syncheck",
        )

    assert res["success"] is True
    assert res["env_id"] == "env-uuid-xyz"
    assert "role_permissions" in res["events"]
    assert res["is_identical"] is False
    assert isinstance(res["diff"], dict)
    # The diff should contain string key for jsondiff.Symbol
    assert "rules" in res["diff"]

    # Check that OPA get request used Authorization and X-pdp-name headers
    opa_call = mock_get.call_args_list[3]
    headers = opa_call[1].get("headers", {})
    assert headers.get("Authorization") == "Bearer tok-1"
    assert headers.get("X-pdp-name") == "test"


def test_get_pdp_events_url_stripping() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        get_pdp_events("http://117.5.151.111:4000/api/fleet", "pdp-123")
        mock_get.assert_called_once_with("http://117.5.151.111:4000/api/pdp/pdp-123/events")
