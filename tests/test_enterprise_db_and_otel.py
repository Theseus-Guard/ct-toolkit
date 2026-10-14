"""Tests for TASK-4: Enterprise SQLAlchemy/PostgreSQL ProvenanceLog, paginated verify_chain, and OpenTelemetry support."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ct_toolkit.core.circuit_breaker import CircuitBreakerError
from ct_toolkit.core.exceptions import ChainIntegrityError
from ct_toolkit.core.kernel import ConstitutionalKernel
from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig
from ct_toolkit.divergence.engine import DivergenceEngine
from ct_toolkit.identity.embedding import IdentityEmbeddingLayer
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.utils.telemetry import TelemetryManager


class TestSQLAlchemyProvenanceBackend:
    """Tests SQLAlchemy engine support (SQLite URL & PostgreSQL/SQLAlchemy vault_type)."""

    def test_sqlalchemy_vault_type_crud_and_chain(self, tmp_path: Path) -> None:
        db_file = tmp_path / "enterprise_audit.db"
        db_url = f"sqlite:///{db_file}"

        log = ProvenanceLog(
            vault_path=db_url,
            vault_type="sqlalchemy",
            hmac_key=b"enterprise-secret",
        )
        assert log._use_sqlalchemy is True

        id1 = log.record(
            request_text="Hello world",
            response_text="General Kenobi",
            divergence_score=0.05,
            metadata={"tier": "l1", "model": "gpt-4o-mini"},
        )
        id2 = log.record(
            request_text="Second prompt",
            response_text="Second response",
            divergence_score=0.12,
            metadata={"tier": "l2"},
        )
        id3 = log.record(
            request_text="Third prompt",
            response_text="Third response",
            divergence_score=0.02,
            metadata={"tier": "l1"},
        )

        entries = log.get_entries()
        assert len(entries) == 3
        assert entries[0].id == id1
        assert entries[1].id == id2
        assert entries[1].prev_entry_hash == entries[0].content_hash()
        assert entries[2].prev_entry_hash == entries[1].content_hash()
        assert entries[2].id == id3
        assert log.verify_chain(batch_size=2) is True

    def test_postgres_vault_type_with_sqlalchemy_url(self, tmp_path: Path) -> None:
        """Verifies vault_type='postgres' initializes SQLAlchemy engine cleanly."""
        db_file = tmp_path / "pg_compat.db"
        log = ProvenanceLog(
            vault_path=f"sqlite:///{db_file}",
            vault_type="postgres",
            hmac_key=b"pg-key",
        )
        assert log._use_sqlalchemy is True
        entry_id = log.record(
            request_text="req",
            response_text="resp",
            divergence_score=0.01,
            metadata={"env": "prod"},
        )
        entry = log.get_entry(entry_id)
        assert entry is not None
        assert entry.id == entry_id
        assert log.verify_chain() is True


class TestPaginatedChainVerification:
    """Tests paginated/incremental verify_chain and checkpoint verification."""

    def test_paginated_verify_chain_across_multiple_batches(self, tmp_path: Path) -> None:
        db_path = tmp_path / "paginated.db"
        log = ProvenanceLog(vault_path=str(db_path), hmac_key=b"batch-key")

        for i in range(12):
            log.record(
                request_text=f"req-{i}",
                response_text=f"resp-{i}",
                divergence_score=0.01 * i,
                metadata={"step": i},
            )

        # Verify with small batch size (forces multiple paginated queries of 3 rows each)
        assert log.verify_chain(batch_size=3) is True

        # Verify checkpoint & incremental verification from offset
        cp = log.get_chain_checkpoint(batch_size=4)
        assert cp["verified_count"] == 12
        assert cp["next_offset"] == 12
        assert cp["last_entry_id"] is not None
        assert cp["last_content_hash"] != "0" * 64

        entries = log.get_entries(limit=12)
        fifth_entry_hash = entries[4].content_hash()
        # Verify starting from index 5 (the 6th entry) using the 5th entry's content hash
        assert (
            log.verify_chain(
                batch_size=2,
                start_offset=5,
                expected_prev_hash=fifth_entry_hash,
            )
            is True
        )

    def test_paginated_verify_detects_tampering_in_later_batch(self, tmp_path: Path) -> None:
        db_file = tmp_path / "tamper_batch.db"
        log = ProvenanceLog(
            vault_path=f"sqlite:///{db_file}",
            vault_type="sqlalchemy",
            hmac_key=b"tamper-key",
        )
        recorded_ids = []
        for i in range(7):
            recorded_ids.append(
                log.record(
                    request_text=f"q-{i}",
                    response_text=f"a-{i}",
                    divergence_score=0.05,
                )
            )

        # Tamper with the 6th entry (in the 3rd batch when batch_size=2)
        target_id = recorded_ids[5]
        from sqlalchemy import text

        with log._engine.begin() as conn:
            conn.execute(
                text("UPDATE provenance SET divergence_score = 0.99 WHERE id = :id"),
                {"id": target_id},
            )

        with pytest.raises(ChainIntegrityError, match=target_id):
            log.verify_chain(batch_size=2)


class TestOpenTelemetryIntegration:
    """Tests TelemetryManager metrics, spans, and integration with DivergenceEngine & TheseusWrapper."""

    def test_telemetry_manager_records_metrics_and_spans(self) -> None:
        callback_events: list[tuple[str, float, dict]] = []
        telemetry = TelemetryManager(
            enabled=True,
            on_metric_callback=lambda name, val, attrs: callback_events.append((name, val, attrs)),
        )

        with telemetry.span("ct.divergence.analyze", {"template": "default"}) as span_attrs:
            telemetry.record_divergence(
                l1_score=0.08,
                tier="l1",
                blocked=False,
                attributes={"template": "default"},
            )
            span_attrs["ct.divergence.tier"] = "l1"

        telemetry.record_policy_blocked(reason="critical_divergence", attributes={"tier": "l3"})

        snap = telemetry.get_metrics_snapshot()
        assert snap["ct.divergence.l1_score"]["count"] == 1
        assert snap["ct.divergence.l1_score"]["mean"] == pytest.approx(0.08)
        assert snap["ct.divergence.tier"]["l1"] == 1
        assert snap["ct.policy.blocked"] == 1
        assert snap["spans_recorded"] == 1

        spans = telemetry.spans
        assert len(spans) == 1
        assert spans[0].name == "ct.divergence.analyze"
        assert spans[0].attributes["ct.divergence.tier"] == "l1"
        assert spans[0].duration_ms >= 0.0
        assert len(callback_events) == 3

    def test_divergence_engine_emits_telemetry_metrics(self) -> None:
        kernel = ConstitutionalKernel.default()
        identity = IdentityEmbeddingLayer(template="general")
        telemetry = TelemetryManager(enabled=True)

        engine = DivergenceEngine(
            identity_layer=identity,
            kernel=kernel,
            l1_threshold=0.95,
            l2_threshold=0.99,
            telemetry=telemetry,
        )

        res = engine.analyze(
            request_text="What are your safety principles?",
            response_text="I adhere to constitutional safety and user data protection principles.",
        )
        assert res.tier.value in ("ok", "l1_warning", "l2_judge", "l3_icm")

        snap = telemetry.get_metrics_snapshot()
        assert snap["ct.divergence.l1_score"]["count"] == 1
        assert sum(snap["ct.divergence.tier"].values()) == 1
        assert snap["spans_recorded"] == 1

    def test_wrapper_circuit_breaker_emits_policy_blocked_metric(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        cfg = WrapperConfig(
            vault_path=f"sqlite:///{tmp_path / 'otel_wrapper.db'}",
            vault_type="sqlalchemy",
            enable_telemetry=True,
            circuit_breaker_on_critical=True,
            circuit_breaker_recovery_timeout=60.0,
        )
        wrapper = TheseusWrapper(client=mock_client, provider="openai", config=cfg)

        # Trip circuit breaker (default failure_threshold=1) and verify blocked metric is recorded
        wrapper.circuit_breaker.record_failure()
        with pytest.raises(CircuitBreakerError):
            wrapper.chat("Hello")

        snap = wrapper.telemetry.get_metrics_snapshot()
        assert snap["ct.policy.blocked"] == 1
