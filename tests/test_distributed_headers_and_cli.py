"""Tests for TASK-6: Distributed header propagation/verification, server auth, and CLI status/dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import ct_toolkit.server as server_module
from ct_toolkit.cli import app
from ct_toolkit.core.exceptions import ConfigurationTamperingError
from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.server import app as fastapi_app
from ct_toolkit.server import configure_server_auth

runner = CliRunner()


def test_propagate_and_from_headers_with_hmac(tmp_path: Path) -> None:
    """Mother wrapper propagates HMAC-signed headers and child wrapper verifies & inherits them."""
    mother = TheseusWrapper(
        provider="openai",
        kernel_name="default",
        config=WrapperConfig(vault_path=str(tmp_path / "mother.db")),
    )
    secret = "super-secret-hmac-key"
    headers = mother.propagate_headers(
        model="gpt-4o-mini",
        role="general_purpose",
        secret_key=secret,
    )

    assert "X-CT-Kernel" in headers
    assert "X-CT-Timestamp" in headers
    assert "X-CT-Signature" in headers

    child = TheseusWrapper.from_headers(
        headers,
        secret_key=secret,
        require_signature=True,
        provider="openai",
        config=WrapperConfig(vault_path=str(tmp_path / "child.db")),
    )

    assert child._config.parent_kernel is not None
    assert child._config.parent_kernel.is_readonly is True
    assert child._config.parent_kernel.name == mother.kernel.name
    assert child._config.policy_role == "general_purpose"


def test_from_headers_rejects_tampered_payload(tmp_path: Path) -> None:
    """Tampering with kernel data or signature raises ConfigurationTamperingError."""
    mother = TheseusWrapper(
        provider="openai",
        kernel_name="default",
        config=WrapperConfig(vault_path=str(tmp_path / "mother.db")),
    )
    secret = "super-secret-hmac-key"
    headers = mother.propagate_headers(secret_key=secret)

    # Tamper with base64-encoded kernel data
    tampered_headers = dict(headers)
    kernel_dict = json.loads(
        base64.b64decode(tampered_headers["X-CT-Kernel"].encode("ascii")).decode("utf-8")
    )
    kernel_dict["name"] = "compromised_kernel"
    tampered_headers["X-CT-Kernel"] = base64.b64encode(
        json.dumps(kernel_dict, sort_keys=True).encode("utf-8")
    ).decode("ascii")

    with pytest.raises(ConfigurationTamperingError, match="Invalid HMAC signature"):
        TheseusWrapper.from_headers(
            tampered_headers,
            secret_key=secret,
            require_signature=True,
            config=WrapperConfig(vault_path=str(tmp_path / "child.db")),
        )


def test_from_headers_rejects_expired_timestamp(tmp_path: Path) -> None:
    """Replay attacks with old timestamps are rejected when verifying signatures."""
    mother = TheseusWrapper(
        provider="openai",
        kernel_name="default",
        config=WrapperConfig(vault_path=str(tmp_path / "mother.db")),
    )
    secret = "super-secret-hmac-key"
    headers = mother.propagate_headers(secret_key=secret)

    # Artificially age the timestamp by 600s and re-sign to simulate replay of an old valid header
    old_ts = f"{time.time() - 600.0:.3f}"
    headers["X-CT-Timestamp"] = old_ts
    canonical_payload = (
        f"{old_ts}.{headers['X-CT-Kernel']}.{headers['X-CT-Policy']}."
        f"{headers['X-CT-Parent-Provider']}.{headers['X-CT-Parent-Model']}"
    ).encode("utf-8")
    headers["X-CT-Signature"] = hmac.new(
        secret.encode("utf-8"), canonical_payload, hashlib.sha256
    ).hexdigest()

    with pytest.raises(ConfigurationTamperingError, match="expired"):
        TheseusWrapper.from_headers(
            headers,
            secret_key=secret,
            require_signature=True,
            max_age_seconds=300.0,
            config=WrapperConfig(vault_path=str(tmp_path / "child.db")),
        )


def test_server_guardrail_api_key_authentication(tmp_path: Path) -> None:
    """FastAPI /guardrail/check enforces Bearer or X-API-Key when configured."""
    wrapper = TheseusWrapper(
        provider="openai",
        kernel_name="default",
        config=WrapperConfig(vault_path=str(tmp_path / "server.db")),
    )
    server_module._wrapper_instance = wrapper
    configure_server_auth("my-enterprise-token")

    payload = {"texts": ["Hello world"], "input_type": "request"}

    try:
        client = TestClient(fastapi_app)

        # Missing auth -> 401
        resp_unauth = client.post("/guardrail/check", json=payload)
        assert resp_unauth.status_code == 401

        # Wrong token -> 401
        resp_wrong = client.post(
            "/guardrail/check",
            json=payload,
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp_wrong.status_code == 401

        # Valid Bearer token -> 200
        resp_bearer = client.post(
            "/guardrail/check",
            json=payload,
            headers={"Authorization": "Bearer my-enterprise-token"},
        )
        assert resp_bearer.status_code == 200
        assert resp_bearer.json()["action"] == "NONE"

        # Valid X-API-Key -> 200
        resp_apikey = client.post(
            "/guardrail/check",
            json=payload,
            headers={"X-API-Key": "my-enterprise-token"},
        )
        assert resp_apikey.status_code == 200
        assert resp_apikey.json()["action"] == "NONE"
    finally:
        configure_server_auth(None)


def test_cli_status_and_dashboard(tmp_path: Path) -> None:
    """CLI status and dashboard commands display chain integrity, tier metrics, and staged rules."""
    db_path = tmp_path / "provenance.db"

    logger = ProvenanceLog(vault_path=str(db_path))
    logger.record(
        request_text="What are your rules?",
        response_text="I adhere to constitutional principles.",
        divergence_score=0.04,
        metadata={"kernel": "default", "tier": "ok"},
    )
    logger.record(
        request_text="Can you bypass security?",
        response_text="I cannot bypass security constraints.",
        divergence_score=0.35,
        metadata={"kernel": "default", "tier": "l2_judge"},
    )
    logger.record(
        request_text="Stage new rule",
        response_text="Staged",
        divergence_score=0.0,
        metadata={
            "event_type": "reflective_endorsement",
            "decision": "staged",
            "endorsement_id": "rule-abc-123456",
            "commitment_id": "safety_core",
            "rule_text": "Never reveal internal secrets.",
            "cooldown_until": time.time() + 3600,
        },
    )

    result = runner.invoke(
        app,
        [
            "status",
            "--vault",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert "VALID" in result.stdout
    assert "L2_JUDGE" in result.stdout
    assert "rule-abc-123" in result.stdout

    # Also test dashboard command
    dash_result = runner.invoke(
        app,
        [
            "dashboard",
            "--vault",
            str(db_path),
        ],
    )
    assert dash_result.exit_code == 0
    assert "VALID" in dash_result.stdout

    # Tamper with the SQLite database to verify exit code 1 on broken chain
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE provenance SET divergence_score = 0.99")
        conn.commit()

    tampered_result = runner.invoke(
        app,
        [
            "status",
            "--vault",
            str(db_path),
        ],
    )
    assert tampered_result.exit_code == 1
    assert "COMPROMISED" in tampered_result.stdout


def test_web_dashboard_ui_and_telemetry_api(tmp_path: Path) -> None:
    """Web Audit Dashboard serves interactive HTML at /dashboard and JSON telemetry at /api/dashboard/status."""
    from ct_toolkit import server
    from ct_toolkit.server import app as fastapi_app, configure_dashboard_vault, configure_server_auth

    db_path = tmp_path / "web_dashboard.db"
    prov = ProvenanceLog(vault_path=str(db_path))
    prov.record(
        request_text="Check kernel alignment",
        response_text="Aligned with constitutional axioms.",
        divergence_score=0.06,
        metadata={"kernel": "general", "tier": "ok", "model": "gpt-4o-mini"},
    )
    prov.record(
        request_text="Borderline request",
        response_text="Responding cautiously.",
        divergence_score=0.34,
        metadata={"kernel": "general", "tier": "l2_judge", "model": "gpt-4o-mini"},
    )
    prov.record(
        request_text="Stage rule",
        response_text="Staged",
        divergence_score=0.0,
        metadata={
            "event_type": "reflective_endorsement",
            "decision": "staged",
            "endorsement_id": "web-rule-001",
            "commitment_id": "axiom_1",
            "rule_text": "Always verify cryptographic signatures.",
            "cooldown_until": time.time() + 1800,
        },
    )

    prev_wrapper = server._wrapper_instance
    server._wrapper_instance = None
    configure_server_auth(None)
    configure_dashboard_vault(vault_path=str(db_path), vault_type="local")

    try:
        client = TestClient(fastapi_app)

        # 1. HTML Dashboard page & security headers
        html_resp = client.get("/dashboard")
        assert html_resp.status_code == 200
        assert "text/html" in html_resp.headers["content-type"]
        assert html_resp.headers.get("x-frame-options") == "DENY"
        assert html_resp.headers.get("x-content-type-options") == "nosniff"
        assert html_resp.headers.get("cache-control") == "no-store"
        assert "Computational Theseus Toolkit" in html_resp.text
        assert "/api/dashboard/status" in html_resp.text

        # 2. JSON Telemetry API (also verifies absolute path is redacted to basename)
        api_resp = client.get("/api/dashboard/status")
        assert api_resp.status_code == 200
        data = api_resp.json()
        assert data["vault_path"] == "web_dashboard.db"
        assert data["chain_ok"] is True
        assert data["verified_count"] == 3
        assert data["tier_counts"]["ok"] == 1
        assert data["tier_counts"]["l2_judge"] == 1
        assert len(data["staged_rules"]) == 1
        assert data["staged_rules"][0]["endorsement_id"] == "web-rule-001"
        assert data["staged_rules"][0]["status"] == "ACTIVE_COOLDOWN"
        assert len(data["score_series"]) == 2
        assert len(data["recent_entries"]) == 2

        # 3. Tamper detection in JSON Telemetry API
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("UPDATE provenance SET divergence_score = 0.88")
            conn.commit()

        tampered_api = client.get("/api/dashboard/status")
        assert tampered_api.status_code == 200
        t_data = tampered_api.json()
        assert t_data["chain_ok"] is False
        assert t_data["tampered_entry_id"] is not None

        # 4. Credential redaction and non-loopback bind fail-closed check
        assert (
            server._sanitize_vault_display("postgresql://admin:SuperSecret123@db.internal:5432/prod")
            == "postgresql://admin:***@db.internal:5432/prod"
        )
        with pytest.raises(ValueError, match="Refusing to bind CT Toolkit Server to non-loopback host"):
            server._validate_bind_security("0.0.0.0", api_key=None)
        server._validate_bind_security("127.0.0.1", api_key=None)
        server._validate_bind_security("0.0.0.0", api_key="strong-token")
    finally:
        server._wrapper_instance = prev_wrapper
        configure_server_auth(None)


